#!/usr/bin/env python3
"""Build data/repo_registry.json + data/tree.json (+ UI mirrors).

Inputs:
  data/file_diffs.json  (per-session file-mutation events, must exist first)
  data/timeline.json    (session_id -> agent title mapping)

Repo registry: one entry per canonical repo root an agent touched. Includes
origin URL + name + first/last touch + agents who touched it.

Tree: walks each repo filesystem (depth-limited, blocklisted dirs skipped),
collects files with line counts + mtime, joins with file_diffs events to
annotate first_ts / last_ts / kinds / event_count per file. Caps at
5000 files per repo (newest last_ts wins).
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import defaultdict
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _git_repo_resolver import (
    classify_module,
    git_remote_origin,
    repo_name,
    _is_blocklisted,
    _is_privacy_locked,
    is_privacy_filename,
)

ROOT = pathlib.Path(".")
FILE_DIFFS = ROOT / "data" / "file_diffs.json"
TIMELINE = ROOT / "data" / "timeline.json"
OUT_REG_DATA = ROOT / "data" / "repo_registry.json"
OUT_REG_UI = ROOT / "ui" / "public" / "data" / "repo_registry.json"
OUT_TREE_DATA = ROOT / "data" / "tree.json"
OUT_TREE_UI = ROOT / "ui" / "public" / "data" / "tree.json"

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build",
    "__pycache__", ".cache", ".pytest_cache", ".mypy_cache",
    "target", ".next", ".turbo", ".astro", "coverage", ".idea",
}
MAX_FILES_PER_REPO = 5000
MAX_FILE_SIZE_FOR_LINECOUNT = 1_500_000  # ~1.5 MB
TEXT_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".kt",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".scala",
    ".cs", ".sh", ".bash", ".zsh", ".fish", ".lua", ".pl", ".sql",
    ".astro", ".vue", ".svelte", ".html", ".htm", ".css", ".scss", ".sass",
    ".less", ".md", ".mdx", ".rst", ".txt", ".json", ".jsonl", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".dockerfile",
    ".gitignore", ".nix", ".tf", ".hcl",
}


def _is_text_file(path: pathlib.Path) -> bool:
    if path.suffix.lower() in TEXT_EXTS:
        return True
    # Permit extensionless shell-ish files if small
    if not path.suffix and path.is_file():
        try:
            if path.stat().st_size < 64_000:
                return True
        except OSError:
            return False
    return False


def _count_lines(path: pathlib.Path) -> int:
    try:
        if path.stat().st_size > MAX_FILE_SIZE_FOR_LINECOUNT:
            return -1
    except OSError:
        return -1
    try:
        with path.open("rb") as fh:
            n = 0
            for _ in fh:
                n += 1
            return n
    except OSError:
        return -1


def _walk_repo(repo_root: pathlib.Path, cap: int = MAX_FILES_PER_REPO) -> list:
    """Yield (abs_path, lines, mtime) for text files, pruning blocklisted dirs."""
    out = []
    if not repo_root.exists():
        return out
    for sub in repo_root.rglob("*"):
        try:
            # Prune via skip list
            parts = sub.parts
            if any(seg in SKIP_DIRS for seg in parts):
                continue
            if _is_privacy_locked(str(sub)):
                continue
            # Filename-level privacy guard — catches data/bundles/[REDACTED].json etc.
            if is_privacy_filename(sub.name):
                continue
            # Also block any parent dirname matching privacy keyword
            if any(is_privacy_filename(seg) for seg in parts):
                continue
            if not sub.is_file():
                continue
            if sub.is_symlink():
                continue
            if not _is_text_file(sub):
                continue
            try:
                st = sub.stat()
            except OSError:
                continue
            lines = _count_lines(sub)
            if lines < 0:
                continue
            out.append((str(sub), lines, st.st_mtime))
            if len(out) >= cap * 2:  # over-collect to allow trimming by last_ts later
                break
        except OSError:
            continue
    return out


def main() -> int:
    if not FILE_DIFFS.exists():
        print(f"ERROR: {FILE_DIFFS} missing — run build_file_diffs_index.py first", file=sys.stderr)
        return 1
    print("loading file_diffs + timeline…", flush=True)
    diffs = json.loads(FILE_DIFFS.read_text())
    try:
        timeline = json.loads(TIMELINE.read_text())
        sid_to_title = {a["id"]: a.get("title") or "" for a in timeline.get("agents", [])}
    except Exception:
        sid_to_title = {}

    # Build repo_registry: aggregate across all events
    registry: dict[str, dict] = {}
    # Also index: repo -> { path: {first_ts, last_ts, event_count, kinds (set)} }
    per_repo_file_events: dict[str, dict[str, dict]] = defaultdict(dict)

    for sid, events in diffs.items():
        agent_title = sid_to_title.get(sid)
        for ev in events:
            repo = ev.get("repo")
            path = ev.get("path")
            ts = ev.get("ts")
            kind = ev.get("kind")
            if not repo or not path or ts is None:
                continue
            r = registry.setdefault(repo, {
                "name": None,
                "origin": None,
                "worktrees": [],
                "first_seen_ts": ts,
                "last_touch_ts": ts,
                "touched_by_agents": set(),
            })
            r["first_seen_ts"] = min(r["first_seen_ts"], ts)
            r["last_touch_ts"] = max(r["last_touch_ts"], ts)
            if agent_title:
                r["touched_by_agents"].add(agent_title)

            f = per_repo_file_events[repo].setdefault(path, {
                "first_ts": ts,
                "last_ts": ts,
                "event_count": 0,
                "kinds": set(),
            })
            f["first_ts"] = min(f["first_ts"], ts)
            f["last_ts"] = max(f["last_ts"], ts)
            f["event_count"] += 1
            f["kinds"].add(kind)

    # Finalize registry entries
    reg_out: dict[str, dict] = {}
    for repo, info in registry.items():
        origin = git_remote_origin(repo)
        name = repo_name(repo, origin)
        reg_out[repo] = {
            "name": name,
            "origin": origin,
            "worktrees": info["worktrees"],
            "first_seen_ts": round(info["first_seen_ts"], 3),
            "last_touch_ts": round(info["last_touch_ts"], 3),
            "touched_by_agents": sorted(info["touched_by_agents"]),
        }

    OUT_REG_DATA.parent.mkdir(parents=True, exist_ok=True)
    OUT_REG_UI.parent.mkdir(parents=True, exist_ok=True)
    for p in (OUT_REG_DATA, OUT_REG_UI):
        p.write_text(json.dumps(reg_out, separators=(",", ":")))
    print(f"wrote repo_registry · {len(reg_out)} repos", flush=True)

    # Build tree.json: walk each repo fs
    tree_repos: dict[str, dict] = {}
    print(f"walking filesystems for {len(reg_out)} repos…", flush=True)
    for repo, info in reg_out.items():
        rp = pathlib.Path(repo)
        if not rp.exists():
            continue
        fs_files = _walk_repo(rp)
        # Enrich each file with event info (if present)
        file_evt = per_repo_file_events.get(repo, {})
        enriched = []
        for abs_path, lines, mtime in fs_files:
            fe = file_evt.get(abs_path)
            entry = {
                "path": abs_path,
                "lines": lines,
                "mtime": round(mtime, 3),
            }
            if fe:
                entry["first_ts"] = round(fe["first_ts"], 3)
                entry["last_ts"] = round(fe["last_ts"], 3)
                entry["event_count"] = fe["event_count"]
                entry["kinds"] = sorted(fe["kinds"])
            enriched.append(entry)
        # Cap by last_ts desc (then mtime for untouched files)
        enriched.sort(key=lambda e: (e.get("last_ts", 0), e["mtime"]), reverse=True)
        enriched = enriched[:MAX_FILES_PER_REPO]
        # Group into modules
        modules: dict[str, dict] = {}
        for entry in enriched:
            mod = classify_module(entry["path"], repo)
            m = modules.setdefault(mod, {"files": [], "total_lines": 0})
            # Record relative path for UI compactness
            rel = entry["path"][len(repo) + 1:] if entry["path"].startswith(repo + "/") else entry["path"]
            m["files"].append({
                "path": rel,
                "lines": entry["lines"],
                "mtime": entry["mtime"],
                **({"first_ts": entry["first_ts"]} if "first_ts" in entry else {}),
                **({"last_ts": entry["last_ts"]} if "last_ts" in entry else {}),
                **({"event_count": entry["event_count"]} if "event_count" in entry else {}),
                **({"kinds": entry["kinds"]} if "kinds" in entry else {}),
            })
            m["total_lines"] += max(0, entry["lines"])

        total_files = sum(len(m["files"]) for m in modules.values())
        total_lines = sum(m["total_lines"] for m in modules.values())
        tree_repos[repo] = {
            "name": info["name"],
            "origin": info["origin"],
            "total_files": total_files,
            "total_lines": total_lines,
            "modules": modules,
        }

    tree_out = {"repos": tree_repos}
    OUT_TREE_DATA.parent.mkdir(parents=True, exist_ok=True)
    OUT_TREE_UI.parent.mkdir(parents=True, exist_ok=True)
    for p in (OUT_TREE_DATA, OUT_TREE_UI):
        p.write_text(json.dumps(tree_out, separators=(",", ":")))
    nf = sum(r["total_files"] for r in tree_repos.values())
    nl = sum(r["total_lines"] for r in tree_repos.values())
    print(f"wrote tree · {len(tree_repos)} repos · {nf} files · {nl} lines", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
