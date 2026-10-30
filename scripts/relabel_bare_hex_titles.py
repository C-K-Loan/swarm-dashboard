#!/usr/bin/env python3
"""v11-Q2 one-off: rewrite data/prose/*.jsonl in place, remapping any
agent_title that is a bare 8-char hex to `claude-<project>-<sid[-8:]>`.
Preserves the original value as meta.agent_title_raw.

Only touches provider=='claude' rows (codex side already handled by
extract_codex_prose.py's full title resolver).
"""
import json
import re
import sys
from pathlib import Path

PROSE = Path("data/prose")
BARE_HEX = re.compile(r"^[0-9a-f]{8}$")


def main():
    total_rows = 0
    total_rewritten = 0
    per_file = {}
    for p in sorted(PROSE.glob("*.jsonl")):
        rewritten = 0
        rows = 0
        out_lines = []
        with open(p) as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    out_lines.append(line)
                    continue
                rows += 1
                if o.get("provider") == "claude":
                    at = o.get("agent_title") or ""
                    if BARE_HEX.match(at):
                        sid = o.get("session_id") or ""
                        proj = o.get("project") or "unknown"
                        new = f"claude-{proj}-{(sid[-8:] or 'unknown')}"
                        o.setdefault("meta", {})["agent_title_raw"] = at
                        o["agent_title"] = new
                        rewritten += 1
                out_lines.append(json.dumps(o) + "\n")
        if rewritten:
            p.write_text("".join(out_lines))
            per_file[p.name] = (rows, rewritten)
        total_rows += rows
        total_rewritten += rewritten

    print(f"[done] scanned {total_rows} rows across {len(list(PROSE.glob('*.jsonl')))} files")
    print(f"[done] rewrote {total_rewritten} bare-hex titles")
    for name, (rows, rw) in sorted(per_file.items(), key=lambda x: -x[1][1]):
        print(f"  {rw:>5} / {rows:>6}  {name}")


if __name__ == "__main__":
    main()
