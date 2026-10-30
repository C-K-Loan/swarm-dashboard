#!/usr/bin/env python3
"""Discover media files (mp4/png/webm/svg/jpg) in project output dirs and attribute
them to sessions via mtime overlap. Also parses the WoWCoD dev diary HTML for
human-authored captions.

Reads existing data/artifacts.json, merges filesystem-discovered media entries
keyed by session_id, writes back to data/artifacts.json.
"""
import json
import os
import re
import sys
import glob
from pathlib import Path
from html.parser import HTMLParser


BUNDLE_DIR = Path("[REDACTED_PATH]")
ARTIFACTS_PATH = Path("data/artifacts.json")
DIARY_PATH = Path("[REDACTED_PATH]")

MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".webm", ".mov"}

# Per-project output dirs to scan (relative to projectDir, "**" means recursive)
# Served via symlinks in ui/public/<prefix>-media -> absolute path
PROJECT_SCANS = {
    "gamejam": [
        {"dir": "dist/diary/media", "serve_prefix": "/gamejam-media"},
        {"dir": "dist/videos", "serve_prefix": None},        # too many; use diary subset
        {"dir": "dist/screenshots", "serve_prefix": None},
        {"dir": "dist/diary/shots", "serve_prefix": None},
    ],
    "other-project": [
        {"dir": "docs/screenshots", "serve_prefix": None},
    ],
    "other-project": [
        {"dir": "dash", "serve_prefix": None, "recursive": True},
        {"dir": "docs/images", "serve_prefix": None},
    ],
    "nebius-hack-dev": [
        {"dir": "ui/screenshots", "serve_prefix": None},
        {"dir": "docs/charts", "serve_prefix": None},
    ],
}


class DiaryParser(HTMLParser):
    """Parse diary cards: each `<div class=card>` -> {title, ts, media_src, caption}."""

    def __init__(self):
        super().__init__()
        self.cards = []
        self.cur_card = None
        self.in_h4 = False
        self.in_small = False
        self.in_p = False
        self.in_b_label = False
        self.cur_label = None
        self.cur_text = []
        self.depth_in_card = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and a.get("class") == "card":
            self.cur_card = {"title": "", "time_str": "", "media_src": None, "media_tag": None, "caption": {}}
            self.depth_in_card = 1
            return
        if self.cur_card is None:
            return
        if tag == "div":
            self.depth_in_card += 1
        if tag == "h4":
            self.in_h4 = True
            self.cur_text = []
        elif tag == "small" and self.in_h4:
            self.in_small = True
            self.cur_text.append(" | ")
        elif tag == "video":
            self.cur_card["media_tag"] = "video"
            self.cur_card["media_src"] = a.get("src")
        elif tag == "img":
            self.cur_card["media_tag"] = "img"
            self.cur_card["media_src"] = a.get("src")
        elif tag == "p":
            self.in_p = True
            self.cur_text = []
        elif tag == "b" and self.in_p:
            self.in_b_label = True
            self.cur_text = []

    def handle_endtag(self, tag):
        if self.cur_card is None:
            return
        if tag == "div":
            self.depth_in_card -= 1
            if self.depth_in_card == 0:
                self.cards.append(self.cur_card)
                self.cur_card = None
            return
        if tag == "h4":
            full = "".join(self.cur_text).strip()
            # "lean-polish-perf <small>fea6a58 · 10-15 05:42</small>"
            # split on " | " marker inserted at <small>
            parts = full.split(" | ", 1)
            self.cur_card["title"] = parts[0].strip()
            if len(parts) > 1:
                self.cur_card["time_str"] = parts[1].strip()
            self.in_h4 = False
        elif tag == "small":
            self.in_small = False
        elif tag == "b" and self.in_b_label:
            label = "".join(self.cur_text).strip().rstrip(":").lower()
            self.cur_label = label
            self.cur_text = []
            self.in_b_label = False
        elif tag == "p":
            if self.cur_label:
                self.cur_card["caption"][self.cur_label] = "".join(self.cur_text).strip()
                self.cur_label = None
            self.in_p = False

    def handle_data(self, data):
        if self.cur_card is None:
            return
        if self.in_h4 or self.in_p or self.in_b_label:
            self.cur_text.append(data)


def parse_diary_time(time_str, default_year=2026):
    """Parse '10-15 05:42' or 'fea6a58 · 10-15 05:42' -> ts float."""
    import datetime
    m = re.search(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", time_str or "")
    if not m:
        return None
    mo, d, h, mi = map(int, m.groups())
    try:
        return datetime.datetime(default_year, mo, d, h, mi).timestamp()
    except Exception:
        return None


def load_diary_cards():
    """Return list of {ts, title, media_src, media_tag, caption}."""
    if not DIARY_PATH.exists():
        return []
    html = DIARY_PATH.read_text(errors="ignore")
    parser = DiaryParser()
    parser.feed(html)
    out = []
    for c in parser.cards:
        ts = parse_diary_time(c["time_str"])
        if ts and c.get("media_src"):
            out.append({
                "ts": ts,
                "title": c["title"],
                "media_src": c["media_src"],
                "media_tag": c["media_tag"],
                "caption": c["caption"],
            })
    return out


def scan_dir_for_media(base_dir, recursive=False):
    """Yield (abs_path, mtime, size)."""
    if not base_dir.exists():
        return
    pattern = "**/*" if recursive else "*"
    for p in base_dir.glob(pattern):
        if not p.is_file():
            continue
        if p.suffix.lower() not in MEDIA_EXT:
            continue
        try:
            st = p.stat()
        except Exception:
            continue
        yield (str(p.resolve()), st.st_mtime, st.st_size)


def get_session_time_ranges(project_name):
    """For each session in the bundle, return (session_id, session_cct_name, start_ts, end_ts).
    Uses first/last line timestamp from the jsonl path.
    """
    bundle = BUNDLE_DIR / f"{project_name}.json"
    if not bundle.exists():
        return []
    data = json.load(open(bundle))
    out = []
    for s in data.get("sessions", []):
        sid = s.get("id")
        spath = s.get("path")
        if not sid or not spath or not os.path.exists(spath):
            continue
        # Read first+last timestamp cheaply
        start_ts = s.get("firstActivity") or s.get("firstTs")
        end_ts = s.get("lastActivity") or s.get("lastTs")
        # fallback: parse jsonl
        if not (start_ts and end_ts):
            try:
                first = None
                last = None
                for line in open(spath, errors="ignore"):
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    t = o.get("timestamp")
                    if t:
                        if first is None:
                            first = t
                        last = t
                from datetime import datetime
                def _ts(x):
                    try:
                        return datetime.fromisoformat(str(x).replace("Z","+00:00")).timestamp()
                    except Exception:
                        return None
                start_ts = _ts(first) if first else None
                end_ts = _ts(last) if last else None
            except Exception:
                pass
        if isinstance(start_ts, str):
            from datetime import datetime
            try:
                start_ts = datetime.fromisoformat(start_ts.replace("Z","+00:00")).timestamp()
            except Exception:
                start_ts = None
        if isinstance(end_ts, str):
            from datetime import datetime
            try:
                end_ts = datetime.fromisoformat(end_ts.replace("Z","+00:00")).timestamp()
            except Exception:
                end_ts = None
        if not (start_ts and end_ts):
            continue
        out.append((sid, s.get("cctName") or "", start_ts, end_ts))
    return out


def web_url_for(abs_path, serve_prefix, base_abs):
    """If file is under base_abs and we have a serve_prefix, return a URL path."""
    if not serve_prefix:
        return None
    try:
        rel = os.path.relpath(abs_path, base_abs)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return f"{serve_prefix}/{rel}"


def main():
    artifacts = {}
    if ARTIFACTS_PATH.exists():
        artifacts = json.load(open(ARTIFACTS_PATH))

    diary_cards = load_diary_cards()
    print(f"[info] diary cards parsed: {len(diary_cards)}", file=sys.stderr)
    # Build quick lookup: basename -> card
    diary_by_src = {}
    for c in diary_cards:
        src = c["media_src"]
        if src:
            # media/foo.mp4 -> foo.mp4
            key = os.path.basename(src)
            diary_by_src[key] = c

    total_media = 0
    total_attributed = 0
    media_per_session = {}

    # For each configured project
    for proj_name, scans in PROJECT_SCANS.items():
        bundle = BUNDLE_DIR / f"{proj_name}.json"
        if not bundle.exists():
            continue
        bundle_data = json.load(open(bundle))
        proj_dir = (bundle_data.get("related") or {}).get("projectDir")
        if not proj_dir:
            continue

        sessions = get_session_time_ranges(proj_name)
        if not sessions:
            continue
        print(f"[info] {proj_name}: {len(sessions)} sessions, scanning {len(scans)} dirs", file=sys.stderr)

        # Collect all media files for this project
        all_media = []
        for scan in scans:
            base = Path(proj_dir) / scan["dir"]
            for abs_path, mtime, size in scan_dir_for_media(base, recursive=scan.get("recursive", False)):
                all_media.append({
                    "abs_path": abs_path,
                    "mtime": mtime,
                    "size": size,
                    "serve_prefix": scan["serve_prefix"],
                    "base_abs": str(base.resolve()),
                })
        total_media += len(all_media)
        print(f"[info]   found {len(all_media)} media files", file=sys.stderr)

        # Attribute each media to the session whose window covers mtime (prefer the longest window containing it; else closest).
        # Use a 2-min grace on each side so a file flushed just after session end still attaches.
        GRACE = 120
        for m in all_media:
            best = None
            for sid, cct_name, s_start, s_end in sessions:
                if s_start - GRACE <= m["mtime"] <= s_end + GRACE:
                    # Score by span (prefer tighter containment)
                    span = s_end - s_start
                    if best is None or span < best[1]:
                        best = (sid, span, cct_name)
            if best is None:
                continue
            sid = best[0]
            basename = os.path.basename(m["abs_path"])
            card = diary_by_src.get(basename)
            caption_parts = []
            if card:
                for k in ("seeing", "tested", "result"):
                    v = (card["caption"] or {}).get(k)
                    if v:
                        caption_parts.append(f"{k}: {v}")
            caption = " · ".join(caption_parts) if caption_parts else (card["title"] if card else basename)
            web_url = web_url_for(m["abs_path"], m["serve_prefix"], m["base_abs"])
            entry = {
                "ts": m["mtime"],
                "tool": "fs-media",
                "path": m["abs_path"],
                "basename": basename,
                "kind": "media",
                "size": m["size"],
                "web_url": web_url,
                "caption": caption,
                "diary_title": card["title"] if card else None,
            }
            artifacts.setdefault(sid, []).append(entry)
            media_per_session[sid] = media_per_session.get(sid, 0) + 1
            total_attributed += 1

    print(f"[info] total media found: {total_media}", file=sys.stderr)
    print(f"[info] attributed to sessions: {total_attributed}", file=sys.stderr)
    top = sorted(media_per_session.items(), key=lambda x: -x[1])[:5]
    print(f"[info] top sessions by media: {top}", file=sys.stderr)

    # Re-sort each session by ts
    for sid in artifacts:
        artifacts[sid].sort(key=lambda x: x.get("ts", 0))

    ARTIFACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    json.dump(artifacts, open(ARTIFACTS_PATH, "w"), indent=2)
    print(f"[done] wrote {ARTIFACTS_PATH} ({len(artifacts)} sessions)", file=sys.stderr)


if __name__ == "__main__":
    main()
