#!/usr/bin/env python3
"""Convert data/bucket_summaries.jsonl to ui/public/data/bucket_summaries.json
keyed by `${sid}|${bucket_start}` for fast O(1) UI lookup."""
import json
import os
import tempfile
from pathlib import Path

SRC = Path("data/bucket_summaries.jsonl")
OUT = Path("ui/public/data/bucket_summaries.json")


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    idx = {}
    kept = 0
    skipped = 0
    with SRC.open() as source:
        for line in source:
            try:
                o = json.loads(line)
            except Exception:
                continue
            s = o.get("summary") or {}
            if s.get("_parse_error"):
                skipped += 1
                continue
            key = f"{o['sid']}|{int(o['bucket_start'])}"
            if not any(s.get(k) for k in ("started", "finished", "blocked", "key_decisions")):
                idx.pop(key, None)  # A valid empty refresh supersedes old bullets.
                skipped += 1
                continue
            idx[key] = {
                "summary": {k: s.get(k) or [] for k in ("started", "finished", "blocked", "key_decisions") if s.get(k)},
                "grain_sec": o.get("grain_sec", 3600),
                "summary_version": o.get("summary_version", 1),
                "msg_count": o.get("msg_count") or 0,
                "project": o.get("project"),
                "agent_title": o.get("agent_title"),
            }
            kept += 1
    fd, tmp = tempfile.mkstemp(prefix=".bucket-summaries-", dir=OUT.parent)
    with os.fdopen(fd, "w") as fh:
        json.dump(idx, fh, separators=(",", ":"))
    os.replace(tmp, OUT)
    size_kb = OUT.stat().st_size / 1024
    print(f"[done] kept {kept} / skipped {skipped} → {OUT} ({size_kb:.0f}KB)")


if __name__ == "__main__":
    main()
