#!/usr/bin/env python3
"""Reduce classified prose JSONL into agent-card JSON for the dashboard.

Groups by (project, agent_title). For each group:
  - task          <- most common agent_role across turns
  - progress      <- latest "report-progress" labels + their raw text snippet
  - blocker       <- most recent non-null blocker_type
  - required_action <- derived (v0.1 heuristic): if blocker, phrase it
  - urgency       <- highest urgency across last 20 turns
  - state         <- derived from blocker presence + urgency + recency
  - last_update   <- most recent ts
  - evidence_count <- count of classified turns in group

Output: JSON array suitable for Astro Props shape.

usage: reduce_to_cards.py <classified_dir> <out_json>
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from fleet_merge import canonicalize as canonicalize_fleet  # noqa: E402
from datetime import datetime
from pathlib import Path


import re
import urllib.request


def load_cct_dash_sessions(dash_url="http://127.0.0.1:8765/api/sessions"):
    """Fetch live cct-dash sessions, index by uuid for account + cwd enrichment."""
    try:
        with urllib.request.urlopen(dash_url, timeout=5) as r:
            d = json.load(r)
    except Exception:
        return {}
    out = {}
    for row in d.get("rows", []):
        for key in ("mapping_uuid", "cct_uuid", "v2_uuid", "live_uuid"):
            u = row.get(key)
            if u and u not in out:
                out[u] = {
                    "account": row.get("acct") or row.get("account") or "",
                    "cwd": row.get("cwd") or "",
                    "name": row.get("name") or "",
                    "model": row.get("model") or "",
                    "fleet": row.get("fleet") or "",
                }
    return out


SESSION_UUID_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){0,4}$")


def clean_summary(text, max_chars=140):
    """Pick a short, meaningful snippet from a text block for the task/progress line."""
    if not text:
        return ""
    text = text.strip()
    # Prefer the first sentence if the block is a paragraph
    for sep in ["\n\n", "\n"]:
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    text = text.strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        text = text[: max_chars] + "..."
    return text


def to_human_time(ts):
    if not ts:
        return "unknown"
    dt = datetime.fromtimestamp(ts)
    delta = (datetime.now() - dt).total_seconds()
    if delta < 60:
        return f"{int(delta)} sec ago"
    if delta < 3600:
        return f"{int(delta/60)} min ago"
    if delta < 86400:
        return f"{int(delta/3600)}h ago"
    return f"{int(delta/86400)}d ago"


def derive_state(blocker, urgency, recency_min):
    """State: blocked > waiting > idle > working based on evidence."""
    if blocker and urgency in ("mid", "high"):
        return "blocked"
    if blocker:
        return "waiting"
    if recency_min > 60:
        return "idle"
    return "working"


def required_action_for(blocker_type, blocker_text=None):
    """Build a required-action string. Prefer the actual turn text that triggered
    the blocker; fall back to a per-type heuristic phrase.
    """
    if not blocker_type:
        return None
    if blocker_text:
        # Pull a short, meaningful sentence
        hint = clean_summary(blocker_text, 180)
        if hint:
            return hint
    phrases = {
        "waiting-human": "operator needs to respond",
        "rate-limit": "wait for throttle or switch provider",
        "tool-failure": "fix broken tool invocation",
        "retry-loop": "break the loop, change approach",
        "context-exhausted": "compact history or restart session",
        "scope-ambiguous": "clarify scope with user",
    }
    return phrases.get(blocker_type, f"resolve: {blocker_type}")


def reduce_group(turns, text_lookup=None, dash_lookup=None, session_meta=None):
    """Collapse a sorted-by-ts list of classified turns into one card.

    text_lookup: {(src_id, ts): text} map for evidence snippets.
    dash_lookup: {uuid: {account, cwd, name, model, fleet}} from cct-dash API.
    session_meta: {session_id: {account, host, agent_title}} from prose JSONL.
    """
    if not turns:
        return None
    first = turns[0]
    turns = sorted(turns, key=lambda t: t.get("ts", 0))
    recent = turns[-20:]  # consider last 20 for state inference
    latest = turns[-1]
    text_lookup = text_lookup or {}
    dash_lookup = dash_lookup or {}
    session_meta = session_meta or {}
    dash = dash_lookup.get(first.get("src_id"), {})
    smeta = session_meta.get(first.get("src_id"), {})

    # agent_role aggregate (skip stringy "null")
    def _nn_pre(v):
        return v if v and v not in ("null", "none", "None") else None
    role_counts = Counter(_nn_pre(t["labels"].get("agent_role")) for t in turns if _nn_pre(t["labels"].get("agent_role")))
    top_role = role_counts.most_common(1)[0][0] if role_counts else None

    def _nn(v):
        """Treat 'null'/'none'/'' as None (classifier sometimes returns strings)."""
        if v in (None, "", "null", "none", "None"):
            return None
        return v

    # blocker: most recent non-null
    blocker = None
    blocker_turn = None
    blocker_text = None
    for t in reversed(recent):
        bt = _nn(t["labels"].get("blocker_type"))
        if bt:
            blocker = bt
            blocker_turn = t
            blocker_text = text_lookup.get((t.get("src_id"), t.get("ts")))
            break

    # urgency: highest in last 20
    urgency_order = {"low": 0, "mid": 1, "high": 2}
    urgencies = [_nn(t["labels"].get("urgency")) for t in recent]
    urgencies = [u for u in urgencies if u in urgency_order]
    highest_urg = max(urgencies, key=lambda u: urgency_order.get(u, 0)) if urgencies else None

    # agent_intent: most recent that is a progress or report
    progress_turn = None
    for t in reversed(recent):
        if t["labels"].get("agent_intent") in ("report-progress", "summary"):
            progress_turn = t
            break

    recency_sec = (datetime.now().timestamp()) - (latest.get("ts", 0))
    recency_min = recency_sec / 60.0

    state = derive_state(blocker, highest_urg, recency_min)

    def _snippet(t, max_chars=240):
        key = (t.get("src_id"), t.get("ts"))
        text = text_lookup.get(key, "")
        text = text.replace("\n", " ").strip()
        if len(text) > max_chars:
            text = text[: max_chars] + "..."
        return text

    # Build evidence samples: last 5 turns with text, blocker turn first if present
    evidence = []
    seen_ts = set()
    pool = []
    if blocker_turn:
        pool.append(blocker_turn)
        seen_ts.add(blocker_turn.get("ts"))
    for t in reversed(recent[-5:]):
        if t.get("ts") in seen_ts:
            continue
        pool.append(t)
    for t in pool[:5]:
        evidence.append({
            "ts": t.get("ts"),
            "ts_human": to_human_time(t.get("ts")),
            "labels": t.get("labels", {}),
            "text": _snippet(t),
            "role": t.get("role"),
        })

    # Pull real text snippets:
    #  task     = short role-oriented label ("worker: coordinating lanes")
    #  progress = the most recent substantive assistant prose turn (what it just said)
    progress_text = ""
    task_text = ""
    for t in reversed(recent):
        txt = text_lookup.get((t.get("src_id"), t.get("ts")), "")
        if len(txt.strip()) >= 60 and t.get("role") == "assistant_prose":
            progress_text = clean_summary(txt, 220)
            break
    # Task line = short characterization built from role + a few labels
    role_part = f"{top_role or 'agent'}"
    if blocker:
        task_text = f"{role_part} — currently {blocker.replace('-', ' ')}"
    elif progress_turn:
        intent = progress_turn["labels"].get("agent_intent", "")
        task_text = f"{role_part} — {intent.replace('-', ' ') if intent else 'active'}"
    else:
        task_text = f"{role_part} — idle"

    # Title enrichment: skip uuid-looking titles
    raw_title = first.get("agent_title") or smeta.get("agent_title") or ""
    looks_like_uuid = bool(SESSION_UUID_RE.match(raw_title)) or len(raw_title) == 8
    display_title = raw_title
    if looks_like_uuid or not raw_title:
        display_title = (
            dash.get("name")
            or (dash.get("cwd") and Path(dash["cwd"]).name)
            or f"(unnamed) {first.get('src_id', '')[:8]}"
        )

    # Account resolution: prose JSONL first (from bundle), dash fallback
    account = smeta.get("account") or dash.get("account") or ""

    return {
        "title": display_title,
        "project": first.get("project") or "",
        "fleet": canonicalize_fleet(first.get("fleet") or dash.get("fleet") or ""),
        "fleet_raw": first.get("fleet") or dash.get("fleet") or "",
        "model": first.get("model") or dash.get("model") or "",
        "account": account,
        "host": smeta.get("host") or dash.get("cwd") or "workstation",
        "state": state,
        "task": task_text or f"operating as {top_role}" if top_role else "idle",
        "progress": progress_text or "no recent progress signal",
        "blocker": {"type": blocker} if blocker else None,
        "required_action": required_action_for(blocker, blocker_text=blocker_text),
        "urgency": highest_urg,
        "last_update": to_human_time(latest.get("ts")),
        "evidence_count": len(turns),
        "evidence": evidence,
        "_src_session_id": first.get("src_id"),
        "_classifier_model": first.get("classifier_model"),
        "_had_uuid_title": looks_like_uuid,
    }


def reduce_file(path, text_lookup=None, dash_lookup=None, session_meta=None):
    """Return list of cards (one per session_id — each session is a card)."""
    turns = [json.loads(l) for l in open(path)]
    groups = defaultdict(list)
    for t in turns:
        key = t.get("src_id") or (t.get("project"), t.get("agent_title"))
        groups[key].append(t)
    cards = [
        reduce_group(ts, text_lookup=text_lookup, dash_lookup=dash_lookup, session_meta=session_meta)
        for ts in groups.values()
    ]
    return [c for c in cards if c]


def load_text_lookup(prose_dir, project_names):
    """Build (session_id, ts) -> text map from prose JSONL files.
    Also returns a side-dict mapping session_id -> {account, host} pulled from prose.
    """
    lookup = {}
    session_meta = {}  # session_id -> dict
    prose_dir = Path(prose_dir)
    for name in project_names:
        p = prose_dir / f"{name}.jsonl"
        if not p.exists():
            continue
        for line in open(p):
            try:
                o = json.loads(line)
            except Exception:
                continue
            key = (o.get("session_id"), o.get("ts"))
            lookup[key] = o.get("text") or ""
            sid = o.get("session_id")
            if sid and sid not in session_meta:
                session_meta[sid] = {
                    "account": o.get("account"),
                    "host": o.get("host"),
                    "agent_title": o.get("agent_title"),
                }
    return lookup, session_meta


def main():
    if len(sys.argv) < 3:
        print(
            "usage: reduce_to_cards.py <classified_dir_or_file> <out_json> [--prose-dir DIR]",
            file=sys.stderr,
        )
        sys.exit(1)
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    prose_dir = None
    if "--prose-dir" in sys.argv:
        prose_dir = Path(sys.argv[sys.argv.index("--prose-dir") + 1])
    all_cards = []
    paths = [src] if src.is_file() else sorted(src.glob("*.jsonl"))
    # Build text lookup from matching prose files
    text_lookup = None
    session_meta = None
    if prose_dir:
        project_names = sorted({p.stem.split("__", 1)[0] for p in paths})
        # Union with every raw prose file so post-rename codex projects land
        prose_names = sorted({p.stem for p in Path(prose_dir).glob("*.jsonl")}) if prose_dir else []
        all_names = sorted(set(project_names) | set(prose_names))
        text_lookup, session_meta = load_text_lookup(prose_dir, all_names)
        print(f"[info] built text lookup: {len(text_lookup)} entries, session_meta: {len(session_meta)} sessions from {len(all_names)} projects", file=sys.stderr)
    dash_lookup = load_cct_dash_sessions()
    print(f"[info] built cct-dash lookup: {len(dash_lookup)} session uuids", file=sys.stderr)
    for p in paths:
        all_cards.extend(reduce_file(p, text_lookup=text_lookup, dash_lookup=dash_lookup, session_meta=session_meta))
    # Sort: blocked first, then by recency
    state_rank = {"blocked": 0, "waiting": 1, "working": 2, "idle": 3, "done": 4}
    all_cards.sort(key=lambda c: (state_rank.get(c["state"], 9), c["last_update"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(all_cards, open(out, "w"), indent=2)
    print(f"wrote {len(all_cards)} cards to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
