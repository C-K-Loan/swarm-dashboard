#!/usr/bin/env python3
"""Shared privacy blocklist. ALL build scripts must import + enforce.

Rules of the game:
  - BLOCKED_PATH_PREFIXES: any abs path with one of these as a prefix is blocked.
  - BLOCKED_REPO_NAMES: exact (lowercase) repo basenames to drop.
  - BLOCKED_REPO_PREFIXES: basename STARTS WITH one of these → drop.
  - BLOCKED_FILENAME_SUBSTRINGS: filename contains one of these → drop.
  - BLOCKED_CONTENT_SUBSTRINGS: any text (snippet, prose body, title…) that
    mentions one of these at any case → drop THAT row/field entirely.

Call `is_blocked_path(abs_path)` and `is_blocked_content(text)` at the
output stage of every build pipeline. If `is_blocked_path` is True the row
must not be emitted. If `is_blocked_content` is True the field/snippet
must be omitted (and if the whole row's content is tainted, drop the row).
"""
from __future__ import annotations

import os
from pathlib import Path

HOME = os.path.expanduser("~")

# Private terms live in a local, untracked file, never in this repository.
# One entry per line: "/abs/or/~/path/prefix", "repo-name", "prefix*", "content:term"; "#" starts a comment.
CONFIG = Path(os.environ.get("PRIVACY_BLOCKLIST_FILE") or Path(__file__).resolve().parents[1] / "config" / "privacy-blocklist.txt")


def load_terms(path: Path = CONFIG) -> list[str]:
    try:
        return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    except OSError:
        return []


_TERMS = load_terms()
BLOCKED_PATH_PREFIXES = tuple(os.path.expanduser(t) for t in _TERMS if t.startswith(("/", "~")))
BLOCKED_REPO_NAMES = frozenset(t.lower() for t in _TERMS if not t.startswith(("/", "~", "content:")) and not t.endswith("*"))
BLOCKED_REPO_PREFIXES = tuple(t[:-1].lower() for t in _TERMS if t.endswith("*") and not t.startswith(("/", "~")))
BLOCKED_FILENAME_SUBSTRINGS = tuple(sorted(BLOCKED_REPO_NAMES))
BLOCKED_CONTENT_SUBSTRINGS = tuple(sorted(set(BLOCKED_FILENAME_SUBSTRINGS) | {t[8:].lower() for t in _TERMS if t.startswith("content:")}))


def is_blocked_path(abs_path: str) -> bool:
    if not abs_path:
        return False
    low = abs_path.lower()
    for pre in BLOCKED_PATH_PREFIXES:
        if low.startswith(pre.lower()):
            return True
    # Any path component (segment) that matches a blocked basename / prefix / filename substring
    for seg in abs_path.split("/"):
        seg_low = seg.lower()
        if not seg_low:
            continue
        if seg_low in BLOCKED_REPO_NAMES:
            return True
        if any(seg_low.startswith(p) for p in BLOCKED_REPO_PREFIXES):
            return True
        if any(k in seg_low for k in BLOCKED_FILENAME_SUBSTRINGS):
            return True
    return False


def is_blocked_repo_name(name: str) -> bool:
    if not name:
        return False
    nl = name.lower()
    if nl in BLOCKED_REPO_NAMES:
        return True
    if any(nl.startswith(p) for p in BLOCKED_REPO_PREFIXES):
        return True
    return False


def is_blocked_filename(name: str) -> bool:
    if not name:
        return False
    nl = name.lower()
    return any(k in nl for k in BLOCKED_FILENAME_SUBSTRINGS)


def is_blocked_content(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(k in low for k in BLOCKED_CONTENT_SUBSTRINGS)
