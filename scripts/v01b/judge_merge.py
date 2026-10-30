#!/usr/bin/env python3
"""Accuracy eval of a v0.1.B merge tree, per level, with a stronger judge model on Token Factory.

Per level (cell = 1h leaf, row-4h, row-day, col-1h, col-4h, col-day):
- coverage: sampled source (leaf) bullets under a node; judge says if each fact is kept in the merged bullets
  (covered / partly / missing) and whether it matters (important). Reports all-fact and important-fact coverage.
- faithfulness: sampled merged bullets vs the source bullets they cite: supported / partial / unsupported.
- duplicates: near-duplicate merged bullet pairs within a node (token Jaccard >= 0.5), per bullet.
- drift: share of merged bullets citing one source, and share that copy it verbatim.
Usage: judge_merge.py [--name gamejam-day1] [--judge Qwen/Qwen3-235B-A22B-Instruct-2507] [--max-usd 1.5]
"""
import argparse, json, os, random, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'v01b'
FIELDS = ('started', 'finished', 'blocked', 'key_decisions')
PRICE = (0.20, 0.60)  # USD per 1M in/out, conservative for Qwen3-235B-A22B-Instruct-2507 on Token Factory
norm = lambda t: re.sub(r'\W+', ' ', t.lower()).strip()

COVER_SYS = '''You check whether a short merged summary preserves facts from its source bullets.
For EACH numbered source bullet decide:
- kept: "covered" (the merged summary states this fact, possibly generalized or combined), "partly" (only vaguely or partially), or "missing".
- important: true if the fact matters to someone reviewing what the team did (a deliverable, result, blocker, decision); false for routine housekeeping (reading files, registering, checking status).
Return JSON {"items": [{"n": number, "kept": "...", "important": true|false}]} with one item per source bullet.'''
COVER_SCHEMA = {'type': 'object', 'required': ['items'], 'properties': {'items': {'type': 'array', 'items': {'type': 'object', 'required': ['n', 'kept', 'important'],
                'properties': {'n': {'type': 'integer'}, 'kept': {'type': 'string', 'enum': ['covered', 'partly', 'missing']}, 'important': {'type': 'boolean'}}}}}}
FAITH_SYS = '''You check merged summary bullets against the source bullets each one cites.
For EACH numbered merged bullet decide "supported" (everything it says is stated or directly implied by its sources),
"partial" (mostly supported but adds or distorts a detail), or "unsupported" (claims something the sources do not say).
Return JSON {"items": [{"n": number, "verdict": "...", "why": "short reason when not supported"}]}.'''
FAITH_SCHEMA = {'type': 'object', 'required': ['items'], 'properties': {'items': {'type': 'array', 'items': {'type': 'object', 'required': ['n', 'verdict'],
                'properties': {'n': {'type': 'integer'}, 'verdict': {'type': 'string', 'enum': ['supported', 'partial', 'unsupported']}, 'why': {'type': 'string'}}}}}}


class ClaudeSubJudge:
    """Claude Haiku through the Max subscription (scripts/v02/claude_haiku_day1.py guards: OAuth only, extra usage off,
    model and rate-limit receipt checked per call). Costs quota, not dollars; stops if overage would be used."""
    MODEL = 'claude-haiku-4-5-20251001'

    def __init__(self, a):
        import subprocess, tempfile
        os.environ.setdefault('CCT_V02_DATASET', 'prototype')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v02'))
        import claude_haiku_day1 as h
        h.MODEL = self.MODEL
        self.h, self.sp, self.tmp = h, subprocess, Path(tempfile.mkdtemp(prefix='v01b-judge-'))
        self.auth = h.check_auth(); self.cost = 0.0; self.calls = 0; self.a = a; self.first_week = None; self.week = None

    def __call__(self, system, user, schema):
        sysmsg = system + '\nReply with ONLY the JSON object, no prose, no code fences.'
        for attempt in range(2):
            log = self.tmp / f'{time.time_ns()}.jsonl'
            with log.open('w') as out:
                self.sp.run(self.h.command(sysmsg), cwd=self.tmp, env=self.h.environment(), input=user, text=True, stdout=out, stderr=self.sp.DEVNULL, timeout=300)
            rows = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
            result = next((x for x in reversed(rows) if x.get('type') == 'result'), {})
            rate = next((x.get('rate_limit_info') for x in reversed(rows) if x.get('type') == 'rate_limit_event'), None) or {}
            if rate.get('isUsingOverage') or rate.get('status') not in (None, 'allowed', 'allowed_warning'):
                raise SystemExit('subscription limit/overage reached; stopping judge')
            if result.get('modelUsage') and set(result['modelUsage']) != {self.MODEL}:
                raise SystemExit('unexpected model in receipt: ' + ','.join(result['modelUsage']))
            week = ((rate.get('unifiedWindows') or {}).get('seven_day') or {}).get('utilization')
            if week is not None:
                self.first_week = self.first_week if self.first_week is not None else week; self.week = week
            self.calls += 1
            text = result.get('result', '')
            m = re.search(r'\{.*\}', text, re.S)
            try: return json.loads(m.group(0))['items']
            except Exception: continue
        print('claude judge parse failed', file=sys.stderr); return []


class Judge:
    def __init__(self, a):
        self.a = a; self.cost = 0.0; self.calls = 0
        self.key = os.environ['NEBIUS_TOKEN_FACTORY_KEY']; self.base = os.environ.get('NEBIUS_TOKEN_FACTORY_BASE', 'https://api.tokenfactory.nebius.com/v1')

    def __call__(self, system, user, schema):
        if self.cost > self.a.max_usd: raise SystemExit(f'judge budget reached: USD {self.cost:.3f}')
        body = {'model': self.a.judge, 'temperature': 0, 'max_tokens': 4096, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'judge', 'schema': schema}}}
        for attempt in range(3):
            try:
                req = urllib.request.Request(self.base.rstrip('/') + '/chat/completions', json.dumps(body).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
                with urllib.request.urlopen(req, timeout=300) as r: d = json.load(r)
                u = d.get('usage') or {}
                self.cost += u.get('prompt_tokens', 0) / 1e6 * PRICE[0] + u.get('completion_tokens', 0) / 1e6 * PRICE[1]; self.calls += 1
                return json.loads(d['choices'][0]['message']['content'])['items']
            except Exception as e:
                err = e; time.sleep(3 * (attempt + 1))
        print('judge call failed:', repr(err)[:200], file=sys.stderr); return []


def leaves_under(nodes, nid):
    n = nodes[nid]
    if n['kind'] == 'row' and n['level'] == '1h': return [nid]
    return [l for c in n['children'] for l in leaves_under(nodes, c)]


def jacc(a, b):
    a, b = set(norm(a).split()), set(norm(b).split())
    return len(a & b) / max(1, len(a | b))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--name', default='gamejam-day1')
    ap.add_argument('--judge', default='Qwen/Qwen3-235B-A22B-Instruct-2507'); ap.add_argument('--max-usd', type=float, default=1.5)
    ap.add_argument('--backend', choices=['tf', 'claude-sub'], default='claude-sub'); ap.add_argument('--out', default='judge_report.json')
    ap.add_argument('--nodes', type=int, default=8); ap.add_argument('--facts', type=int, default=40); ap.add_argument('--bullets', type=int, default=40)
    a = ap.parse_args(); rnd = random.Random(7)
    if a.backend == 'tf': sys.exit('Token Factory is off limits until CKL lifts the ban (2026-10-30); use --backend claude-sub')
    tree = json.loads((ROOT / a.name / 'merge_tree.json').read_text()); nodes = tree['nodes']
    leaf = {b['id']: b for n in nodes.values() if n['kind'] == 'row' and n['level'] == '1h' for f in FIELDS for b in n['bullets'][f]}
    judge = ClaudeSubJudge(a) if a.backend == 'claude-sub' else Judge(a)
    if a.backend == 'claude-sub': a.judge = ClaudeSubJudge.MODEL
    levels = {'row-4h': ('row', '4h'), 'row-day': ('row', 'day'), 'col-1h': ('col', '1h'), 'col-4h': ('col', '4h'), 'col-day': ('col', 'day')}
    results = {}
    for name, (kind, lvl) in levels.items():
        # Only nodes where a real merge happened (several children); single-child nodes are copies of their child.
        cand = [k for k, n in nodes.items() if n['kind'] == kind and n['level'] == lvl and n.get('merged') and not n.get('fallback')]  # model failures are reported as fallback rate, not judged
        pick = rnd.sample(cand, min(a.nodes, len(cand)))
        cov_jobs, faith_bullets, dup, nb, single, verbatim, comp = [], [], 0, 0, 0, 0, [0, 0]
        for k in pick:
            n = nodes[k]; merged = [b for f in FIELDS for b in n['bullets'][f]]
            src = [leaf[l] for x in leaves_under(nodes, k) for f in FIELDS for l in [b['id'] for b in nodes[x]['bullets'][f]]]
            comp[0] += len(merged); comp[1] += len(src)
            nb += len(merged); dup += sum(1 for i in range(len(merged)) for j in range(i) if jacc(merged[i]['t'], merged[j]['t']) >= 0.5)
            single += sum(len(b['leaf_src']) == 1 for b in merged)
            verbatim += sum(len(b['leaf_src']) == 1 and norm(leaf[b['leaf_src'][0]]['t']) == norm(b['t']) for b in merged)
            sample = rnd.sample(src, min(a.facts, len(src)))
            cov_jobs.append(('Merged summary:\n' + '\n'.join(f'- ({b["field"]}) {b["t"]}' for b in merged) + '\n\nSource bullets:\n' + '\n'.join(f'[{i + 1}] ({b["field"]}) {b["t"]}' for i, b in enumerate(sample)), len(sample)))
            faith_bullets += merged
        fs = rnd.sample(faith_bullets, min(a.bullets, len(faith_bullets)))
        faith_jobs = []
        for i in range(0, len(fs), 10):
            chunk = fs[i:i + 10]
            faith_jobs.append('\n\n'.join(f'[{j + 1}] MERGED: {b["t"]}\nSOURCES:\n' + '\n'.join(f'  - {leaf[s]["t"]}' for s in b['leaf_src'][:15]) for j, b in enumerate(chunk)))
        with ThreadPoolExecutor(4 if a.backend == 'claude-sub' else 8) as ex:
            cov = list(ex.map(lambda j: judge(COVER_SYS, j[0], COVER_SCHEMA), cov_jobs))
            faith = list(ex.map(lambda j: judge(FAITH_SYS, j, FAITH_SCHEMA), faith_jobs))
        items = [x for r in cov for x in r]; imp = [x for x in items if x.get('important')]
        score = lambda xs: (sum(x['kept'] == 'covered' for x in xs) + 0.5 * sum(x['kept'] == 'partly' for x in xs)) / max(1, len(xs))
        fv = [x for r in faith for x in r]
        allm = [n for n in nodes.values() if n['kind'] == kind and n['level'] == lvl and n.get('merged')]
        results[name] = {'nodes': len(pick), 'fallback_rate': round(sum(bool(n.get('fallback')) for n in allm) / max(1, len(allm)), 3), 'merged_bullets': comp[0], 'source_bullets': comp[1], 'compression': round(comp[0] / max(1, comp[1]), 3),
                         'coverage_all': round(score(items), 3), 'coverage_important': round(score(imp), 3), 'facts_judged': len(items), 'important_share': round(len(imp) / max(1, len(items)), 3),
                         'faithful': round(sum(x['verdict'] == 'supported' for x in fv) / max(1, len(fv)), 3), 'partial': round(sum(x['verdict'] == 'partial' for x in fv) / max(1, len(fv)), 3),
                         'unsupported': round(sum(x['verdict'] == 'unsupported' for x in fv) / max(1, len(fv)), 3), 'bullets_judged': len(fv),
                         'unsupported_examples': [x.get('why', '') for x in fv if x['verdict'] == 'unsupported'][:3],
                         'near_dup_per_bullet': round(dup / max(1, nb), 3), 'single_source': round(single / max(1, nb), 3), 'verbatim_copy': round(verbatim / max(1, nb), 3)}
        print(name, json.dumps(results[name]), f'judge USD {judge.cost:.3f}', file=sys.stderr)
    out = {'judge': a.judge, 'merge_model': tree['meta']['model'], 'judge_usd_est': round(judge.cost, 4), 'judge_calls': judge.calls, 'levels': results,
           'subscription_weekly_utilization': [getattr(judge, 'first_week', None), getattr(judge, 'week', None)],
           'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (ROOT / a.name / a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
