#!/usr/bin/env python3
"""Extract agent prose from any bundle project.
Input: project name (from $SESSION_BUNDLE_ROOT/{projects,fleets}/<name>.json, default data/bundles)
Output: JSONL at data/prose/<name>.jsonl

Each line = one prose turn with normalized schema:
  {
    "project": str,
    "session_id": str,
    "agent_title": str,
    "provider": "claude"|"codex",
    "model": str,
    "host": "workstation"|"data-host"|"mac",
    "fleet": str|null,
    "account": str|null,
    "role": "assistant_prose"|"user",
    "text": str,
    "ts": float,
    "meta": {
      "tool_call_count": int,
      "has_error": bool,
      "has_file_edit": bool,
      "is_subagent_report": bool,
      "char_len": int,
      "phase": "commentary"|"final_answer"|null,  # codex only
    }
  }

Codex policy per CKL default: exclude commentary, keep final_answer only.
"""
import json, os, sys, subprocess, hashlib, glob, re
from pathlib import Path
from datetime import datetime, timezone

BUNDLE_DIR = Path(os.environ.get("SESSION_BUNDLE_ROOT", "data/bundles")) / "projects"  # session index, see docs/adapt-to-your-harness.md
OUT_DIR = Path("data/prose")
OUT_DIR.mkdir(parents=True, exist_ok=True)

INCLUDE_CODEX_COMMENTARY = False  # CKL default: exclude

BARE_HEX_RE = re.compile(r"^[0-9a-f]{8}$")

# v13-AA: classify user-role messages so the UI can render human prompts vs
# agent-to-agent (cct send) traffic with distinct bubble styles.
A2A_HEADER_RE = re.compile(r"\[from\s+(?P<sender>[^\]]+)\]", re.IGNORECASE)
# Claude Code wraps big/formatted content pasted into user messages (including
# a2a delivery) in <pasted_content id="..."> wrappers — strip those before the
# a2a-header match or we'd mis-classify every long cct send as "human".
PASTE_WRAP_RE = re.compile(r"<pasted_content\s+id=\"[^\"]*\">\s*", re.IGNORECASE)


def classify_user_message(text):
    """Returns (msg_kind, a2a_sender_or_None)."""
    if not text or not text.strip():
        return "empty", None
    probe = PASTE_WRAP_RE.sub("", text).lstrip()
    # Only match within the first 160 chars so a stray `[from ...]` midway
    # through a long human message doesn't masquerade as a2a.
    m = A2A_HEADER_RE.match(probe[:160])
    if m:
        return "a2a", m.group("sender").strip()
    return "human", None


def normalize_claude_title(raw_title, sid, project):
    """v11-Q2: bare 8-char hex titles become 'claude-<project>-<sid[-8:]>'.
    Preserves other titles unchanged."""
    if raw_title and not BARE_HEX_RE.match(raw_title):
        return raw_title, raw_title
    short = (sid or "")[-8:] or "unknown"
    proj = project or "unknown"
    return f"claude-{proj}-{short}", raw_title


def ts_iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def read_remote(host, path):
    """SSH into host and cat file. Returns str or empty on failure.
    Path may contain shell globs (e.g. ~/.codex/sessions/**/rollout-*-UUID.jsonl)."""
    # Strip optional 'host:' prefix if included
    if ":" in path and path.startswith(host + ":"):
        path = path.split(":", 1)[1]
    try:
        # Use shell to resolve glob + cat matches. Expand ~ remotely.
        cmd = f"shopt -s globstar nullglob 2>/dev/null; for f in {path}; do cat \"$f\"; done"
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", host, "bash", "-c", cmd],
            capture_output=True, text=True, timeout=180, errors="ignore",
        )
        if r.returncode != 0:
            print(f"[WARN] ssh rc={r.returncode} {host}:{path}", file=sys.stderr)
            if r.stderr:
                print(f"       stderr: {r.stderr[:200]}", file=sys.stderr)
        return r.stdout
    except Exception as e:
        print(f"[WARN] remote read failed {host}:{path}: {e}", file=sys.stderr)
        return ""


LOCAL_HOST = os.uname().nodename


def read_jsonl_lines(path, host):
    """Yield lines from a jsonl file, local or remote.
    Remote paths may contain host: prefix or glob patterns."""
    if host and host != LOCAL_HOST:
        # Attempt remote read via ssh (user has aliases: laptop, data-host)
        content = read_remote(host, path)
        for line in content.splitlines():
            if line.strip():
                yield line
    else:
        # Strip host: prefix if present
        if ":" in path and path.startswith(LOCAL_HOST + ":"):
            path = path.split(":", 1)[1]
        # Expand ~ and resolve globs locally
        expanded = os.path.expanduser(path)
        matches = glob.glob(expanded, recursive=True) if any(c in expanded for c in "*?[") else [expanded]
        for p in matches:
            try:
                with open(p, errors="ignore") as fh:
                    for line in fh:
                        if line.strip():
                            yield line
            except FileNotFoundError:
                print(f"[WARN] local file missing: {p}", file=sys.stderr)


def extract_claude(session, project, out_fh, dedupe):
    """Pull Claude assistant text blocks + user content. Walk main + subagents."""
    paths = [session["path"]]
    sub = session.get("subagentsDir")
    if sub:
        # Walk subagents dir for any additional transcripts
        if session["host"] in (None, "workstation", os.uname().nodename):
            paths.extend(glob.glob(os.path.join(sub, "**/*.jsonl"), recursive=True))

    kept = 0
    for p in paths:
        is_sub = "/subagents/" in p
        for line in read_jsonl_lines(p, session.get("host")):
            try:
                o = json.loads(line)
            except Exception:
                continue
            m = o.get("message") or {}
            t = ts_iso(o.get("timestamp"))
            role = m.get("role")
            if not t:
                continue

            content = m.get("content") or []
            # Count tool_use blocks for meta flag
            tool_count = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "tool_use")
            has_edit = any(
                isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in ("Edit", "Write", "NotebookEdit")
                for c in content
            )
            has_err = False
            # tool_result blocks (in user messages) may carry errors
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    txt = str(c.get("content", "")).lower()
                    if "error" in txt or "traceback" in txt or "exception" in txt:
                        has_err = True

            if role == "assistant":
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        text = c.get("text", "").strip()
                        if not text:
                            continue
                        h = hashlib.md5((str(m.get("id")) + text).encode()).hexdigest()
                        if h in dedupe:
                            continue
                        dedupe.add(h)
                        norm_title, raw_title = normalize_claude_title(session.get("title") or "", session["id"], project)
                        rec = {
                            "project": project,
                            "session_id": session["id"],
                            "agent_title": norm_title,
                            "provider": "claude",
                            "model": session.get("model") or "",
                            "host": session.get("host") or "",
                            "fleet": session.get("fleet"),
                            "account": session.get("account"),
                            "role": "assistant_prose",
                            "text": text,
                            "ts": t,
                            "meta": {
                                "tool_call_count": tool_count,
                                "has_error": has_err,
                                "has_file_edit": has_edit,
                                "is_subagent_report": is_sub,
                                "char_len": len(text),
                                "phase": None,
                                "agent_title_raw": raw_title,
                            },
                        }
                        out_fh.write(json.dumps(rec) + "\n")
                        kept += 1
            elif role == "user":
                # Include user prose only when it's actual text (not pure tool_result).
                # Claude Code messages can have content as a bare string OR a list of
                # dicts; handle both. v13-AA bugfix: the bare-string case was being
                # iterated character-by-character, extracting nothing.
                user_text_chunks = []
                if isinstance(content, str):
                    user_text_chunks.append(content)
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            user_text_chunks.append(c.get("text", ""))
                for text in user_text_chunks:
                    text = (text or "").strip()
                    if not text or len(text) < 3:
                        continue
                    # Skip pasted-content commands and tool-result wrappers
                    if text.startswith("<local-command-stdout>"):
                        continue
                    # Strip the system-reminder preamble common in long-running sessions,
                    # but KEEP the actual user prompt that follows.
                    if text.startswith("<system-reminder>"):
                        end = text.find("</system-reminder>")
                        if end >= 0:
                            text = text[end + len("</system-reminder>"):].strip()
                        if not text:
                            continue
                    # v13-AA: user messages have id=None in Claude Code, so hashing
                    # on (id, text) collapsed EVERY user turn into one entry globally.
                    # Key on session_id + ts + first 200 chars to preserve legit dupes.
                    h = hashlib.md5((session["id"] + str(t) + text[:200]).encode()).hexdigest()
                    if h in dedupe:
                        continue
                    dedupe.add(h)
                    norm_title, raw_title = normalize_claude_title(session.get("title") or "", session["id"], project)
                    kind, sender = classify_user_message(text)
                    rec = {
                        "project": project,
                        "session_id": session["id"],
                        "agent_title": norm_title,
                        "provider": "claude",
                        "model": session.get("model") or "",
                        "host": session.get("host") or "",
                        "fleet": session.get("fleet"),
                        "account": session.get("account"),
                        "role": "user_prose",
                        "text": text,
                        "ts": t,
                        "meta": {
                            "tool_call_count": 0,
                            "has_error": False,
                            "has_file_edit": False,
                            "is_subagent_report": False,
                            "char_len": len(text),
                            "phase": None,
                            "agent_title_raw": raw_title,
                            "msg_kind": kind,
                            "a2a_sender": sender,
                        },
                    }
                    out_fh.write(json.dumps(rec) + "\n")
                    kept += 1
    return kept


def extract_codex(session, project, out_fh, dedupe):
    """Pull Codex assistant messages. CKL default: final_answer only."""
    path = session["path"]
    kept = 0
    for line in read_jsonl_lines(path, session.get("host")):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "response_item":
            continue
        p = o.get("payload") or {}
        if p.get("type") != "message" or p.get("role") != "assistant":
            continue
        t = ts_iso(o.get("timestamp"))
        if not t:
            continue
        phase = p.get("phase") or "message"
        if not INCLUDE_CODEX_COMMENTARY and phase == "commentary":
            continue
        text = "".join(c.get("text", "") for c in (p.get("content") or []) if isinstance(c, dict))
        if not text.strip():
            continue
        h = hashlib.md5((session["id"] + text + str(t)).encode()).hexdigest()
        if h in dedupe:
            continue
        dedupe.add(h)
        rec = {
            "project": project,
            "session_id": session["id"],
            "agent_title": session.get("title") or "",
            "provider": "codex",
            "model": session.get("model") or "",
            "host": session.get("host") or "",
            "fleet": session.get("fleet"),
            "account": session.get("account"),
            "role": "assistant_prose",
            "text": text,
            "ts": t,
            "meta": {
                "tool_call_count": 0,
                "has_error": "error" in text.lower() or "traceback" in text.lower(),
                "has_file_edit": False,
                "is_subagent_report": False,
                "char_len": len(text),
                "phase": phase,
            },
        }
        out_fh.write(json.dumps(rec) + "\n")
        kept += 1
    return kept


FLEET_BUNDLE_DIR = BUNDLE_DIR.parent / "fleets"


def _load_sessions(name):
    """Union of project bundle + fleet bundle, deduped by session id.

    Project bundles are keyed by cwd (so sessions started outside the project folder
    are missed). Fleet bundles are keyed by title prefix via the registry resolver and
    pick up sessions regardless of cwd. Loading both means a session with a
    `<fleet>-*` title is captured even when run from [REDACTED_PATH] or any other cwd.
    """
    sessions = {}
    for bundle in (BUNDLE_DIR / f"{name}.json", FLEET_BUNDLE_DIR / f"{name}.json"):
        if not bundle.exists():
            continue
        data = json.load(open(bundle))
        for s in data.get("sessions", []):
            sid = s.get("id")
            if sid and sid not in sessions:
                sessions[sid] = s
    return list(sessions.values())


def extract_project(name):
    sessions = _load_sessions(name)
    if not sessions:
        print(f"[ERR] no bundle (project or fleet) for '{name}'", file=sys.stderr)
        return None
    out_path = OUT_DIR / f"{name}.jsonl"
    dedupe = set()
    totals = {"claude": 0, "codex": 0, "opencode": 0, "gemini": 0, "skipped": 0}
    with open(out_path, "w") as fh:
        for s in sessions:
            prov = s.get("provider")
            if prov == "claude":
                totals["claude"] += extract_claude(s, name, fh, dedupe)
            elif prov == "codex":
                totals["codex"] += extract_codex(s, name, fh, dedupe)
            else:
                totals["skipped"] += 1
    print(f"[{name}] wrote {out_path}: {totals}  ({len(sessions)} sessions from project+fleet bundles)")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: extract_prose.py <project_name> [project_name ...]", file=sys.stderr)
        sys.exit(1)
    for n in sys.argv[1:]:
        extract_project(n)
