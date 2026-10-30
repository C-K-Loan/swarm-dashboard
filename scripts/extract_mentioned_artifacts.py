#!/usr/bin/env python3
"""v13-U: grep prose JSONL for file paths and emit "mentioned artifacts".

Picks up files that:
  - Are referenced in prose text (e.g. "saved recording to /tmp/foo.mp4")
  - Are present in tool_result output but not in tool_use file-edit blocks
  - Were written by bash commands (ffmpeg, cargo run) not captured by
    extract_artifacts.py's tool_use scan

Writes data/mentioned_artifacts.json keyed by session_id, each entry:
  { ts, path, basename, kind: "media|code|other", source: "prose",
    ghost: bool, web_url: str|null }

Then merges into data/artifacts.json so the existing UI consumes them with
zero changes.
"""
import json
import os
import re
import sys
import shutil
import glob
from pathlib import Path
from collections import defaultdict

PROSE = Path("data/prose")
ARTIFACTS = Path("data/artifacts.json")
MENTIONED = Path("data/mentioned_artifacts.json")
MEDIA_DIR = Path("ui/public/artifacts")
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

MEDIA_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "webm", "mov", "mkv", "avi", "bmp", "tiff"}
CODE_EXTS = {
    "py", "rs", "md", "json", "yaml", "yml", "toml", "sh", "bash", "js", "jsx", "ts", "tsx",
    "html", "css", "scss", "astro", "vue", "svelte", "go", "c", "cpp", "h", "hpp",
    "java", "kt", "swift", "rb", "php", "lua", "nim", "zig", "sql", "tf", "hcl", "bzl", "proto",
    "env", "cfg", "ini", "dockerfile", "wasm",
}
OTHER_EXTS = {"zip", "tar.gz", "exe", "pdf", "csv", "jsonl", "parquet", "db", "sqlite"}
ALL_EXTS = MEDIA_EXTS | CODE_EXTS | OTHER_EXTS

# Match absolute paths starting with / or ~ ending with a known extension.
# Delimited by whitespace, quotes, parens, equals, backticks or line edges.
PATH_RE = re.compile(
    r"(?:^|[\s=\(\['\"`<>])"                      # left boundary (not captured in group)
    r"(?P<path>[/~][\w\-/.]+?\.(?P<ext>"
    + "|".join(sorted((e.replace(".", r"\.") for e in ALL_EXTS), key=len, reverse=True))
    + r"))"
    r"(?=$|[\s'\"`\)\],;<>:?!])",                  # right boundary lookahead (quote, punct, EOL)
    re.IGNORECASE,
)

PATH_BLOCKLIST = (
    "[REDACTED_PATH]",  # privacy
    "[REDACTED_PATH]",
    "[REDACTED_PATH]",
    "/etc/ssl/",
    "/sys/", "/proc/", "/dev/",
)


def classify_ext(ext):
    e = ext.lower()
    if e in MEDIA_EXTS: return "media"
    if e in CODE_EXTS: return "code"
    return "other"


def resolve_path(p):
    """Expand ~ and normalize."""
    if p.startswith("~"):
        p = os.path.expanduser(p)
    try:
        return os.path.realpath(p)
    except Exception:
        return p


def file_safe_name(session_id, p):
    base = os.path.basename(p)
    return f"{session_id[:8]}__mentioned__{base}"


def try_copy_media(session_id, src_path):
    """If src OR previously-copied dest exists, return a web_url. Prior-dest-wins
    so worktree cleanup after an earlier run doesn't drop valid public assets."""
    dest = MEDIA_DIR / file_safe_name(session_id, src_path)
    if dest.exists():
        return "/artifacts/" + dest.name
    try:
        if not os.path.isfile(src_path):
            return None
        if os.path.getsize(src_path) > 50 * 1024 * 1024:
            return None
    except OSError:
        return None
    try:
        shutil.copy2(src_path, dest)
    except Exception:
        return None
    return "/artifacts/" + dest.name


def main():
    by_sid = defaultdict(dict)  # sid -> { path -> record }
    stats = {"rows_scanned": 0, "paths_matched": 0, "unique_paths": 0, "ghosts": 0, "real_media": 0, "real_code": 0, "skip_block": 0}

    for p in sorted(PROSE.glob("*.jsonl")):
        for line in open(p):
            try:
                o = json.loads(line)
            except Exception:
                continue
            sid = o.get("session_id")
            ts = o.get("ts")
            text = o.get("text") or ""
            if not sid or not ts or not text:
                continue
            stats["rows_scanned"] += 1
            for m in PATH_RE.finditer(text):
                raw = m.group("path")
                stats["paths_matched"] += 1
                abs_path = resolve_path(raw)
                if any(abs_path.startswith(b) for b in PATH_BLOCKLIST):
                    stats["skip_block"] += 1
                    continue
                # Reject tiny junk like /a.py from false matches
                if len(abs_path) < 6:
                    continue
                # Dedup within session, keep earliest ts
                prev = by_sid[sid].get(abs_path)
                if prev and prev["ts"] <= ts:
                    continue
                ext = m.group("ext").lower()
                kind = classify_ext(ext)
                exists = os.path.isfile(abs_path)
                rec = {
                    "ts": ts,
                    "path": abs_path,
                    "basename": os.path.basename(abs_path),
                    "kind": kind,
                    "source": "prose",
                    "ghost": not exists,
                    "web_url": None,
                    "tool": "prose-grep",
                }
                if kind == "media":
                    url = try_copy_media(sid, abs_path)
                    if url:
                        rec["web_url"] = url
                        rec["ghost"] = False  # public asset survived even if src is gone
                        stats["real_media"] += 1
                elif kind == "code" and exists:
                    stats["real_code"] += 1
                if rec["ghost"]:
                    stats["ghosts"] += 1
                by_sid[sid][abs_path] = rec

    # Flatten + sort
    out = {}
    for sid, paths in by_sid.items():
        rows = sorted(paths.values(), key=lambda r: r["ts"])
        out[sid] = rows
        stats["unique_paths"] += len(rows)

    MENTIONED.parent.mkdir(parents=True, exist_ok=True)
    with open(MENTIONED, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"[info] rows_scanned={stats['rows_scanned']} paths_matched={stats['paths_matched']}", file=sys.stderr)
    print(f"[info] unique mentioned paths: {stats['unique_paths']}  ghost={stats['ghosts']}  real_media={stats['real_media']}  real_code={stats['real_code']}  blocked={stats['skip_block']}", file=sys.stderr)
    print(f"[done] wrote {MENTIONED}", file=sys.stderr)

    # Merge into data/artifacts.json so UI picks it up unchanged
    if ARTIFACTS.exists():
        existing = json.load(open(ARTIFACTS))
    else:
        existing = {}
    added = 0
    upgraded = 0  # ghost → real (dest-wins fix recovered a web_url)
    for sid, rows in out.items():
        bucket = existing.setdefault(sid, [])
        by_path = {r.get("path"): i for i, r in enumerate(bucket)}
        for r in rows:
            if r["path"] in by_path:
                old = bucket[by_path[r["path"]]]
                # Upgrade an existing ghost entry when the new one has a web_url
                if r.get("web_url") and not old.get("web_url"):
                    bucket[by_path[r["path"]]] = r
                    upgraded += 1
                continue
            bucket.append(r)
            by_path[r["path"]] = len(bucket) - 1
            added += 1
        bucket.sort(key=lambda x: x.get("ts", 0))
    with open(ARTIFACTS, "w") as fh:
        json.dump(existing, fh, indent=2)
    print(f"[done] merged {added} new entries, upgraded {upgraded} ghost→real, into {ARTIFACTS}", file=sys.stderr)


if __name__ == "__main__":
    main()
