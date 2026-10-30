#!/usr/bin/env python3
"""Deterministic artifact extraction from raw agent transcripts.

Walks Claude .jsonl and Codex rollout-*.jsonl for file writes/edits, classifies
by extension (MEDIA / CODE / OTHER), copies media files to ui/public/artifacts/,
and emits data/artifacts.json keyed by session_id.

usage: extract_artifacts.py <classified_dir>
       (reads bundle paths from [REDACTED_PATH])
"""
import json
import os
import shutil
import sys
from pathlib import Path
from collections import defaultdict


BUNDLE_DIR = Path("[REDACTED_PATH]")
OUT_PATH = Path("data/artifacts.json")
MEDIA_DIR = Path("ui/public/artifacts")

MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".webm", ".mov", ".bmp", ".tiff"}
CODE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".c", ".cpp", ".h", ".hpp",
    ".md", ".json", ".yaml", ".yml", ".toml", ".sh", ".bash", ".zsh",
    ".astro", ".vue", ".svelte", ".html", ".css", ".scss", ".sql",
    ".java", ".kt", ".swift", ".rb", ".php", ".lua", ".nim", ".zig",
    ".txt", ".cfg", ".ini", ".env", ".dockerfile", ".dockerignore",
    ".tf", ".hcl", ".bzl", ".proto",
}

LOCAL_HOST = os.uname().nodename
CLAUDE_EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Create", "str_replace_editor"}
CODEX_EDIT_NAMES = {"write_file", "apply_patch", "str_replace_editor", "edit_file", "create_file"}


def ts_from(s):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def classify_path(path):
    ext = Path(path).suffix.lower()
    if ext in MEDIA_EXT:
        return "media"
    if ext in CODE_EXT:
        return "code"
    return "other"


def file_safe_name(session_id, p):
    """Build safe destination name for public artifacts dir."""
    base = Path(p).name
    return f"{session_id[:8]}__{base}"


def try_copy_media(session_id, src_path, out_dir):
    """Copy media file into ui/public/artifacts/. Returns web URL or None on failure."""
    if not os.path.exists(src_path):
        return None
    try:
        size = os.path.getsize(src_path)
    except Exception:
        return None
    if size > 10 * 1024 * 1024:  # cap at 10 MB
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / file_safe_name(session_id, src_path)
    try:
        if not dest.exists():
            shutil.copy2(src_path, dest)
        return f"/artifacts/{dest.name}"
    except Exception as e:
        print(f"[warn] copy failed: {src_path} -> {dest}: {e}", file=sys.stderr)
        return None


def extract_from_claude(session_path, session_id, project_dir=None):
    """Walk Claude .jsonl for tool_use file edits. Yields {ts, kind, path, tool}."""
    if not os.path.exists(session_path):
        return
    for line in open(session_path, errors="ignore"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        m = o.get("message") or {}
        if m.get("role") != "assistant":
            continue
        t = ts_from(o.get("timestamp"))
        if not t:
            continue
        for c in m.get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            name = c.get("name") or ""
            if name not in CLAUDE_EDIT_TOOLS:
                continue
            inp = c.get("input") or {}
            path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
            if not path:
                continue
            # Resolve absolute if given relative + we know project_dir
            if not os.path.isabs(path) and project_dir:
                path = os.path.join(project_dir, path)
            yield {
                "ts": t,
                "tool": name,
                "path": path,
                "basename": os.path.basename(path),
                "kind": classify_path(path),
            }


def extract_from_codex(session_path, session_id, project_dir=None):
    """Walk Codex rollout jsonl for function_call tool uses that write files."""
    if not os.path.exists(session_path):
        return
    for line in open(session_path, errors="ignore"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "response_item":
            continue
        p = o.get("payload") or {}
        if p.get("type") not in ("function_call", "custom_tool_call"):
            continue
        name = p.get("name") or ""
        if name not in CODEX_EDIT_NAMES:
            continue
        t = ts_from(o.get("timestamp"))
        if not t:
            continue
        args_raw = p.get("arguments") or p.get("input") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except Exception:
            continue
        path = args.get("path") or args.get("file_path")
        if not path:
            # apply_patch has `patch` text containing @@ paths
            patch = args.get("patch") or args.get("content")
            if isinstance(patch, str):
                import re
                for m2 in re.finditer(r"(?:\*\*\*\s+)?(?:Update|Add) File:\s*(\S+)", patch):
                    path = m2.group(1)
                    break
        if not path:
            continue
        if not os.path.isabs(path) and project_dir:
            path = os.path.join(project_dir, path)
        yield {
            "ts": t,
            "tool": name,
            "path": path,
            "basename": os.path.basename(path),
            "kind": classify_path(path),
        }


def main():
    # Collect all session paths from bundles
    classified_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/classified")
    projects = sorted({p.stem.split("__", 1)[0] for p in classified_dir.glob("*.jsonl")})
    print(f"[info] projects: {projects}", file=sys.stderr)

    out = {}  # session_id -> [{ts, tool, path, basename, kind, web_url?}]
    total_paths = 0
    media_copied = 0
    media_missing = 0

    for name in projects:
        bundle = BUNDLE_DIR / f"{name}.json"
        if not bundle.exists():
            continue
        data = json.load(open(bundle))
        project_dir = (data.get("related") or {}).get("projectDir")
        for s in data["sessions"]:
            sid = s.get("id")
            if not sid:
                continue
            if s.get("host") and s.get("host") != LOCAL_HOST and s.get("host") != "workstation":
                continue  # skip remote-only sessions (would need ssh)
            spath = s.get("path")
            if not spath or ":" in spath.split("/")[0]:
                continue
            provider = s.get("provider")
            if provider == "claude":
                arts = list(extract_from_claude(spath, sid, project_dir=project_dir))
                # Also walk subagents dir if present
                subdir = s.get("subagentsDir")
                if subdir and os.path.isdir(subdir):
                    import glob
                    for subp in glob.glob(os.path.join(subdir, "**/*.jsonl"), recursive=True):
                        arts.extend(extract_from_claude(subp, sid, project_dir=project_dir))
            elif provider == "codex":
                arts = list(extract_from_codex(spath, sid, project_dir=project_dir))
            else:
                arts = []
            if not arts:
                continue
            # Enrich media with web_url
            for a in arts:
                if a["kind"] == "media":
                    url = try_copy_media(sid, a["path"], MEDIA_DIR)
                    if url:
                        a["web_url"] = url
                        media_copied += 1
                    else:
                        a["web_url"] = None
                        media_missing += 1
            # Sort by ts
            arts.sort(key=lambda x: x["ts"])
            out[sid] = arts
            total_paths += len(arts)

    print(f"[info] sessions with artifacts: {len(out)}", file=sys.stderr)
    print(f"[info] total path events: {total_paths}", file=sys.stderr)
    print(f"[info] media copied: {media_copied}", file=sys.stderr)
    print(f"[info] media missing on disk: {media_missing}", file=sys.stderr)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT_PATH, "w"), indent=2)
    print(f"[done] wrote {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
