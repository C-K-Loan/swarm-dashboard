#!/usr/bin/env python3
"""Shared git-repo resolver. Walks up to .git, folds worktrees back to main repo.

Public API:
  resolve_repo_root(abs_path) -> Optional[str]           canonical main-repo root
  resolve_repos_from_agent_events(events) -> dict        session_id -> [repos by freq]
  classify_module(abs_path, repo_root) -> str            top-level dir inside repo
  extract_clone_hints_from_text(text) -> list[(url,dst)]
  git_remote_origin(repo_root) -> Optional[str]
  repo_name(repo_root, origin) -> str
"""
from __future__ import annotations

import os
import re
import subprocess
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

HOME = os.path.expanduser("~")
BLOCKLIST_SEGMENTS = (
    "/node_modules/",
    "/.venv/",
    "/dist/",
    "/build/",
    "/__pycache__/",
    "/.cache/",
    "/.git/",
    "/.pytest_cache/",
    "/.mypy_cache/",
)
# Privacy guard: names and paths come from the local, untracked blocklist file (see _privacy_blocklist.py).
from _privacy_blocklist import (  # noqa: E402
    BLOCKED_PATH_PREFIXES as PRIVACY_LOCKED,
    BLOCKED_REPO_NAMES as PRIVACY_REPO_BASENAMES,
    BLOCKED_REPO_PREFIXES as PRIVACY_BASENAME_PREFIXES,
    BLOCKED_FILENAME_SUBSTRINGS as PRIVACY_FILENAME_KEYWORDS,
)
PRIVACY_PATH_SUBSTRINGS = tuple(sorted(f"/{n}/" for n in PRIVACY_REPO_BASENAMES))
STOP_AT = ("/", HOME)


def _is_privacy_locked(path: str) -> bool:
    """Strict zero-leak policy: any path that mentions a blocked project term
    anywhere — as a substring, in any case — is blocked."""
    if not path:
        return False
    s = path if path.endswith("/") else path + "/"
    if any(s.startswith(p) for p in PRIVACY_LOCKED):
        return True
    low = path.lower()
    # Basename-prefix families anywhere in the path.
    if any(p in low for p in PRIVACY_BASENAME_PREFIXES):
        return True
    # Substring match on all filename-keyword tokens — catches path components
    # like "some-<name>/" or "pre-<name>-x".
    if any(k in low for k in PRIVACY_FILENAME_KEYWORDS):
        return True
    return False


def _is_blocked_repo_basename(repo_root: str) -> bool:
    if not repo_root:
        return False
    # Reject anything inside a .git/ internal directory (submodule-worktree
    # edge case produces /.../mono/.git/modules/... which is NOT a working tree).
    if "/.git/" in repo_root or repo_root.endswith("/.git"):
        return True
    # Any ancestor segment that is a blocked basename or starts with a blocked
    # prefix → block the whole candidate (catches worktrees whose main repo is
    # under a blocked parent like /other-project/mono/...).
    for seg in repo_root.split("/"):
        seg_low = seg.lower()
        if not seg_low:
            continue
        if seg_low in PRIVACY_REPO_BASENAMES:
            return True
        if any(seg_low.startswith(p) for p in PRIVACY_BASENAME_PREFIXES):
            return True
    return False


def is_privacy_filename(name: str) -> bool:
    if not name:
        return False
    low = name.lower()
    return any(k in low for k in PRIVACY_FILENAME_KEYWORDS)


def contains_privacy_term(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    if any(k in low for k in PRIVACY_FILENAME_KEYWORDS):
        return True
    return False


def _is_blocklisted(path: str) -> bool:
    s = path if path.endswith("/") else path + "/"
    return any(seg in s for seg in BLOCKLIST_SEGMENTS)


@lru_cache(maxsize=8192)
def _find_dot_git_from_dir(dir_path: str) -> Optional[str]:
    """Walk upward from dir_path returning the first '.git' entry path, or None."""
    try:
        cur = Path(dir_path).resolve(strict=False)
    except (OSError, RuntimeError):
        cur = Path(dir_path)
    while True:
        sc = str(cur)
        if _is_privacy_locked(sc):
            return None
        g = cur / ".git"
        try:
            exists = g.exists()
        except OSError:
            exists = False
        if exists:
            return str(g)
        if sc in STOP_AT or sc == "/":
            return None
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


@lru_cache(maxsize=8192)
def resolve_repo_root(abs_path: str) -> Optional[str]:
    """Return the canonical main-repo root for a given absolute path.

    Worktrees fold back to their main repo. Returns None if no .git found,
    the path is privacy-locked, or lives inside a blocklisted dir chain.
    """
    if not abs_path:
        return None
    p = Path(abs_path)
    sp = str(p)
    if _is_privacy_locked(sp) or _is_blocklisted(sp):
        return None
    try:
        if p.exists():
            start = p.parent if p.is_file() else p
        else:
            # Not locally present (e.g. remote host path). Treat as dir-like unless clearly a file.
            start = p.parent if "." in p.name and "/" not in p.name else p
    except OSError:
        start = p
    git_path = _find_dot_git_from_dir(str(start))
    if not git_path:
        return None
    g = Path(git_path)
    try:
        is_dir = g.is_dir()
    except OSError:
        is_dir = False
    if is_dir:
        candidate = str(g.parent)
        if _is_blocked_repo_basename(candidate):
            return None
        return candidate
    # .git is a FILE (worktree pointer)
    try:
        text = g.read_text(errors="replace")
    except OSError:
        return str(g.parent)
    m = re.search(r"gitdir:\s*(\S+)", text)
    if not m:
        candidate = str(g.parent)
        if _is_blocked_repo_basename(candidate):
            return None
        return candidate
    gitdir_str = m.group(1)
    gitdir = Path(gitdir_str)
    if not gitdir.is_absolute():
        gitdir = (g.parent / gitdir_str).resolve(strict=False)
    # Try commondir file (preferred)
    commondir_file = gitdir / "commondir"
    try:
        if commondir_file.exists():
            cd = commondir_file.read_text().strip()
            cd_path = Path(cd)
            if not cd_path.is_absolute():
                cd_path = (gitdir / cd).resolve(strict=False)
            # cd_path points at '/real/repo/.git'
            candidate = str(cd_path.parent)
            if _is_blocked_repo_basename(candidate):
                return None
            return candidate
    except OSError:
        pass
    # Fallback: strip '/worktrees/<name>' from gitdir
    sgd = str(gitdir)
    if "/worktrees/" in sgd:
        main_git = sgd.split("/worktrees/")[0]  # '/real/repo/.git'
        candidate = str(Path(main_git).parent)
    else:
        candidate = str(gitdir.parent)
    if _is_blocked_repo_basename(candidate):
        return None
    return candidate


def classify_module(abs_path: str, repo_root: str) -> str:
    """Return the top-level dir inside repo_root. 'root' for files directly under repo_root."""
    if not abs_path or not repo_root:
        return "root"
    try:
        rp = Path(repo_root).resolve(strict=False)
        ap = Path(abs_path).resolve(strict=False)
        rel = ap.relative_to(rp)
    except (ValueError, OSError, RuntimeError):
        p = str(Path(abs_path))
        r = str(Path(repo_root))
        if p.startswith(r + "/"):
            rel = Path(p[len(r) + 1:])
        else:
            return "root"
    parts = rel.parts
    if not parts:
        return "root"
    first = parts[0]
    if len(parts) == 1:
        return "root"
    return first


def resolve_repos_from_agent_events(events: Iterable[dict]) -> dict:
    """Group events by session_id → list of canonical repo roots sorted by frequency.

    events: iterable of dicts with keys session_id and cwd.
    """
    per_sess: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        sid = e.get("session_id")
        cwd = e.get("cwd")
        if not sid or not cwd:
            continue
        root = resolve_repo_root(cwd)
        if root:
            per_sess[sid][root] += 1
    return {sid: [r for r, _ in c.most_common()] for sid, c in per_sess.items()}


# ---------------- Clone + remote discovery ----------------

_CLONE_RE = re.compile(
    r"git\s+clone\s+(?:--[\w-]+(?:[= ][^\s]+)?\s+)*"
    r"(?P<url>(?:https?://|git@)[^\s`'\"]+?)(?:\.git)?"
    r"(?=\s|$|[;|&`'\"])"
    r"(?:\s+(?P<dst>/[^\s;|&`'\"]+))?",
)


def extract_clone_hints_from_text(text: str) -> list:
    """Return list of (url, dest_path_or_None) parsed from any 'git clone ...' lines in text."""
    out = []
    if not text:
        return out
    for m in _CLONE_RE.finditer(text):
        url = m.group("url")
        dst = m.group("dst")
        if url:
            out.append((url, dst))
    return out


@lru_cache(maxsize=1024)
def git_remote_origin(repo_root: str) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "-C", repo_root, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0:
            u = r.stdout.strip()
            return u or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def repo_name(repo_root: str, origin: Optional[str] = None) -> str:
    if origin:
        m = re.search(r"([^/:]+?)(?:\.git)?$", origin.rstrip("/"))
        if m:
            return m.group(1)
    return Path(repo_root).name
