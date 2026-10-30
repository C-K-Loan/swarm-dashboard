#!/usr/bin/env python3
"""Report on a v0.1.B merge tree: concat vs merged bullet counts, duplicate rate, provenance integrity, examples.

Usage: merge_report.py [--name gamejam-day1] [--examples 3]
"""
import argparse, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'v01b'
FIELDS = ('started', 'finished', 'blocked', 'key_decisions')
norm = lambda t: re.sub(r'\W+', ' ', t.lower()).strip()


def leaves_of(nodes, nid):
    """All 1h row leaf nodes under nid."""
    n = nodes[nid]
    if n['kind'] == 'row' and n['level'] == '1h': return [nid]
    return [l for c in n['children'] for l in leaves_of(nodes, c)]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--name', default='gamejam-day1'); ap.add_argument('--examples', type=int, default=3)
    a = ap.parse_args()
    tree = json.loads((ROOT / a.name / 'merge_tree.json').read_text()); nodes = tree['nodes']
    leaf_ids = {b['id'] for n in nodes.values() if n['kind'] == 'row' and n['level'] == '1h' for f in FIELDS for b in n['bullets'][f]}
    stats, bad_src = [], 0
    for nid, n in nodes.items():
        if n['kind'] == 'colgroup' or (n['kind'] == 'row' and n['level'] == '1h'): continue
        leaves = leaves_of(nodes, nid)
        concat = [b['t'] for l in leaves for f in FIELDS for b in nodes[l]['bullets'][f]]
        merged = [b for f in FIELDS for b in n['bullets'][f]]
        for b in merged:
            if not b['leaf_src'] or not set(b['leaf_src']) <= leaf_ids: bad_src += 1
        under = {x for l in leaves for f in FIELDS for x in [b['id'] for b in nodes[l]['bullets'][f]]}
        outside = sum(1 for b in merged if not set(b['leaf_src']) <= under)
        stats.append({'id': nid, 'kind': n['kind'], 'level': n['level'], 'concat': len(concat), 'exact_dupes': len(concat) - len({norm(t) for t in concat}),
                      'merged': len(merged), 'merged_dupes': len(merged) - len({norm(b['t']) for b in merged}), 'outside_subtree': outside,
                      'covered_leaf': len({x for b in merged for x in b['leaf_src']}), 'fallback': n.get('fallback', False)})
    print('meta', json.dumps(tree['meta']))
    for kind in ('row', 'col'):
        for lvl in ('1h', '4h', 'day'):
            s = [x for x in stats if x['kind'] == kind and x['level'] == lvl]
            if not s: continue
            c, m = sum(x['concat'] for x in s), sum(x['merged'] for x in s)
            print(f"{kind:3} {lvl:3} nodes={len(s):3} concat={c:5} merged={m:4} ratio={m / max(c, 1):.2f} "
                  f"concat_exact_dupes={sum(x['exact_dupes'] for x in s)} merged_dupes={sum(x['merged_dupes'] for x in s)} "
                  f"leaf_coverage={sum(x['covered_leaf'] for x in s) / max(c, 1):.2f} fallbacks={sum(x['fallback'] for x in s)} outside_subtree={sum(x['outside_subtree'] for x in s)}")
    print('bullets with missing/invalid provenance:', bad_src)
    big = sorted([x for x in stats if x['kind'] == 'col'], key=lambda x: -x['concat'])[:a.examples]
    for x in big:
        n = nodes[x['id']]
        print(f"\n=== {x['id']} ({len(n.get('agents', []))} agents) concat {x['concat']} -> merged {x['merged']}")
        for f in FIELDS:
            for b in n['bullets'][f]: print(f'  [{f}] {b["t"]}  ({len(b["leaf_src"])} src)')


if __name__ == '__main__':
    main()
