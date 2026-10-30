#!/usr/bin/env python3
"""Import the gamejam dev-diary media manifest into dashboard artifacts.

Pulls [REDACTED_URL] (served by gamejam-master
on data-host). Fixes the "bash-written files no transcript names" gap: thousands of
build/ffmpeg outputs live in the diary's /media/ library but are never typed
in prose, so our transcript-grep extractors can't find them.

Writes into data/artifacts.json under a synthetic session_id "gamejam-devdiary".
Also injects a synthetic agent into data/timeline.json so fleet=gamejam row
expansion includes this sid.

Idempotent: strips any prior "gamejam-devdiary" entries before merging.

Usage:
  python3 scripts/import_devdiary_manifest.py
"""
import datetime
import os
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST_URL = os.environ.get("DEVDIARY_MANIFEST_URL", "")
ARTIFACTS = ROOT / "data" / "artifacts.json"
TIMELINE = ROOT / "data" / "timeline.json"
UI_ARTIFACTS = ROOT / "ui" / "public" / "data" / "artifacts.json"
UI_TIMELINE = ROOT / "ui" / "public" / "data" / "timeline.json"
SID = "gamejam-devdiary"
FLEET = "gamejam"



GAP_SEC = 900  # files closer than one 15min bucket share a segment
MIN_SEC = 60


def media_segments(tss: list[float]) -> list[dict]:
    """One "working" segment per burst of media files; turn_count counts files, not turns.
    Whole-day blocks drew the row from 00:00 even when the first file came hours later."""
    segments: list[dict] = []
    for ts in sorted(tss):
        if segments and ts - segments[-1]["end_ts"] <= GAP_SEC:
            seg = segments[-1]
            seg["end_ts"] = max(seg["end_ts"], ts + MIN_SEC)
            seg["turn_count"] += 1
        else:
            segments.append({"start_ts": ts, "end_ts": ts + MIN_SEC, "state": "working", "turn_count": 1,
                             "blocker_type": None, "urgency": None, "user_intent": None,
                             "agent_intent": None, "agent_role": "dev"})
    for seg in segments:
        seg["duration_sec"] = seg["end_ts"] - seg["start_ts"]
    return segments

def iso_to_ts(s: str) -> float | None:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def main() -> int:
    print(f"[fetch] {MANIFEST_URL}", flush=True)
    with urllib.request.urlopen(MANIFEST_URL, timeout=15) as r:
        manifest = json.loads(r.read())
    items = manifest.get("items", [])
    print(f"[fetch] {len(items)} items", flush=True)

    # Pair every video with its sibling image (same stem, .png/.jpg/.webp) as a
    # poster so the dashboard renders the diary's pre-generated frame instead of
    # the browser's generic black box. Playback still starts at 0.
    IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    VIDEO_EXTS = (".mp4", ".webm", ".mov")
    poster_by_stem: dict[str, str] = {}
    for it in items:
        b = it.get("basename") or ""
        for ext in IMAGE_EXTS:
            if b.lower().endswith(ext):
                stem = b[: -len(ext)]
                poster_by_stem[stem] = it.get("url")
                break

    # Build artifact entries
    entries = []
    for it in items:
        basename = it.get("basename")
        url = it.get("url")
        ts = iso_to_ts(it.get("mtime"))
        if not basename or not url or ts is None:
            continue
        kind = "media"  # manifest tags all as video|image → bucket as media
        canonical = it.get("canonical_path") or basename
        poster_url = None
        lower = basename.lower()
        if any(lower.endswith(e) for e in VIDEO_EXTS):
            for e in VIDEO_EXTS:
                if lower.endswith(e):
                    stem = basename[: -len(e)]
                    poster_url = poster_by_stem.get(stem)
                    break
        entries.append({
            "ts": round(ts, 3),
            "tool": "devdiary-manifest",
            "path": canonical,
            "basename": basename,
            "kind": kind,
            "web_url": url,
            "poster_url": poster_url,
            "source": "devdiary",
            "ghost": False,
            "size": it.get("size"),
            "diary_day": it.get("diary_day"),
            "diary_title": it.get("diary_title"),
        })
    entries.sort(key=lambda e: e["ts"])
    print(f"[fetch] {len(entries)} valid entries", flush=True)

    # Patch artifacts.json
    arts = json.loads(ARTIFACTS.read_text()) if ARTIFACTS.exists() else {}
    arts[SID] = entries
    ARTIFACTS.write_text(json.dumps(arts, indent=2))
    UI_ARTIFACTS.parent.mkdir(parents=True, exist_ok=True)
    UI_ARTIFACTS.write_text(json.dumps(arts))
    print(f"[write] {ARTIFACTS} + mirror", flush=True)

    # Patch timeline.json: inject a synthetic agent whose segments follow real media times.
    tl = json.loads(TIMELINE.read_text())
    tl["agents"] = [a for a in tl.get("agents", []) if a.get("id") != SID]
    segments = media_segments([e["ts"] for e in entries])
    synth_agent = {
        "id": SID,
        "title": "gamejam-devdiary",
        "fleet": FLEET,
        "project": "gamejam",
        "provider": "devdiary",
        "model": "n/a",
        "host": "data-host",
        "total_turns": len(entries),
        "total_cost_usd": 0,
        "segments": segments,
    }
    tl["agents"].append(synth_agent)
    TIMELINE.write_text(json.dumps(tl))
    UI_TIMELINE.parent.mkdir(parents=True, exist_ok=True)
    UI_TIMELINE.write_text(json.dumps(tl))
    print(f"[write] {TIMELINE} + mirror · injected {SID} agent with {len(segments)} media segments", flush=True)
    print(f"[done] fleet=gamejam now exposes {len(entries)} dev-diary media via synthetic sid", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
