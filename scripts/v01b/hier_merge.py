#!/usr/bin/env python3
"""v0.1.B: 2-D hierarchical merge of V0.1 hourly bullet summaries.

Rows: per agent, 1h leaves -> 4h -> day (each parent merges its children to <= K bullets per field).
Columns: per time bucket at every level, merge ALL agents' row nodes for that bucket (chunked tree when wide).
Every merged bullet cites child bullet ids (src) and the transitive leaf bullet ids (leaf_src), so any bullet
traces back to the V0.1 hourly summary it came from. Output: data/v01b/<name>/merge_tree.json.

Usage: hier_merge.py --base-url URL --key-env MODAL_VLLM_KEY [--model Qwen/Qwen3-8B] [--name gamejam-day1]
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SRC = Path('data/bucket_summaries.jsonl')
OUT_ROOT = Path(__file__).resolve().parents[2] / 'data' / 'v01b'
FIELDS = ('started', 'finished', 'blocked', 'key_decisions')
CAP = {'started': 6, 'finished': 6, 'blocked': 4, 'key_decisions': 4}
LEVELS = (('1h', 3600), ('4h', 14400), ('day', 86400))
DAY1_START = 1791641725  # 2026-10-10 14:15:25 UTC
CHUNK = 8  # max child nodes per column merge call

# Prompt + schema are shared with the live merge API (ui/src/pages/api/v01b/merge.ts): single source.
_SPEC = json.loads((Path(__file__).with_name('merge_prompt.json')).read_text())
SYSTEM, SCHEMA = _SPEC['system'], _SPEC['schema']


def norm(text):
    return re.sub(r'\W+', ' ', text.lower()).strip()


class Merger:
    def __init__(self, args):
        self.args = args
        self.claude = None
        if args.backend == 'claude-sub':
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from claude_sub import ClaudeSub
            self.claude = ClaudeSub(args.model, args.max_weekly_pp, (args.max_in, args.max_out) if args.max_in else None, args.max_week_abs)
        elif args.backend == 'or-free':  # free-cooker's route: pinned free model+provider, $0 ceiling, audited receipts
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v02'))
            import openrouter_free as orf
            self.orf, args.model = orf, orf.MODEL
            self.key = orf.DEFAULT_KEY.read_text().strip()
            try: self.preflight = orf.preflight(self.key)  # pricing, endpoint and free quota; no completion request
            except orf.GuardError as e: raise SystemExit('free route preflight: ' + str(e))
        else:
            self.key = os.environ[args.key_env]
        self.usage = {'calls': 0, 'in': 0, 'out': 0, 'fallbacks': 0, 'retries': 0}
        self.t0 = time.time()
        self.gate, self.next_at, self.sent = __import__('threading').Lock(), 0.0, 0  # shared-quota routes: request cap + min spacing

    def call(self, user):
        # Per-call cache (model + prompt): a stopped run resumes without paying for finished calls again.
        import hashlib
        cache = OUT_ROOT / '_call-cache' / hashlib.sha256((self.args.model + '\n' + SYSTEM + '\n' + user).encode()).hexdigest()[:32]
        if cache.exists():
            self.usage['cached'] = self.usage.get('cached', 0) + 1
            return json.loads(cache.read_text())
        out = self._call(user)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.with_suffix('.tmp').write_text(json.dumps(out)); os.replace(cache.with_suffix('.tmp'), cache)
        return out

    def _call(self, user):
        if self.claude:
            out = self.claude(SYSTEM, user)
            self.usage['calls'] += 1
            return out
        if self.args.backend == 'or-free':
            return self._call_free(user)
        body = {'model': self.args.model, 'temperature': 0, 'max_tokens': self.args.max_tokens,
                'messages': [{'role': 'system', 'content': SYSTEM + self.args.system_suffix}, {'role': 'user', 'content': user}]}
        if not self.args.no_schema: body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'merge', 'schema': SCHEMA}}
        if 'qwen3' in self.args.model.lower(): body['chat_template_kwargs'] = {'enable_thinking': False}
        if self.args.or_provider:  # OpenRouter: pin one full-precision provider, no fallback, billed cost in usage
            if self.usage.get('usd', 0) >= self.args.max_usd: raise SystemExit(f'USD cap reached: {self.usage["usd"]:.4f}')
            body['provider'] = {'only': [self.args.or_provider], 'quantizations': ['bf16', 'fp16'], 'allow_fallbacks': False}
            body['usage'] = {'include': True}
        for wait in (20, 60, 120, 240, None):  # 429 = rate limit, not a model failure: back off instead of burning a retry
            with self.gate:
                if self.args.max_requests and self.sent >= self.args.max_requests: raise SystemExit(f'request cap reached: {self.sent}')
                time.sleep(max(0.0, self.next_at - time.time())); self.next_at = time.time() + (60 / self.args.rpm if self.args.rpm else 0); self.sent += 1; self.usage["requests"] = self.sent
            req = urllib.request.Request(self.args.base_url.rstrip('/') + '/chat/completions', json.dumps(body).encode(),
                                         {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    d = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code != 429: raise
                msg = e.read().decode(errors='replace')[:300]
                if wait is None or 'per-day' in msg or 'per day' in msg: raise SystemExit('rate limited (stopping, cache keeps progress): ' + msg)
                self.usage['rate_limited'] = self.usage.get('rate_limited', 0) + 1; time.sleep(wait)
        u = d.get('usage') or {}
        self.usage['calls'] += 1; self.usage['in'] += u.get('prompt_tokens', 0); self.usage['out'] += u.get('completion_tokens', 0)
        if self.args.or_provider:
            self.usage['usd'] = round(self.usage.get('usd', 0) + float(u.get('cost') or 0), 6)
            prov = self.usage.setdefault('providers', {}); prov[d.get('provider', '?')] = prov.get(d.get('provider', '?'), 0) + 1
        text = re.sub(r'<think>.*?</think>', '', d['choices'][0]['message']['content'] or '', flags=re.S)
        return json.loads(re.search(r'\{.*\}', text, re.S).group(0))

    def _call_free(self, user):
        orf = self.orf
        body = {'model': orf.MODEL, 'temperature': 0, 'max_tokens': self.args.max_tokens, 'reasoning': {'enabled': False},
                'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}],
                'provider': {'only': [orf.PROVIDER], 'allow_fallbacks': False, 'data_collection': 'deny', 'max_price': dict(orf.ZERO_PRICES)}}
        with self.gate:
            if self.args.max_requests and self.sent >= self.args.max_requests: raise SystemExit(f'request cap reached: {self.sent}')
            time.sleep(max(0.0, self.next_at - time.time())); self.next_at = time.time() + (60 / self.args.rpm if self.args.rpm else 0); self.sent += 1; self.usage["requests"] = self.sent
        try:
            d = orf.api('/chat/completions', self.key, body)
            u = orf.audit_response(d)  # $0 receipt, pinned model and provider, or GuardError
        except orf.TransientProviderError:
            raise  # 502/503/504: counts as a failed attempt
        except orf.GuardError as e:  # 429, nonzero/missing cost, wrong model/provider: stop; the cache keeps progress
            raise SystemExit('free route stopped: ' + str(e))
        self.usage['calls'] += 1; self.usage['in'] += u.get('prompt_tokens', 0); self.usage['out'] += u.get('completion_tokens', 0)
        self.usage['usd'] = 0.0
        text = re.sub(r'<think>.*?</think>', '', d['choices'][0]['message']['content'] or '', flags=re.S)
        return json.loads(re.search(r'\{.*\}', text, re.S).group(0))

    def merge(self, children):
        """children: list of (label, node). Returns bullets {field: [{t, src}]} with src validated, plus fallback flag."""
        lines, ids, num = [], {}, {}  # short per-call numbers: an 8B model garbles long ids
        for label, node in children:
            lines.append('## ' + label)
            for f in FIELDS:
                for b in node['bullets'][f]:
                    ids[b['id']] = b; num[len(num) + 1] = b['id']
                    lines.append(f'[{len(num)}] ({f}) {b["t"]}')
        if self.args.max_minutes and time.time() - self.t0 > self.args.max_minutes * 60:
            raise SystemExit('wall-clock budget exceeded')
        for attempt in range(2):
            try:
                out = self.call('\n'.join(lines))
                res = {}
                for f in FIELDS:
                    keep = []
                    for b in out.get(f, [])[:CAP[f]]:
                        src = [num[int(s)] for s in dict.fromkeys(b.get('src', [])) if str(s).strip().lstrip('[').rstrip(']').isdigit() and int(str(s).strip('[] ')) in num]
                        if src and b.get('t', '').strip(): keep.append({'t': b['t'].strip(), 'src': src})
                    res[f] = keep
                return res, False
            except SystemExit:
                raise
            except Exception as e:  # malformed output or transport error: retry once, then deterministic fallback
                self.usage['retries'] += attempt == 0
                err = e
        self.usage['fallbacks'] += 1
        res, seen = {f: [] for f in FIELDS}, set()
        for f in FIELDS:
            for b in ids.values():
                if b['field'] == f and norm(b['t']) not in seen and len(res[f]) < CAP[f]:
                    seen.add(norm(b['t'])); res[f].append({'t': b['t'], 'src': [b['id']]})
        print('fallback:', repr(err)[:200], file=sys.stderr)
        return res, True


def finish(node_id, bullets, leaf_of):
    """Assign ids to merged bullets and resolve transitive leaf provenance."""
    out = {}
    for f in FIELDS:
        out[f] = []
        for i, b in enumerate(bullets[f]):
            bid = f'{node_id}#{f[0]}{i}'
            leaf = sorted({l for s in b['src'] for l in leaf_of[s]})
            leaf_of[bid] = leaf
            out[f].append({'id': bid, 'field': f, 't': b['t'], 'src': b['src'], 'leaf_src': leaf})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base-url', default=''); ap.add_argument('--key-env', default='MODAL_VLLM_KEY')
    ap.add_argument('--model', default='Qwen/Qwen3-8B'); ap.add_argument('--name', default='gamejam-day1')
    ap.add_argument('--project', default='gamejam'); ap.add_argument('--start', type=int, default=DAY1_START)
    ap.add_argument('--hours', type=int, default=24); ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--max-minutes', type=float, default=90)
    ap.add_argument('--backend', choices=['openai', 'claude-sub', 'or-free'], default='openai')
    ap.add_argument('--or-provider', default='', help='OpenRouter provider slug to pin (bf16/fp16 only, no fallback)')
    ap.add_argument('--max-usd', type=float, default=0.5, help='OpenRouter: stop when billed cost reaches this')
    ap.add_argument('--max-requests', type=int, default=0, help='openai: hard cap on HTTP requests (shared free quota)')
    ap.add_argument('--rpm', type=float, default=0, help='openai: max requests per minute'); ap.add_argument('--max-tokens', type=int, default=2048)
    ap.add_argument('--no-schema', action='store_true', help='openai: route lacks json_schema; parse the JSON object from the text')
    ap.add_argument('--system-suffix', default='', help='appended to the system prompt (e.g. a reasoning switch)')
    ap.add_argument('--max-weekly-pp', type=float, default=None, help='claude-sub: stop when the weekly meter rises more than this')
    ap.add_argument('--max-in', type=int, default=None); ap.add_argument('--max-out', type=int, default=None)
    ap.add_argument('--max-week-abs', type=float, default=None, help='claude-sub: stop when the shared weekly meter reaches this fraction')
    args = ap.parse_args()
    if 'nebius' in args.base_url: sys.exit('Token Factory is off limits until CKL lifts the ban (2026-10-30)')
    h0 = args.start - args.start % 3600; h1 = h0 + args.hours * 3600
    rows = [json.loads(l) for l in SRC.open()]
    rows = [r for r in rows if r['project'] == args.project and h0 <= r['bucket_start'] < h1 and r['grain_sec'] == 3600]
    nodes, leaf_of = {}, {}
    # Leaves: V0.1 hourly bullets, one node per (agent, hour).
    for r in rows:
        nid = f"r:{r['sid'][:8]}:1h:{r['bucket_start']}"
        b = {}
        for f in FIELDS:
            vals = r['summary'].get(f) if isinstance(r['summary'].get(f), list) else []
            b[f] = []
            for i, t in enumerate(vals):
                bid = f'{nid}#{f[0]}{i}'; leaf_of[bid] = [bid]
                b[f].append({'id': bid, 'field': f, 't': t, 'src': [], 'leaf_src': [bid]})
        nodes[nid] = {'kind': 'row', 'level': '1h', 'sid': r['sid'], 'agent': r['agent_title'], 'start': r['bucket_start'],
                      'end': r['bucket_start'] + 3600, 'children': [], 'bullets': b, 'msg_count': r.get('msg_count', 0), 'merged': False}
    m = Merger(args)
    agents = sorted({n['sid'] for n in nodes.values()})
    name_of = {n['sid']: n['agent'] for n in nodes.values()}

    def nbul(n): return sum(len(n['bullets'][f]) for f in FIELDS)

    def build(nid, meta, kids, labels):
        meta['children'] = [k for k in kids]
        meta['msg_count'] = sum(nodes[k]['msg_count'] for k in kids)
        if len(kids) == 1 or sum(nbul(nodes[k]) for k in kids) == 0:
            src = nodes[kids[0]] if len(kids) == 1 else {'bullets': {f: [] for f in FIELDS}}
            meta['bullets'] = finish(nid, {f: [{'t': b['t'], 'src': [b['id']]} for b in src['bullets'][f]] for f in FIELDS}, leaf_of)
            meta['merged'] = False
        else:
            res, fb = m.merge([(labels(k), nodes[k]) for k in kids])
            meta['bullets'] = finish(nid, res, leaf_of); meta['merged'] = True; meta['fallback'] = fb
        meta['concat_bullets'] = sum(nbul(nodes[k]) if not nodes[k]['merged'] and nodes[k]['level'] == '1h' else nodes[k].get('concat_bullets', nbul(nodes[k])) for k in kids)
        nodes[nid] = meta

    hh = lambda t: time.strftime('%H:%M', time.gmtime(t))
    # Rows: 4h, then day, per agent (children are the next-lower level of the same agent).
    for (lvl, span), (prev, _) in zip(LEVELS[1:], LEVELS[:-1]):
        jobs = []
        for sid in agents:
            groups = defaultdict(list)
            for k, n in nodes.items():
                if n['kind'] == 'row' and n['sid'] == sid and n['level'] == prev:
                    groups[h0 + (n['start'] - h0) // span * span].append(k)
            for start, kids in groups.items():
                kids.sort(key=lambda k: nodes[k]['start'])
                nid = f'r:{sid[:8]}:{lvl}:{start}'
                meta = {'kind': 'row', 'level': lvl, 'sid': sid, 'agent': name_of[sid], 'start': start, 'end': min(start + span, h1)}
                jobs.append((nid, meta, kids))
        with ThreadPoolExecutor(args.workers) as ex:
            list(ex.map(lambda j: build(j[0], j[1], j[2], lambda k: f'{hh(nodes[k]["start"])}-{hh(nodes[k]["end"])} UTC'), jobs))
        print(f'rows {lvl}: {len(jobs)} nodes, usage {m.usage}', file=sys.stderr)
    # Columns: per level and bucket, merge all agents' row nodes in that bucket (chunked when wide).
    for lvl, span in LEVELS:
        buckets = defaultdict(list)
        for k, n in list(nodes.items()):
            if n['kind'] == 'row' and n['level'] == lvl: buckets[n['start']].append(k)

        def col(item):
            start, kids = item
            kids.sort(key=lambda k: -nodes[k]['msg_count'])
            label = lambda k: nodes[k].get('agent') or k
            nid = f'c:{lvl}:{start}'
            if len(kids) > CHUNK:
                groups = [kids[i:i + CHUNK] for i in range(0, len(kids), CHUNK)]
                gids = []
                for gi, g in enumerate(groups):
                    gid = f'{nid}:g{gi}'
                    build(gid, {'kind': 'colgroup', 'level': lvl, 'start': start, 'end': start + span, 'agents': [nodes[k]['agent'] for k in g]}, g, label)
                    gids.append(gid)
                build(nid, {'kind': 'col', 'level': lvl, 'start': start, 'end': start + span, 'agents': [nodes[k]['agent'] for k in kids]}, gids,
                      lambda g: 'agents: ' + ', '.join(nodes[g]['agents']))
            else:
                build(nid, {'kind': 'col', 'level': lvl, 'start': start, 'end': start + span, 'agents': [nodes[k]['agent'] for k in kids]}, kids, label)
        with ThreadPoolExecutor(args.workers) as ex:
            list(ex.map(col, sorted(buckets.items())))
        print(f'cols {lvl}: {len(buckets)} buckets, usage {m.usage}', file=sys.stderr)
    out = OUT_ROOT / args.name
    out.mkdir(parents=True, exist_ok=True)
    meta = {'version': 'v0.1.B', 'model': args.model, 'source': str(SRC), 'project': args.project, 'start': h0, 'end': h1,
            'levels': [l for l, _ in LEVELS], 'caps': CAP, 'route': ({'openrouter_provider': args.or_provider, 'quantizations': ['bf16', 'fp16'], 'allow_fallbacks': False} if args.or_provider else {'api': m.orf.API, 'model': m.orf.MODEL, 'reasoning': False} if args.backend == 'or-free' else None), 'usage': m.usage, 'elapsed_s': round(time.time() - m.t0, 1),
            'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    if m.claude:
        meta.update(backend='claude-sub', weekly_meter=[m.claude.first_week, m.claude.week], weekly_delta_pp=m.claude.weekly_delta_pp(), claude_usage=m.claude.usage)
    tmp = out / 'merge_tree.json.tmp'
    tmp.write_text(json.dumps({'meta': meta, 'nodes': nodes}, ensure_ascii=False))
    os.replace(tmp, out / 'merge_tree.json')
    print(json.dumps(meta))


if __name__ == '__main__':
    main()
