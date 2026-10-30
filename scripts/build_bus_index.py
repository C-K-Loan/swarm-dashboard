#!/usr/bin/env python3
"""Build per-receiver bus index for retro-detecting agent-dispatched msgs.

Scans the cct bus journal ([REDACTED_PATH] per the
harness-architect's answer) and emits data/bus_index.json keyed by receiver name:

    {
      "<receiver>": [
        {"ts": epoch_seconds, "from": "sender-name", "status": "...",
         "provenance": "...", "hash": "sha256-first16-of-prefix-stripped-body"}
      ]
    }

Client reads this and, for each transcript row with role=u, looks for a bus
row where to==receiver AND |ts - row.ts| <= 120s AND hash matches the row's
body hash (prefix-stripped). On match → relabel as a2a with sender = row.from.
"""
import hashlib
import json
import re
import time
from pathlib import Path

BUS_PATH   = Path("[REDACTED_PATH]")
OUT_PATH   = Path("data/bus_index.json")
OUT_PATH_B = Path("ui/public/data/bus_index.json")

PREFIX_RE = re.compile(r"^\s*\[from\s+[^\]]+\]\s*", re.IGNORECASE)


def iso_to_epoch(s: str) -> float:
    try:
        return time.mktime(time.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def body_prefix(text: str) -> str:
    """First 120 chars of prefix-stripped text — used as a cheap match key.
    Client compares substring equality synchronously (no SubtleCrypto needed).
    """
    return PREFIX_RE.sub("", text or "")[:120].strip()


def main():
    if not BUS_PATH.exists():
        print(f"bus file not found: {BUS_PATH}")
        return
    by_receiver: dict[str, list[dict]] = {}
    rows_seen = 0
    rows_kept = 0
    with BUS_PATH.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            rows_seen += 1
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("kind") or "") != "send":
                continue
            to = (r.get("to") or "").strip()
            if not to:
                continue
            ts = r.get("ts")
            epoch = iso_to_epoch(ts) if isinstance(ts, str) else float(ts or 0)
            if not epoch:
                continue
            entry = {
                "ts": epoch,
                "from": (r.get("from") or "").strip(),
                "status": r.get("status") or "",
                "provenance": r.get("provenance") or "",
                "prefix": body_prefix(r.get("text") or ""),
            }
            by_receiver.setdefault(to, []).append(entry)
            rows_kept += 1
    for arr in by_receiver.values():
        arr.sort(key=lambda x: x["ts"])

    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "window_sec": 120, "receivers": len(by_receiver), "rows": rows_kept,
           "data": by_receiver}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    OUT_PATH_B.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH_B.write_text(json.dumps(out, separators=(",", ":")))
    sz = OUT_PATH.stat().st_size
    print(f"scanned: {rows_seen}, kept (kind=send): {rows_kept}, receivers: {len(by_receiver)}, out size: {sz/1024/1024:.2f} MB")


if __name__ == "__main__":
    main()
