#!/usr/bin/env python3
"""Augment timeline.json with cct sessions that have no classified prose yet.

The prose-based pipeline (extract_prose -> classify_batch -> make_timeline) only
sees agents that produce transcripts the extractor understands. Opencode is
transcript-less, so sandboxed opencode agents never appear on /timeline today.

This script makes any live/dormant cct session a first-class citizen on the
timeline by synthesizing a minimal agent entry from `cct top --json` for every
session not already present. It is idempotent and additive — never removes or
mutates existing (prose-sourced) agents.

Run after build_fleet_index.py / make_timeline.py in the refresh cron.
"""
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMELINE_PATHS = [
    ROOT / "data" / "timeline.json",
    ROOT / "ui" / "public" / "data" / "timeline.json",
]
ARTIFACT_PATHS = [
    ROOT / "data" / "artifacts.json",
    ROOT / "ui" / "public" / "data" / "artifacts.json",
]
HIERARCHY_PATHS = [
    ROOT / "data" / "timeline_hierarchy.json",
    ROOT / "ui" / "public" / "data" / "timeline_hierarchy.json",
]

# Fleet-pattern → org mapping. Idempotent on repeat runs; mapping key is agent-id.
FLEET_ORG_RULES = [
    (re.compile(r"^other-project"), "nemotron-demo", "Nemotron Demo"),
]

# File extensions that are worth surfacing as artifacts in the dash.
ARTIFACT_EXTS = {
    ".html": "code", ".htm": "code", ".js": "code", ".ts": "code", ".tsx": "code",
    ".py": "code", ".rs": "code", ".go": "code", ".c": "code", ".cpp": "code", ".h": "code",
    ".md": "doc", ".txt": "doc", ".yaml": "config", ".yml": "config", ".json": "config", ".toml": "config",
    # kind="media" (not "image"/"video") so the big MEDIA tile grid in the
    # artifact widget renders them as thumbnails. The widget filters on
    # kind==="media" for the tile section; "image" rows only show in FILES list.
    ".png": "media", ".jpg": "media", ".jpeg": "media", ".gif": "media", ".webp": "media", ".svg": "media",
    ".mp4": "media", ".webm": "media", ".mov": "media",
    ".jsonl": "data", ".csv": "data",
}

AGE_RE = re.compile(r"^(\d+)([smhdw])")
AGE_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}

STATE_MAP = {
    "cooking": "working",
    "working": "working",
    "idle": "idle",
    "input_busy": "working",
    # NOTE: timeline COLORS has no "done" entry → it falls back to grey, hiding
    # dormant lifetimes. Map dormant to "working" so each session's lifespan
    # shows up as a colored band instead of a single grey stripe.
    "dormant": "working",
    "done": "working",
    "blocked": "blocked",
    "waiting": "waiting",
    "live": "working",
}

ATTEMPTS_MIRROR = ROOT / "ui" / "public" / "attempts"


def parse_age_seconds(age: str) -> int:
    if not age:
        return 0
    m = AGE_RE.match(age.strip())
    if not m:
        return 0
    return int(m.group(1)) * AGE_UNIT.get(m.group(2), 60)


def stable_id(name: str) -> str:
    """Deterministic UUID-shaped id from the cct name, so re-runs don't duplicate."""
    h = hashlib.sha1(f"cct-session::{name}".encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def read_cct_top():
    try:
        out = subprocess.check_output(["cct", "top", "--json"], text=True, timeout=60)
        return json.loads(out)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[warn] cct top failed: {e}", file=sys.stderr)
        return []


def _empty_seg_fields():
    return {
        "blocker_type": None,
        "turn_count": 1,
        "cost_usd": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "roles": {},
        "intents": {},
        "user_intents": {},
        "blockers": {},
        "urgencies": {},
        "sample_text": "",
        "latest_text": "",
        "file_edits_count": 0,
        "errors_count": 0,
    }


def make_segments(start_ts: float, end_ts: float, state: str):
    """Emit segments at ~5-min granularity across the span so every timeline
    bucket (bucket size is zoom-adaptive, often 5-30 min) gets a colored cell.
    A single long segment only lands in one bucket, leaving the rest grey.
    """
    span = max(60.0, end_ts - start_ts)
    SEG_WIDTH = 300.0  # 5 min
    MAX_SEGS = 288     # cap at 24h of 5-min cells
    n = max(1, min(int(span / SEG_WIDTH) + 1, MAX_SEGS))
    w = span / n
    segs = []
    for i in range(n):
        s = start_ts + i * w
        e = s + w
        seg = {"start_ts": s, "end_ts": e, "duration_sec": w, "state": state}
        seg.update(_empty_seg_fields())
        segs.append(seg)
    return segs


def _file_mtime_range(cwd: str):
    """Return (min_mt, max_mt) across interesting files in cwd, or (0,0)."""
    if not cwd:
        return (0.0, 0.0)
    p = Path(cwd)
    if not p.is_dir():
        return (0.0, 0.0)
    lo = hi = 0.0
    try:
        for child in p.iterdir():
            if not child.is_file() or child.name.startswith("."):
                continue
            if child.suffix.lower() not in ARTIFACT_EXTS:
                continue
            try:
                mt = child.stat().st_mtime
            except OSError:
                continue
            if lo == 0.0 or mt < lo:
                lo = mt
            if mt > hi:
                hi = mt
    except OSError:
        pass
    return (lo, hi)


def synthesize_agent(sess: dict) -> dict:
    name = sess["name"]
    fleet = (sess.get("fleet") or "").strip()
    engine = sess.get("engine") or ""
    cwd = sess.get("cwd") or ""
    state_raw = (sess.get("state") or sess.get("kind") or "").lower()
    state = STATE_MAP.get(state_raw, "idle")
    last_act = float(sess.get("last_activity_epoch") or time.time())
    now = time.time()

    # Anchor segment span to actual observable activity: the range of file
    # mtimes in the workdir. Falls back to a 1h band ending at last_activity
    # when no files are found. This ensures per-cell artifact filtering in
    # the aggregate widget picks up ALL files emitted during the agent's run.
    #
    # HARD CAP: cwd scan can pick up ancient files unrelated to this agent's
    # current session (e.g. a long-lived project dir with notes from months
    # ago). Clamp start_ts to no earlier than MAX_LOOKBACK_DAYS before the
    # last activity so a persistent agent in an old dir doesn't paint months
    # of fake "working" bands across the timeline.
    MAX_LOOKBACK_DAYS = 14
    MAX_LOOKBACK_SEC = MAX_LOOKBACK_DAYS * 86400
    mt_lo, mt_hi = _file_mtime_range(cwd)
    if mt_lo > 0:
        start_ts = mt_lo - 300
        end_ts = max(mt_hi, last_act) + 300
    else:
        end_ts = last_act if state_raw in ("dormant", "done") else now
        start_ts = max(0.0, end_ts - 3600)
    if end_ts - start_ts < 60:
        start_ts = max(0.0, end_ts - 3600)
    # Enforce the lookback cap relative to end_ts.
    earliest_allowed = end_ts - MAX_LOOKBACK_SEC
    if start_ts < earliest_allowed:
        start_ts = earliest_allowed

    project = ""
    if cwd:
        parts = Path(cwd).parts
        if "Agent" in parts:
            idx = parts.index("Agent")
            if idx + 1 < len(parts):
                project = parts[idx + 1]

    return {
        "id": stable_id(name),
        "title": name,
        "project": project,
        "fleet": fleet,
        "fleet_raw": fleet,
        "model": "",
        "models": [],
        "host": sess.get("host") or "",
        "account": sess.get("account") or "",
        "provider": engine,
        "total_turns": 1,
        "total_cost_usd": 0.0,
        "source": "cct-live",
        "segments": make_segments(start_ts, end_ts, state),
    }


def merge(timeline: dict, cct_sessions: list) -> tuple[int, int]:
    """Return (added, updated) counts. Does not remove any existing agents."""
    existing_by_title = {a.get("title"): a for a in timeline.get("agents", [])}
    existing_by_id = {a.get("id"): a for a in timeline.get("agents", []) if a.get("id")}
    added = updated = 0
    agents = timeline.setdefault("agents", [])

    for sess in cct_sessions:
        name = sess.get("name")
        if not name:
            continue
        sid = stable_id(name)
        existing = existing_by_title.get(name) or existing_by_id.get(sid)
        if existing is None:
            agents.append(synthesize_agent(sess))
            added += 1
        else:
            # Only touch entries we previously added ourselves (source=cct-live);
            # never mutate prose-sourced agents.
            if existing.get("source") != "cct-live":
                continue
            fresh = synthesize_agent(sess)
            for k in ("fleet", "fleet_raw", "project", "provider", "host", "account"):
                existing[k] = fresh[k]
            existing["segments"] = fresh["segments"]
            updated += 1
    return added, updated


def _web_url_for(path: Path, fleet: str) -> str | None:
    """Return a public URL under /attempts/<name> if a mirror exists.
    Tries the agent's fleet first, then the file's immediate parent dir name
    (handles legacy sandbox dirs like [REDACTED_PATH]
    whose files were written by a session whose fleet name doesn't match
    the on-disk directory)."""
    for key in (fleet, path.parent.name):
        if not key: continue
        mirror = ATTEMPTS_MIRROR / key / path.name
        if mirror.is_file():
            return f"/attempts/{key}/{path.name}"
    return None


def scan_workdir_artifacts(cwd: str, since_ts: float, fleet: str = ""):
    """Return list of {ts, tool, path, basename, kind, web_url?} for interesting files in a workdir."""
    out = []
    if not cwd:
        return out
    p = Path(cwd)
    if not p.is_dir():
        return out
    # Only descend one level deep to avoid walking into node_modules / .git / etc.
    try:
        for child in sorted(p.iterdir()):
            if not child.is_file():
                continue
            if child.name.startswith("."):
                continue
            ext = child.suffix.lower()
            kind = ARTIFACT_EXTS.get(ext)
            if not kind:
                continue
            try:
                mt = child.stat().st_mtime
            except OSError:
                continue
            if since_ts and mt < since_ts - 86400:  # discard files older than 1 day before session start
                continue
            # Keep natural kind — the widget renders file rows and opens a
            # modal viewer on click when web_url is set.
            web_url = _web_url_for(child, fleet)
            entry = {
                "ts": mt,
                "tool": "Write",
                "path": str(child),
                "basename": child.name,
                "kind": kind,
                "source": "cct-live",
            }
            if web_url:
                entry["web_url"] = web_url
            out.append(entry)
    except OSError:
        pass
    out.sort(key=lambda x: x["ts"])
    return out


def augment_artifacts(artifacts: dict, cct_sessions: list) -> tuple[int, int]:
    """For each cct session, scan its cwd and inject file-write entries keyed by stable_id."""
    added_agents = added_files = 0
    for sess in cct_sessions:
        name = sess.get("name")
        cwd = sess.get("cwd")
        if not name or not cwd:
            continue
        sid = stable_id(name)
        fleet = (sess.get("fleet") or "").strip()
        last_act = float(sess.get("last_activity_epoch") or time.time())
        age_s = parse_age_seconds(sess.get("age") or "")
        start_ts = max(0.0, time.time() - age_s) if age_s > 0 else (last_act - 3600)
        files = scan_workdir_artifacts(cwd, start_ts, fleet=fleet)
        if not files:
            continue
        existing = artifacts.get(sid, [])
        # Drop entries we previously synthesized (either tagged cct-live, or
        # untagged legacy entries whose path lives inside this session's cwd).
        # Keep everything else (prose-sourced artifacts from other paths).
        cwd_prefix = cwd.rstrip("/") + "/"
        kept = [
            e for e in existing
            if e.get("source") != "cct-live"
            and not (isinstance(e.get("path"), str) and e["path"].startswith(cwd_prefix))
        ]
        if sid not in artifacts:
            added_agents += 1
        merged = kept + files
        artifacts[sid] = sorted(merged, key=lambda x: x.get("ts", 0))
        added_files += len(files)
    return added_agents, added_files


def augment_hierarchy(hier: dict, cct_sessions: list) -> tuple[int, int]:
    """Add fleet-pattern-driven orgs + agent mappings. Idempotent."""
    orgs = hier.setdefault("orgs", [])
    atOrg = hier.setdefault("agentToOrg", {})
    atSrc = hier.setdefault("agentSources", {})
    existing_ids = {o.get("id") for o in orgs}
    added_orgs = added_maps = 0
    # Ensure each rule's org exists in the orgs list.
    for _, oid, olabel in FLEET_ORG_RULES:
        if oid not in existing_ids:
            orgs.append({"id": oid, "label": olabel})
            existing_ids.add(oid)
            added_orgs += 1
    # Map agents by fleet-pattern. Don't clobber existing mappings; respect prose roster.
    for sess in cct_sessions:
        name = sess.get("name")
        fleet = (sess.get("fleet") or "").strip()
        if not name or not fleet:
            continue
        for pattern, oid, _ in FLEET_ORG_RULES:
            if pattern.search(fleet):
                sid = stable_id(name)
                if sid in atOrg:
                    continue
                atOrg[sid] = oid
                atSrc[sid] = {"source": f"fleet-pattern:{pattern.pattern}"}
                added_maps += 1
                break
    hier["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return added_orgs, added_maps


def main():
    sessions = read_cct_top()
    if not sessions:
        print("[info] no cct sessions to merge, exiting", file=sys.stderr)
        return 0
    # Only merge live/dormant/managed; skip the giant unmanaged list.
    managed = [s for s in sessions if s.get("kind") in ("live", "dormant") or (s.get("state") not in ("unmanaged", None))]

    # Timeline augmentation
    for path in TIMELINE_PATHS:
        if not path.exists():
            print(f"[warn] {path} missing, skipping", file=sys.stderr)
            continue
        timeline = json.loads(path.read_text())
        a, u = merge(timeline, managed)
        path.write_text(json.dumps(timeline, separators=(",", ":")))
        print(f"[done] timeline {path.name}: +{a} added · {u} updated · total {len(timeline['agents'])}", file=sys.stderr)

    # Artifacts augmentation (same stable_id keyed by title)
    for path in ARTIFACT_PATHS:
        if not path.exists():
            print(f"[warn] {path} missing, skipping artifacts", file=sys.stderr)
            continue
        artifacts = json.loads(path.read_text())
        ag, fl = augment_artifacts(artifacts, managed)
        path.write_text(json.dumps(artifacts, separators=(",", ":")))
        print(f"[done] artifacts {path.name}: +{ag} agents · +{fl} files · total {len(artifacts)} agent-keys", file=sys.stderr)

    # Hierarchy augmentation — fleet-pattern orgs for sandbox agents
    for path in HIERARCHY_PATHS:
        if not path.exists():
            print(f"[warn] {path} missing, skipping hierarchy", file=sys.stderr)
            continue
        hier = json.loads(path.read_text())
        ao, am = augment_hierarchy(hier, managed)
        path.write_text(json.dumps(hier, separators=(",", ":")))
        print(f"[done] hierarchy {path.name}: +{ao} orgs · +{am} agent mappings · total {len(hier['orgs'])} orgs · {len(hier['agentToOrg'])} mapped agents", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
