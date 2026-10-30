#!/usr/bin/env python3
"""Build the comms graph index for the "Who talks to whom" widget.

Reads ui/public/data/transcript.json + timeline.json, extracts a2a traffic
(messages whose text starts with "[from <sender>]"), aggregates into directed
(sender -> receiver) edges with msg + char counts, and writes:
  data/comms.json
  ui/public/data/comms.json

Called by live_refresh_daemon on every tick.
"""
import json, re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def guess_role(n: str) -> str:
    lc = n.lower()
    if 'review' in lc: return 'review'
    if 'master' in lc or 'planner' in lc or 'manager' in lc or 'producer' in lc: return 'coordination'
    if 'release' in lc or 'deploy' in lc or 'ship' in lc: return 'release'
    if 'qa' in lc or 'bench' in lc or 'test' in lc or 'eval' in lc: return 'qa'
    return 'dev'

FROM_RE = re.compile(r'^\[from ([^\]]+)\]')

def main():
    tx = json.loads((ROOT / 'ui/public/data/transcript.json').read_text())
    tl = json.loads((ROOT / 'ui/public/data/timeline.json').read_text())

    sid2agent = {a['id']: (a.get('title') or a['id'][:8]) for a in tl.get('agents', [])}

    pair_msgs = defaultdict(int)
    pair_chars = defaultdict(int)
    agent_vol = defaultdict(int)

    for sid, msgs in tx.items():
        to_name = sid2agent.get(sid)
        if not to_name: continue
        for m in msgs:
            if not m.get('a2a'): continue
            text = m.get('x') or m.get('m') or ''
            mm = FROM_RE.match(text)
            if not mm: continue
            from_name = mm.group(1).strip()
            if from_name == to_name: continue
            key = (from_name, to_name)
            pair_msgs[key] += 1
            pair_chars[key] += len(text)
            agent_vol[from_name] += 1
            agent_vol[to_name] += 1

    edges = sorted(
        [[f, t, n, pair_chars[(f, t)]] for (f, t), n in pair_msgs.items()],
        key=lambda e: -e[2],
    )
    nodes = sorted(
        [{'n': name, 'vol': v, 'role': guess_role(name)} for name, v in agent_vol.items()],
        key=lambda x: -x['vol'],
    )

    out = {
        'nodes': nodes,
        'edges': edges,
        'roles': ['coordination', 'dev', 'review', 'release', 'qa'],
    }
    for p in (ROOT / 'data/comms.json', ROOT / 'ui/public/data/comms.json'):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out))
    print(f'wrote comms.json · {len(nodes)} nodes · {len(edges)} edges')


if __name__ == '__main__':
    main()
