#!/usr/bin/env python3
"""Walk ~/.codex/sessions/**/rollout-*.jsonl, extract assistant_prose + user text,
attribute to project by session_meta.cwd, write to data/prose/codex-<project>.jsonl.

Captures per-turn model from turn_context events (gpt-6.1-sol, etc).

v11-Q: codex agent_title is derived from the first user prompt (intent slug) or
falls back to the last 8 chars of session_id, so sibling codex sessions in the
same cwd are visually distinct instead of collapsing to one '<project>-master'.
"""
import json
import os
import re
import sys
import glob
import hashlib
import datetime
from pathlib import Path


SESS_ROOT = Path.home() / ".codex" / "sessions"
OUT_DIR = Path("data/prose")
HOST = os.uname().nodename
# v13-V: additional per-host local caches of remote codex sessions, pulled by
# scripts/scrape_remote_hosts.py. Each subdir is a mirror of ~/.codex/sessions/.
REMOTE_ROOT = Path("data/remote-codex")

KNOWN_PROJECTS = {
    "gamejam", "other-project", "other-project", "other-project",
    "nebius-hack-dev", "nebius-nvidia-hackathon",
}


def ts_from(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


from _privacy_blocklist import BLOCKED_PATH_PREFIXES as PRIVACY_LOCKED_PATHS  # local, untracked list


def project_for_cwd(cwd):
    """Return (project, kind, default_agent_title, flags).

    kind drives whether a per-session unique title should replace the default:
      worktree       -> keep default (branch name is already unique)
      master         -> codex-<project>-<slug|sid>
      other-project       -> codex-other-<slug|sid>
      adhoc          -> codex-adhoc-<slug|sid>
      other-project> codex-personal-<slug|sid>
      scratchpad     -> codex-scratchpad-<slug|sid>
      privacy-locked -> row suppressed entirely
      unknown        -> keep default
    """
    flags = {}
    if not cwd:
        return ("other-project", "unknown", "codex-unknown", flags)
    cwd_s = str(cwd)
    for lock in PRIVACY_LOCKED_PATHS:
        if cwd_s == lock.rstrip("/") or cwd_s.startswith(lock):
            flags["privacy_locked"] = True
            return ("[REDACTED]", "privacy-locked", "[REDACTED]", flags)
    if cwd_s == "[REDACTED_PATH]" or cwd_s.startswith("[REDACTED_PATH]"):
        flags["is_test_harness"] = True
        return ("other-project", "other-project", "other-project", flags)
    if cwd_s == "[REDACTED_PATH]" or cwd_s.startswith("[REDACTED_PATH]"):
        return ("other-project", "other-project", "claude-folder-codex", flags)
    if cwd_s.startswith("/tmp/"):
        return ("scratchpad", "scratchpad", "scratchpad-codex", flags)
    parts = Path(cwd).parts
    if cwd_s.rstrip("/") == "[REDACTED_PATH]":
        return ("other-project", "adhoc", "codex-adhoc", flags)
    try:
        idx = parts.index("Agent")
    except ValueError:
        name = Path(cwd).name or "codex"
        parent = Path(cwd).parent.name or "other"
        return (parent, "unknown", name, flags)
    if idx + 1 >= len(parts):
        return ("other-project", "adhoc", "codex-adhoc", flags)
    proj = parts[idx + 1]
    if idx + 3 < len(parts) and parts[idx + 2] == "worktrees":
        branch = parts[idx + 3]
        return (proj, "worktree", branch, flags)
    return (proj, "master", proj + "-master", flags)


STOP_PREFIXES = (
    "please ", "i need to ", "i need ", "can you ",
    "could you ", "we need to ", "we need ", "let's ", "lets ",
    "hey ", "hi ", "ok ", "okay ", "so ", "task: ", "task ",
    "goal: ", "goal ", "context: ", "reply with ",
)
GENERIC_SLUGS = {
    "hi", "hello", "test", "testing", "ok", "yes", "no", "continue",
    "go", "help", "fix", "thanks", "thanks-", "done", "ping", "status",
    "codex-ok", "codex", "reply", "reply-with-exactly",
}
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
NONALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
AGENTS_MD_RE = re.compile(r"^\s*#\s*AGENTS\.md\b", re.IGNORECASE)
CCT_FROM_RE = re.compile(r"^\s*\[from\s+[^\]]+\]\s*", re.IGNORECASE)
YOU_ARE_RE = re.compile(r"^you\s+are\s+(?:(?:a|an|the)\s+)?([a-z0-9][\w\-]{2,60})", re.IGNORECASE)
SHARED_CTX_RE = re.compile(r"^\s*(shared context|read first|applies to all|context\b)", re.IGNORECASE)


def _finalize_slug(t, max_len=40):
    t = NONALNUM_RE.sub("-", t).strip("-").lower()
    if not t:
        return None
    if len(t) > max_len:
        t = t[:max_len]
        if "-" in t:
            t = t[:t.rfind("-")].rstrip("-")
    if len(t) < 4 or t in GENERIC_SLUGS:
        return None
    return t


def slugify_intent(text, max_len=40):
    if not text:
        return None
    t = TAG_RE.sub(" ", text)
    t = WS_RE.sub(" ", t).strip()
    lower = t.lower()
    for sp in STOP_PREFIXES:
        if lower.startswith(sp):
            t = t[len(sp):]
            break
    return _finalize_slug(t, max_len=max_len)


def _looks_like_boilerplate(text):
    t = text.strip()
    if not t:
        return True
    if AGENTS_MD_RE.match(t):
        return True
    if SHARED_CTX_RE.match(t):
        return True
    low = t.lower()
    # cct brief "Reply with exactly: ..." pings aren't informative
    stripped = CCT_FROM_RE.sub("", t, count=1).lower().lstrip()
    if stripped.startswith("reply with exactly") or stripped.startswith("say exactly"):
        return True
    if stripped.startswith("codex ok") or stripped == "ping" or stripped.startswith("acknowledge"):
        return True
    return False


def extract_title_from_prompts(user_prompts):
    """Walk user prompts until one yields a usable (slug, source).

    source values:
      brief-name  — explicit "you are <name>" directive; name used as-is (no prefix)
      brief-slug  — cct brief envelope stripped, slugified rest
      slug        — plain first-prompt slug
      None        — nothing usable
    """
    for text in user_prompts:
        if not text:
            continue
        if _looks_like_boilerplate(text):
            continue
        t = TAG_RE.sub(" ", text)
        t = WS_RE.sub(" ", t).strip()
        rest = CCT_FROM_RE.sub("", t, count=1)
        is_brief = rest != t
        rest = rest.strip()
        if is_brief:
            m = YOU_ARE_RE.match(rest)
            if m:
                name = m.group(1).strip("-_.").lower()
                if name and name not in GENERIC_SLUGS and len(name) >= 3:
                    if len(name) > 40:
                        name = name[:40].rstrip("-")
                    return (name, "brief-name")
            slug = slugify_intent(rest)
            if slug:
                return (slug, "brief-slug")
            continue
        slug = slugify_intent(t)
        if slug:
            return (slug, "slug")
    return (None, None)


def unique_agent_title(kind, project, slug_tuple, sid):
    """Build a self-identifying codex title: 'codex-<slot>-<tag>' per rule M-Q.

    Returns None for worktree + privacy-locked kinds (caller keeps default_title).
    """
    slug, source = slug_tuple if slug_tuple else (None, None)
    short_sid = (sid or "")[-8:] or "unknown"
    if source == "brief-name" and slug:
        return slug  # already globally meaningful subagent name, no prefix
    tag = slug or short_sid
    if kind == "master":
        return f"codex-{project}-{tag}"
    if kind == "other-project":
        return f"codex-other-{tag}"
    if kind == "adhoc":
        return f"codex-adhoc-{tag}"
    if kind == "other-project":
        return f"codex-personal-{tag}"
    if kind == "scratchpad":
        return f"codex-scratchpad-{tag}"
    if kind == "unknown":
        # Rule 7 fallback cwds (/, /tmp parents, random leaf) — still self-identify
        proj_slot = project or "adhoc"
        return f"codex-{proj_slot}-{tag}"
    return None  # worktree / privacy-locked keep default


MODEL_RE = re.compile(r"\b(gpt-[\w\.\-]+|claude-[\w\.\-]+|opus-[\w\.\-]+|sonnet-[\w\.\-]+|fable-[\w\.\-]+|haiku-[\w\.\-]+)\b", re.IGNORECASE)

A2A_HEADER_RE = re.compile(r"^\s*\[from\s+(?P<sender>[^\]]+)\]", re.IGNORECASE)


def classify_user_message(text):
    """v13-AA: distinguish human prompt vs cct-send a2a envelope."""
    if not text or not text.strip():
        return "empty", None
    m = A2A_HEADER_RE.match(text)
    if m:
        return "a2a", m.group("sender").strip()
    return "human", None


# v13-T: content-based project attribution for codex-adhoc/scratchpad/unknown sessions.
# Each entry is (project_name, compiled regex matching any of its keywords).
PROJECT_KEYWORDS = {
    "gamejam": [
        "gamejam", "wowcod", "wow cod", "MW3", "mw3", "THPS", "thps", "skate3", "skate 3",
        "molten core", "ironforge", "orgrimmar", "northshire", "stormwind", "deadmines",
        "van cleef", "katamari", "warcraft", "bam margera", "tony hawk",
    ],
    "other-project": [
        "ghost in the droid", "other-project", "skill hub", "ghost phone", "android world",
        "ghost-core", "other-project",
    ],
    "other-project": [
        "cct branch", "cct send", "cct agent-status", "coding agent tmux", "harness-architect",
    ],
    "nebius-hack-dev": [
        "nebius token factory", "nebius-hack", "cct fleet dashboard", "prose-only observability",
        "qwen3-30b-a3b", "qwen3-30b", "nebius studio",
    ],
}


def _compile_project_patterns():
    out = {}
    for proj, kws in PROJECT_KEYWORDS.items():
        # Word-boundary around alphanumerics; the `(?i)` flag is applied inline.
        esc = sorted({re.escape(k) for k in kws}, key=len, reverse=True)
        out[proj] = re.compile(r"(?i)(?<![\w-])(?:" + "|".join(esc) + r")(?![\w-])")
    return out


_PROJECT_PATS = _compile_project_patterns()
_REATTRIB_KINDS = {"adhoc", "scratchpad", "unknown"}
_REATTRIB_HIT_MIN = 5
_REATTRIB_DOMINANCE = 0.60
_REATTRIB_SCAN_CHARS = 5000


def _score_project(text):
    """Return (dominant_project, hits_dominant, hits_total) from text."""
    scores = {}
    total = 0
    for proj, pat in _PROJECT_PATS.items():
        n = len(pat.findall(text))
        scores[proj] = n
        total += n
    if not total:
        return (None, 0, 0)
    dom = max(scores.items(), key=lambda x: x[1])
    return (dom[0], dom[1], total)


def extract_file(path, sessions, dedupe, host=HOST):
    """Extract assistant_prose + user text from one rollout file.

    Writes nothing. Appends a session entry to `sessions` (list of dicts):
      { sid, cwd, proj, kind, default_title, slug_tuple, flags, buffered }
    buffered is a list of records with placeholder agent_title.
    """
    cwd = None
    session_id = None
    session_start = None
    originator = None
    base_instr_model_guess = None
    cur_model = None
    user_prompts = []  # all user turns, in order, env-context stripped
    buffered = []  # (role, text, ts, model_at_time)

    try:
        fh = open(path, errors="ignore")
    except Exception as e:
        print(f"[warn] cannot open {path}: {e}", file=sys.stderr)
        return 0, None, None

    for line in fh:
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        p = o.get("payload") or {}
        if t == "session_meta":
            cwd = p.get("cwd") or ""
            session_id = p.get("session_id") or p.get("id") or ""
            originator = p.get("originator") or ""
            session_start = ts_from(o.get("timestamp") or p.get("timestamp"))
            bi = (p.get("base_instructions") or {}).get("text", "")
            m = re.search(r"based on (GPT-[\w\.]+)", bi)
            if m:
                base_instr_model_guess = m.group(1).lower().replace("gpt-", "gpt-")
            continue
        if t == "turn_context":
            if p.get("model"):
                cur_model = p["model"]
            continue
        if t == "response_item" and p.get("type") == "message":
            if not session_id:
                continue
            role = p.get("role")
            if role not in ("assistant", "user"):
                continue
            text = "".join(c.get("text", "") for c in (p.get("content") or []) if isinstance(c, dict))
            if not text or not text.strip():
                continue
            if role == "user" and text.startswith("<environment_context>"):
                end = text.find("</environment_context>")
                if end >= 0:
                    text = text[end + len("</environment_context>"):].strip()
                if not text:
                    continue
            ts = ts_from(o.get("timestamp"))
            if ts is None:
                ts = session_start or 0
            if role == "user":
                user_prompts.append(text)
            buffered.append((role, text, ts, cur_model or base_instr_model_guess or "codex-unknown"))
    fh.close()

    proj, kind, default_title, flags = project_for_cwd(cwd)
    if flags.get("privacy_locked"):
        return 0, cwd, session_id

    slug_tuple = extract_title_from_prompts(user_prompts)

    msgs = 0
    recs = []
    for role, text, ts, model in buffered:
        key = hashlib.md5((session_id + text[:200] + str(round(ts, 1))).encode()).hexdigest()
        if key in dedupe:
            continue
        dedupe.add(key)
        rec = {
            "project": proj,
            "session_id": session_id,
            "agent_title": None,  # filled in finalize pass
            "provider": "codex",
            "model": model,
            "host": host,
            "fleet": proj,
            "account": None,
            "role": "assistant_prose" if role == "assistant" else "user_prose",
            "text": text,
            "ts": ts,
            "meta": {
                "tool_call_count": 0,
                "has_error": "error" in text.lower() or "traceback" in text.lower(),
                "has_file_edit": False,
                "is_subagent_report": False,
                "char_len": len(text),
                "phase": None,
                "originator": originator,
                "cwd": cwd,
                "msg_kind": classify_user_message(text)[0] if role == "user" else "agent",
                "a2a_sender": classify_user_message(text)[1] if role == "user" else None,
                **flags,
            },
        }
        recs.append(rec)
        msgs += 1

    sessions.append({
        "sid": session_id,
        "cwd": cwd,
        "proj": proj,
        "kind": kind,
        "default_title": default_title,
        "slug_tuple": slug_tuple,
        "flags": flags,
        "recs": recs,
    })
    return msgs, cwd, session_id


def content_reattribute(sessions):
    """v13-T: for codex sessions whose kind is adhoc/scratchpad/unknown, scan
    the first ~5K chars of concatenated prose for project keywords. If one
    project dominates (>5 hits AND >60% of total hits), rewrite s['proj'] and
    s['kind']='master' so subsequent title/collision passes produce
    'codex-<project>-...' instead of 'codex-adhoc-...'.
    Preserves original attribution in s['reattrib_from'] for logging.
    """
    reattributed = {}
    for s in sessions:
        if s["kind"] not in _REATTRIB_KINDS:
            continue
        # Build concatenated text up to the scan budget
        buf = []
        total = 0
        for rec in s["recs"]:
            txt = rec.get("text") or ""
            if not txt:
                continue
            buf.append(txt)
            total += len(txt)
            if total >= _REATTRIB_SCAN_CHARS:
                break
        text = "\n".join(buf)[:_REATTRIB_SCAN_CHARS]
        dom, hits_dom, hits_total = _score_project(text)
        if not dom or hits_dom < _REATTRIB_HIT_MIN:
            continue
        if hits_total and hits_dom / hits_total < _REATTRIB_DOMINANCE:
            continue
        key = f"{s['proj']}→{dom}"
        reattributed[key] = reattributed.get(key, 0) + 1
        s["reattrib_from"] = {"proj": s["proj"], "kind": s["kind"], "hits_dominant": hits_dom, "hits_total": hits_total}
        s["proj"] = dom
        s["kind"] = "master"
        # Also propagate to each record's project/fleet so prose JSONL + downstream match
        for rec in s["recs"]:
            rec["project"] = dom
            rec["fleet"] = dom
            rec["meta"]["attribution_raw"] = {
                "project": s["reattrib_from"]["proj"],
                "kind": s["reattrib_from"]["kind"],
                "cwd": rec["meta"].get("cwd"),
                "hits_dominant": hits_dom,
                "hits_total": hits_total,
            }
    return reattributed


def finalize_titles(sessions):
    """Resolve agent_title per session with global collision dedup.

    First pass: compute per-session candidate title from slug_tuple.
    Second pass: within each (project, kind) bucket, demote slugs shared by
    multiple sessions back to the sid fallback so no two sessions collide.
    Returns (title_stats, collision_count).
    """
    for s in sessions:
        unique = unique_agent_title(s["kind"], s["proj"], s["slug_tuple"], s["sid"])
        if unique is None:
            s["agent_title"] = s["default_title"]
            s["title_source"] = "default"
        else:
            s["agent_title"] = unique
            s["title_source"] = s["slug_tuple"][1] or "sid"

    # Collision pass: group by (project, kind) and detect duplicate titles.
    # Worktree kind is left alone — branch collisions are legitimate (same branch
    # worked on in multiple sessions is semantically meaningful).
    buckets = {}
    for s in sessions:
        buckets.setdefault((s["proj"], s["kind"]), []).append(s)
    collisions = 0
    for (proj, kind), group in buckets.items():
        if kind == "worktree":
            continue
        title_counts = {}
        for s in group:
            title_counts[s["agent_title"]] = title_counts.get(s["agent_title"], 0) + 1
        for s in group:
            if title_counts[s["agent_title"]] <= 1 or s["title_source"] == "brief-name":
                continue
            short_sid = (s["sid"] or "")[-8:] or "unknown"
            demoted = unique_agent_title(kind, proj, (None, "sid"), s["sid"])
            if demoted is None:
                # kind=unknown/other: just suffix the default title with sid
                s["agent_title"] = f"{s['agent_title']}-{short_sid}"
            else:
                s["agent_title"] = demoted
            s["title_source"] = "sid-demoted"
            collisions += 1
    # Brief-name collisions (same subagent name used twice): suffix with short sid
    name_counts = {}
    for s in sessions:
        if s["title_source"] == "brief-name":
            name_counts[s["agent_title"]] = name_counts.get(s["agent_title"], 0) + 1
    for s in sessions:
        if s["title_source"] == "brief-name" and name_counts.get(s["agent_title"], 0) > 1:
            short_sid = (s["sid"] or "")[-8:] or "unknown"
            s["agent_title"] = f"{s['agent_title']}-{short_sid}"
            s["title_source"] = "brief-name-dedup"

    stats = {}
    for s in sessions:
        stats[s["title_source"]] = stats.get(s["title_source"], 0) + 1
    return stats, collisions


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("codex-*.jsonl"):
        old.unlink()

    files = [(HOST, p) for p in sorted(glob.glob(str(SESS_ROOT / "**" / "rollout-*.jsonl"), recursive=True))]
    # v13-V: pick up each remote host cache in data/remote-codex/<host>/
    if REMOTE_ROOT.is_dir():
        for host_dir in sorted(REMOTE_ROOT.iterdir()):
            if not host_dir.is_dir():
                continue
            remote_files = sorted(glob.glob(str(host_dir / "**" / "rollout-*.jsonl"), recursive=True))
            for p in remote_files:
                files.append((host_dir.name, p))
            print(f"[info] remote host '{host_dir.name}': +{len(remote_files)} rollout files", file=sys.stderr)
    print(f"[info] found {len(files)} codex rollout files total ({HOST} local + any remote caches)", file=sys.stderr)

    dedupe = set()
    attribution = {}
    sessions_seen = set()
    sessions = []

    total_msgs = 0
    for i, (host, path) in enumerate(files):
        if i % 50 == 0:
            print(f"[info] processed {i}/{len(files)} files, {total_msgs} msgs, {len(sessions_seen)} sessions", file=sys.stderr)
        msgs, cwd, sid = extract_file(path, sessions, dedupe, host=host)
        if sid:
            sessions_seen.add(sid)
        if cwd:
            attribution[cwd] = attribution.get(cwd, 0) + msgs
        total_msgs += msgs

    reattrib = content_reattribute(sessions)
    if reattrib:
        print(f"[info] content-reattributed {sum(reattrib.values())} session(s):", file=sys.stderr)
        for k, n in sorted(reattrib.items(), key=lambda x: -x[1]):
            print(f"    {n:>3}  {k}", file=sys.stderr)

    stats, collisions = finalize_titles(sessions)
    print(f"[info] finalized {len(sessions)} sessions, {collisions} slug collisions demoted to sid", file=sys.stderr)

    out_handles = {}
    counts = {}
    for s in sessions:
        proj_key = s["proj"]
        if proj_key not in out_handles:
            out_handles[proj_key] = open(OUT_DIR / f"codex-{proj_key}.jsonl", "w")
            counts.setdefault(proj_key, 0)
        for rec in s["recs"]:
            rec["agent_title"] = s["agent_title"]
            rec["meta"]["agent_title_source"] = s["title_source"]
            out_handles[proj_key].write(json.dumps(rec) + "\n")
            counts[proj_key] += 1
    for fh in out_handles.values():
        fh.close()

    print(f"[done] total msgs: {total_msgs}, sessions: {len(sessions_seen)}", file=sys.stderr)
    print(f"[done] per-project counts: {counts}", file=sys.stderr)
    print(f"[done] agent_title sources: {stats}", file=sys.stderr)
    print(f"[done] top cwds:", file=sys.stderr)
    for cwd, n in sorted(attribution.items(), key=lambda x: -x[1])[:10]:
        print(f"  {n:>6}  {cwd}", file=sys.stderr)


if __name__ == "__main__":
    main()
