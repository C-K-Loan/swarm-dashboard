#!/usr/bin/env python3
"""Agreement of candidate v0.1.B merge trees with a ground-truth merge tree (same nodes, same leaves).

Per sampled node and candidate, a judge model (not a merger, not the GT model) compares the candidate's merged bullets with
the GT merged bullets:
- recall: each GT bullet covered / partly / missing in the candidate (partly = 0.5)
- precision: each candidate bullet "in_gt" (stated in the GT merge), "supported_only" (not in GT but supported by its cited
  source bullets), or "unsupported". precision = (in_gt + supported_only) / n; strict precision = in_gt / n.
- F1 = 2PR / (P + R) with the lenient precision.
Usage: agree_merge.py --gt gamejam-day1-haiku --cand gamejam-day1 gamejam-day1-q235b [--judge claude-sonnet-4-5-20250929]
"""
import argparse, json, random, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'v01b'
FIELDS = ('started', 'finished', 'blocked', 'key_decisions')
LEVELS = {'row-4h': ('row', '4h'), 'row-day': ('row', 'day'), 'col-1h': ('col', '1h'), 'col-4h': ('col', '4h'), 'col-day': ('col', 'day')}
SYSTEM = '''You compare two summaries of the same AI-agent work: a REFERENCE merge (ground truth) and a CANDIDATE merge.
1. For EACH reference bullet R<n>: is its fact present in the candidate? "covered", "partly" (vague or only part), or "missing".
2. For EACH candidate bullet C<n>: "in_gt" if the reference states the same fact (even worded differently or combined),
   "supported_only" if the reference does not state it but the candidate's own listed sources support it,
   "unsupported" if neither.
Return JSON {"ref": [{"n": 1, "match": "..."}], "cand": [{"n": 1, "match": "..."}]} covering every bullet.'''


def bullets(node):
    return [b for f in FIELDS for b in node['bullets'][f]]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--gt', default='gamejam-day1-haiku')
    ap.add_argument('--cand', nargs='+', default=['gamejam-day1', 'gamejam-day1-q235b'])
    ap.add_argument('--judge', default='claude-sonnet-4-5-20250929'); ap.add_argument('--per-level', type=int, default=10)
    ap.add_argument('--max-in', type=int, default=250_000); ap.add_argument('--max-out', type=int, default=120_000)
    ap.add_argument('--out', default='agreement_report.json'); ap.add_argument('--max-week-abs', type=float, default=0.24); ap.add_argument('--max-weekly-pp', type=float, default=None)
    a = ap.parse_args(); rnd = random.Random(11)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from claude_sub import ClaudeSub
    judge = ClaudeSub(a.judge, a.max_weekly_pp, max_tokens=(a.max_in, a.max_out), max_week_abs=a.max_week_abs)
    gt = json.loads((ROOT / a.gt / 'merge_tree.json').read_text())
    cands = {c: json.loads((ROOT / c / 'merge_tree.json').read_text()) for c in a.cand}
    leaf = {b['id']: b['t'] for n in gt['nodes'].values() if n['kind'] == 'row' and n['level'] == '1h' for f in FIELDS for b in n['bullets'][f]}
    jobs = []
    for lvl, (kind, level) in LEVELS.items():
        ids = sorted(k for k, n in gt['nodes'].items() if n['kind'] == kind and n['level'] == level and n.get('merged') and bullets(n)
                     and all(k in t['nodes'] for t in cands.values()))
        for k in rnd.sample(ids, min(a.per_level, len(ids))):
            for c, t in cands.items(): jobs.append((lvl, k, c))

    def run(job):
        lvl, k, c = job
        ref, cn = bullets(gt['nodes'][k]), cands[c]['nodes'][k]
        cand = [] if cn.get('fallback') else bullets(cn)  # a node the model failed to produce scores as empty, not as the concat stand-in
        if not cand:
            return lvl, k, c, len(ref), 0, [], []
        user = 'REFERENCE (ground truth):\n' + '\n'.join(f'R{i + 1}. ({b["field"]}) {b["t"]}' for i, b in enumerate(ref))
        user += '\n\nCANDIDATE:\n' + '\n'.join(f'C{i + 1}. ({b["field"]}) {b["t"]}\n   sources: ' + ' | '.join(leaf.get(s, '?') for s in b['leaf_src'][:6]) for i, b in enumerate(cand))
        for _ in range(2):
            try:
                r = judge(SYSTEM, user)
                return lvl, k, c, len(ref), len(cand), r.get('ref', []), r.get('cand', [])
            except SystemExit:
                raise
            except Exception as e:
                err = e
        print('judge failed', k, c, repr(err)[:120], file=sys.stderr)
        return lvl, k, c, len(ref), len(cand), None, None

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(run, jobs))
    agg = {}
    for lvl, k, c, nr, nc, ref, cand in rows:
        if ref is None: continue
        s = agg.setdefault((lvl, c), {'nodes': 0, 'ref': 0, 'rec': 0.0, 'cand': 0, 'in_gt': 0, 'sup': 0, 'unsup': 0, 'failed': 0})
        s['nodes'] += 1; s['failed'] += nc == 0; s['ref'] += nr; s['cand'] += nc
        s['rec'] += sum({'covered': 1, 'partly': 0.5}.get(x.get('match'), 0) for x in ref[:nr])
        s['in_gt'] += sum(x.get('match') == 'in_gt' for x in cand[:nc]); s['sup'] += sum(x.get('match') == 'supported_only' for x in cand[:nc])
        s['unsup'] += sum(x.get('match') == 'unsupported' for x in cand[:nc])
    levels = {}
    for (lvl, c), s in sorted(agg.items()):
        R = s['rec'] / max(1, s['ref']); P = (s['in_gt'] + s['sup']) / max(1, s['cand']); Ps = s['in_gt'] / max(1, s['cand'])
        levels.setdefault(lvl, {})[c] = {'nodes': s['nodes'], 'recall': round(R, 3), 'precision': round(P, 3), 'strict_precision': round(Ps, 3),
                                          'f1': round(2 * P * R / max(1e-9, P + R), 3), 'unsupported': round(s['unsup'] / max(1, s['cand']), 3),
                                          'gt_bullets': s['ref'], 'cand_bullets': s['cand'], 'failed_nodes': s['failed']}
    out = {'gt': a.gt, 'gt_model': gt['meta']['model'], 'candidates': {c: t['meta']['model'] for c, t in cands.items()}, 'judge': a.judge,
           'judge_note': 'judge is not a merger and not the GT model, but is the same vendor (Anthropic) as the GT model',
           'judge_usage': judge.usage, 'judge_calls': judge.calls, 'weekly_meter': [judge.first_week, judge.week], 'levels': levels,
           'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (ROOT / a.gt / a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
