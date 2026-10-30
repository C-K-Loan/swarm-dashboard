#!/usr/bin/env python3
"""v12-R: build a per-session transcript index for the dashboard.

Reads data/prose/*.jsonl, writes ui/public/data/transcript.json:
  { "<session_id>": [
      {
        "t": <ts>,
        "r": "a"|"u"|"s",      # assistant_prose / user / subagent_report
        "x": "<text>",          # first 2200 chars (longer messages truncated with a flag)
        "ag": "<agent_title>",  # per-row (one session can host subagent replies carrying another title)
        "m": "<model>",         # per-row
        "pv": "claude"|"codex", # provider
        "tc": <tool_call_count>,
        "fe": 0|1,              # file_edit flag
        "sr": 0|1,              # is_subagent_report flag
        "ph": "<phase>",        # codex phase or null
        "trunc": 0|1
      },
      ...
    ],
    ...
  }

Sorted ascending by ts within each session.
"""
import json
import glob
from pathlib import Path
from collections import defaultdict

PROSE = Path("data/prose")
OUT = Path("ui/public/data/transcript.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

MAX_LEN = 2200


def role_short(role, is_sub, msg_kind):
    if is_sub:
        return "s"
    if role == "user" or role == "user_prose":
        # v13-AA: split user into human vs a2a
        if msg_kind == "a2a":
            return "x"
        return "u"
    return "a"


def main():
    sessions = defaultdict(list)
    total = 0
    for p in sorted(PROSE.glob("*.jsonl")):
        for line in open(p):
            try:
                o = json.loads(line)
            except Exception:
                continue
            sid = o.get("session_id")
            if not sid:
                continue
            meta = o.get("meta") or {}
            text = o.get("text") or ""
            trunc = 0
            if len(text) > MAX_LEN:
                text = text[:MAX_LEN] + "…"
                trunc = 1
            rec = {
                "t": float(o.get("ts") or 0),
                "r": role_short(o.get("role"), bool(meta.get("is_subagent_report")), meta.get("msg_kind")),
                "x": text,
                "ag": o.get("agent_title") or "",
                "m": o.get("model") or "",
                "pv": o.get("provider") or "",
                "tc": int(meta.get("tool_call_count") or 0),
                "fe": 1 if meta.get("has_file_edit") else 0,
                "sr": 1 if meta.get("is_subagent_report") else 0,
                "ph": meta.get("phase") or None,
                "trunc": trunc,
                "a2a": meta.get("a2a_sender") or None,
            }
            sessions[sid].append(rec)
            total += 1
    for sid, rows in sessions.items():
        rows.sort(key=lambda r: r["t"])
    print(f"[info] indexed {total} rows across {len(sessions)} sessions")
    with open(OUT, "w") as fh:
        json.dump(sessions, fh, separators=(",", ":"))
    size_mb = OUT.stat().st_size / 1e6
    print(f"[done] wrote {OUT} ({size_mb:.1f}MB)")


if __name__ == "__main__":
    main()
