#!/usr/bin/env python3
"""Build data/file_diffs.json + ui mirror.

Scans Claude JSONL transcripts and codex rollouts for file-mutation tool_use
events, resolves each to a canonical repo via _git_repo_resolver, and emits
per-session file-diff event lists with aggregated line counts.

Event shape:
  {"ts": 1784..., "kind": "added"|"changed"|"deleted", "path": "/abs/path.py",
   "repo": "/abs/repo/root", "la": 42, "lr": 7,
   "snippets": [{"old": "...", "new": "..."}, ...]  # only present for Edit/MultiEdit}
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict
from datetime import datetime
from typing import Iterable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _git_repo_resolver import (
    resolve_repo_root,
    _is_privacy_locked,
    _is_blocklisted,
    is_privacy_filename,
    contains_privacy_term,
)

ROOT = pathlib.Path(".")
OUT_DATA = ROOT / "data" / "file_diffs.json"
OUT_UI = ROOT / "ui" / "public" / "data" / "file_diffs.json"

CLAUDE_PROJECTS = pathlib.Path("[REDACTED_PATH]")
CODEX_SESS = pathlib.Path.home() / ".codex" / "sessions"
REMOTE_CODEX = ROOT / "data" / "remote-codex"

# Blocklist additions on top of resolver defaults
EXTRA_BLOCK_SUFFIX = (".pyc", ".DS_Store")
PER_SESSION_CAP = 500
SNIPPET_LIMIT_PER_PATH = 3
SNIPPET_CHAR_CAP = 80
# Larger budget for new-file previews (Write / apply_patch Add) — there's no
# "old" side to pair against, so we spend the whole pair on the head of the
# new content.
NEWFILE_SNIPPET_CHAR_CAP = 800

RM_RE = re.compile(r"(?m)(?:^|&&|;|\|\||\n)\s*rm\s+(?:-[a-zA-Z]+\s+)*([^\s;|&`'\"]+)")
EXEC_CMD_RE = re.compile(r"""cmd\s*:\s*"((?:\\.|[^"\\])*)" """.strip())
# apply_patch body parsing
PATCH_FILE_HDR = re.compile(r"^\*\*\* (Add|Update|Delete) File:\s*(.+?)\s*$", re.M)


def _parse_ts(ts) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        # Claude JSONL uses ISO Z
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _resolve_path(raw: Optional[str], cwd: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("~/"):
        raw = str(pathlib.Path(raw).expanduser())
    if raw.startswith("/"):
        p = raw
    elif cwd:
        p = str((pathlib.Path(cwd) / raw).resolve(strict=False)) if cwd.startswith("/") else None
        if p is None:
            return None
    else:
        return None
    if _is_privacy_locked(p):
        return None
    if _is_blocklisted(p):
        return None
    if any(p.endswith(s) for s in EXTRA_BLOCK_SUFFIX):
        return None
    if is_privacy_filename(pathlib.Path(p).name):
        return None
    return p


def _add_event(bucket: list, ts: float, kind: str, path: str, la: int, lr: int, snippets=None) -> None:
    if not path or ts is None:
        return
    repo = resolve_repo_root(path)
    if not repo:
        return
    ev = {"ts": round(float(ts), 3), "kind": kind, "path": path, "repo": repo, "la": int(la), "lr": int(lr)}
    if snippets:
        ev["snippets"] = snippets
    bucket.append(ev)


def _snip(s: str) -> Optional[str]:
    if not s:
        return ""
    # Privacy: drop snippet entirely if it mentions a blocked project term
    if contains_privacy_term(s):
        return None
    s = s.replace("\n", "\\n")
    if len(s) > SNIPPET_CHAR_CAP:
        return s[:SNIPPET_CHAR_CAP] + "…"
    return s


def _snip_pair(old: str, new: str) -> Optional[dict]:
    o = _snip(old)
    n = _snip(new)
    if o is None or n is None:
        return None
    return {"old": o, "new": n}


def _snip_newfile(content: str) -> Optional[dict]:
    """Return a snippet pair for a newly-created file: old="" + head of new content.
    Trims to the last newline before the cap when possible for readability."""
    if not content:
        return {"old": "", "new": ""}
    if contains_privacy_term(content):
        return None
    body = content
    truncated = False
    if len(body) > NEWFILE_SNIPPET_CHAR_CAP:
        body = body[:NEWFILE_SNIPPET_CHAR_CAP]
        cut = body.rfind("\n")
        if cut > NEWFILE_SNIPPET_CHAR_CAP // 2:
            body = body[:cut]
        truncated = True
    body = body.replace("\n", "\\n")
    if truncated:
        body = body + "…"
    return {"old": "", "new": body}


# ---------------- Claude ingestion ----------------

def ingest_claude(sessions: dict) -> None:
    """sessions is a dict: session_id -> event list."""
    if not CLAUDE_PROJECTS.exists():
        return
    seen_added_by_sess: dict[str, set] = defaultdict(set)
    snippets_count: dict[tuple, int] = defaultdict(int)  # (session_id, path) -> count
    for proj_dir in CLAUDE_PROJECTS.iterdir():
        if not proj_dir.is_dir():
            continue
        for jl in proj_dir.glob("*.jsonl"):
            try:
                fh = jl.open()
            except OSError:
                continue
            with fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    sid = r.get("sessionId")
                    cwd = r.get("cwd")
                    ts = _parse_ts(r.get("timestamp"))
                    if not sid or ts is None:
                        continue
                    msg = r.get("message") or {}
                    content = msg.get("content") or []
                    if not isinstance(content, list):
                        continue
                    for c in content:
                        if not isinstance(c, dict) or c.get("type") != "tool_use":
                            continue
                        nm = c.get("name") or ""
                        inp = c.get("input") or {}
                        if nm == "Write":
                            path = _resolve_path(inp.get("file_path"), cwd)
                            if not path:
                                continue
                            seen = seen_added_by_sess[sid]
                            kind = "added" if path not in seen else "changed"
                            seen.add(path)
                            body = inp.get("content") or ""
                            la = body.count("\n") + (1 if body else 0)
                            key = (sid, path)
                            snippets = None
                            if snippets_count[key] < SNIPPET_LIMIT_PER_PATH:
                                sp = _snip_newfile(body)
                                if sp:
                                    snippets_count[key] += 1
                                    snippets = [sp]
                            _add_event(sessions[sid], ts, kind, path, la, 0, snippets)
                        elif nm == "Edit":
                            path = _resolve_path(inp.get("file_path"), cwd)
                            if not path:
                                continue
                            old = inp.get("old_string") or ""
                            new = inp.get("new_string") or ""
                            la = new.count("\n") + 1
                            lr = old.count("\n") + 1
                            key = (sid, path)
                            snippets = None
                            if snippets_count[key] < SNIPPET_LIMIT_PER_PATH:
                                sp = _snip_pair(old, new)
                                if sp:
                                    snippets_count[key] += 1
                                    snippets = [sp]
                            _add_event(sessions[sid], ts, "changed", path, la, lr, snippets)
                        elif nm == "MultiEdit":
                            path = _resolve_path(inp.get("file_path"), cwd)
                            if not path:
                                continue
                            edits = inp.get("edits") or []
                            la = lr = 0
                            snippets_batch = []
                            for e in edits:
                                old = (e or {}).get("old_string") or ""
                                new = (e or {}).get("new_string") or ""
                                la += new.count("\n") + 1
                                lr += old.count("\n") + 1
                                if len(snippets_batch) < SNIPPET_LIMIT_PER_PATH:
                                    sp = _snip_pair(old, new)
                                    if sp:
                                        snippets_batch.append(sp)
                            key = (sid, path)
                            if snippets_count[key] < SNIPPET_LIMIT_PER_PATH and snippets_batch:
                                room = SNIPPET_LIMIT_PER_PATH - snippets_count[key]
                                snippets_batch = snippets_batch[:room]
                                snippets_count[key] += len(snippets_batch)
                            else:
                                snippets_batch = None
                            _add_event(sessions[sid], ts, "changed", path, la, lr, snippets_batch)
                        elif nm == "NotebookEdit":
                            path = _resolve_path(inp.get("notebook_path"), cwd)
                            if path:
                                _add_event(sessions[sid], ts, "changed", path, 1, 1)
                        elif nm == "Bash":
                            cmd = inp.get("command") or ""
                            for m in RM_RE.finditer(cmd):
                                rm_path = _resolve_path(m.group(1), cwd)
                                if rm_path:
                                    _add_event(sessions[sid], ts, "deleted", rm_path, 0, 0)


# ---------------- Codex ingestion ----------------

def _parse_apply_patch(body: str, cwd: Optional[str]) -> Iterable[tuple]:
    """Yield (kind, path, la, lr, snippets|None) tuples parsed from an apply_patch body."""
    # Split body into blocks by file header
    hdrs = list(PATCH_FILE_HDR.finditer(body))
    for i, m in enumerate(hdrs):
        action, raw_path = m.group(1), m.group(2).strip()
        start = m.end()
        end = hdrs[i + 1].start() if i + 1 < len(hdrs) else len(body)
        block = body[start:end]
        la = 0
        lr = 0
        added_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                la += 1
                if action == "Add":
                    added_lines.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                lr += 1
        resolved = _resolve_path(raw_path, cwd)
        if not resolved:
            continue
        if action == "Add":
            snip = _snip_newfile("\n".join(added_lines))
            yield ("added", resolved, la, 0, [snip] if snip else None)
        elif action == "Delete":
            yield ("deleted", resolved, 0, lr if lr > 0 else 1, None)
        else:  # Update
            yield ("changed", resolved, la, lr, None)


def _ingest_codex_dir(sess_root: pathlib.Path, sessions: dict) -> int:
    if not sess_root.exists():
        return 0
    files_seen = 0
    for jl in sess_root.rglob("rollout-*.jsonl"):
        files_seen += 1
        session_id = None
        cwd = None
        try:
            fh = jl.open()
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                t = r.get("type")
                if t == "session_meta":
                    p = r.get("payload") or {}
                    session_id = p.get("session_id") or p.get("id")
                    cwd = p.get("cwd")
                    continue
                if t != "response_item" or not session_id:
                    continue
                p = r.get("payload") or {}
                if p.get("type") != "custom_tool_call":
                    continue
                nm = p.get("name") or ""
                ts = _parse_ts(r.get("timestamp"))
                if ts is None:
                    continue
                inp = p.get("input") or ""
                if nm == "apply_patch":
                    body = inp if isinstance(inp, str) else ""
                    # Strip surrounding wrapper like `text(await tools.apply_patch("..."))`
                    if "*** Begin Patch" in body:
                        start = body.find("*** Begin Patch")
                        body = body[start:]
                    for kind, path, la, lr, snippets in _parse_apply_patch(body, cwd):
                        _add_event(sessions[session_id], ts, kind, path, la, lr, snippets)
                elif nm == "exec":
                    # Extract cmd:"..." sub-strings, scan each for rm patterns
                    text = inp if isinstance(inp, str) else ""
                    for m in EXEC_CMD_RE.finditer(text):
                        cmd = m.group(1).encode("utf-8").decode("unicode_escape", errors="replace") if "\\" in m.group(1) else m.group(1)
                        for rm_m in RM_RE.finditer(cmd):
                            rm_path = _resolve_path(rm_m.group(1), cwd)
                            if rm_path:
                                _add_event(sessions[session_id], ts, "deleted", rm_path, 0, 0)
    return files_seen


def ingest_codex(sessions: dict) -> None:
    _ingest_codex_dir(CODEX_SESS, sessions)
    # Pull remote host mirrors too
    if REMOTE_CODEX.exists():
        for host_dir in REMOTE_CODEX.iterdir():
            if host_dir.is_dir():
                _ingest_codex_dir(host_dir, sessions)


# ---------------- Finalize ----------------

def finalize(sessions: dict) -> dict:
    out: dict[str, list] = {}
    for sid, events in sessions.items():
        if not events:
            continue
        # Sort by ts, cap to newest PER_SESSION_CAP
        events.sort(key=lambda e: e["ts"])
        if len(events) > PER_SESSION_CAP:
            events = events[-PER_SESSION_CAP:]
        out[sid] = events
    return out


def main() -> int:
    sessions: dict[str, list] = defaultdict(list)
    print("ingesting Claude JSONL…", flush=True)
    ingest_claude(sessions)
    nc = sum(len(v) for v in sessions.values())
    print(f"  claude events: {nc} across {len(sessions)} sessions", flush=True)
    print("ingesting codex rollouts…", flush=True)
    ingest_codex(sessions)
    nt = sum(len(v) for v in sessions.values())
    print(f"  total events: {nt} across {len(sessions)} sessions (codex delta: {nt - nc})", flush=True)
    out = finalize(sessions)
    OUT_DATA.parent.mkdir(parents=True, exist_ok=True)
    OUT_UI.parent.mkdir(parents=True, exist_ok=True)
    for path in (OUT_DATA, OUT_UI):
        with path.open("w") as fh:
            json.dump(out, fh, separators=(",", ":"))
    final_events = sum(len(v) for v in out.values())
    print(f"wrote {OUT_DATA} + mirror · {len(out)} sessions · {final_events} events (after cap)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
