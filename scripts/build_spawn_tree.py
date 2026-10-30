#!/usr/bin/env python3
"""Build data/spawn_tree.json — parent/child mapping across all agents.

Algorithm:
  For each agent title A, find its FIRST a2a message (role=user_prose, meta.msg_kind=a2a)
  in chronological order. The sender of that message is A's parent.
  If no a2a messages → A is a root.
  If sender is not a known agent title → A is a root (orphan).
  Cycle guard: if parent pointers cycle, break at the oldest (lowest first_ts) agent.
"""
import json
import pathlib
import sys
from collections import defaultdict

PROSE_DIR = pathlib.Path("data/prose")
OUT_DATA = pathlib.Path("data/spawn_tree.json")
OUT_UI = pathlib.Path("ui/public/data/spawn_tree.json")


def main() -> int:
    # Collect agents + their first a2a sender + their first_ts
    first_a2a_sender: dict[str, tuple[float, str]] = {}  # title -> (ts, sender)
    first_ts: dict[str, float] = {}
    for p in sorted(PROSE_DIR.glob("*.jsonl")):
        with p.open() as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                title = r.get("agent_title")
                if not title:
                    continue
                ts = r.get("ts")
                if isinstance(ts, (int, float)):
                    if title not in first_ts or ts < first_ts[title]:
                        first_ts[title] = ts
                meta = r.get("meta") or {}
                if meta.get("msg_kind") != "a2a":
                    continue
                if r.get("role") != "user_prose":
                    continue
                sender = meta.get("a2a_sender")
                if not sender or not isinstance(ts, (int, float)):
                    continue
                prev = first_a2a_sender.get(title)
                if prev is None or ts < prev[0]:
                    first_a2a_sender[title] = (ts, sender)

    known = set(first_ts.keys())
    parents: dict[str, str | None] = {}
    for title in sorted(known):
        info = first_a2a_sender.get(title)
        if info is None:
            parents[title] = None
            continue
        sender = info[1]
        if sender == title:  # self-reference → orphan
            parents[title] = None
            continue
        if sender not in known:
            parents[title] = None
        else:
            parents[title] = sender

    # Cycle guard: walk ancestors from each node; if we hit the node again, break
    # at the oldest member of the cycle.
    for title in list(parents.keys()):
        seen = {title}
        cur = parents.get(title)
        chain = [title]
        while cur is not None:
            if cur in seen:
                # Cycle detected. chain + cur form the cycle. Find oldest in it.
                cycle_members = chain[chain.index(cur):] + [cur] if cur in chain else [cur] + chain
                # Dedupe while preserving some notion of "members"
                members = list(dict.fromkeys(cycle_members))
                oldest = min(members, key=lambda t: first_ts.get(t, float("inf")))
                print(f"[warn] spawn cycle broken at {oldest!r}: {members}", file=sys.stderr)
                parents[oldest] = None
                break
            seen.add(cur)
            chain.append(cur)
            cur = parents.get(cur)

    roots = sorted([t for t, p in parents.items() if p is None], key=lambda t: first_ts.get(t, float("inf")))

    out = {
        "parents": parents,
        "roots": roots,
        "stats": {
            "agents": len(parents),
            "roots": len(roots),
            "children": sum(1 for v in parents.values() if v is not None),
        },
    }
    OUT_DATA.parent.mkdir(parents=True, exist_ok=True)
    OUT_UI.parent.mkdir(parents=True, exist_ok=True)
    for path in (OUT_DATA, OUT_UI):
        with path.open("w") as fh:
            json.dump(out, fh, separators=(",", ":"))
    print(f"wrote {OUT_DATA} + mirror · {out['stats']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
