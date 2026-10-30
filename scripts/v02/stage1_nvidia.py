#!/usr/bin/env python3
"""Stage 1 per-message intent labels by Nemotron 3 Ultra on the NVIDIA build route ($0 trial key).

Same prompt, parser, row format, gold order and flatten as nebius-hack-dev/scripts/classify_bundles.py; only
the HTTP call is replaced (never Nebius: CKL hard rule 2026-10-30). One request per message, no batching.
Stops on 429/402/403/any cost. A bundle with any request error is deleted so a rerun redoes it; the flat
ref_<slug>.jsonl that /eval reads is written only when every gold bundle is complete.
"""
import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nvidia_build as nb

DEV_SCRIPTS = Path('scripts')
sys.path.insert(0, str(DEV_SCRIPTS))
import classify_bundles as cb  # noqa: E402  (load_env is never called, so no Nebius key is read)

STOP = threading.Event()
state = {'reason': None, 'sent': 0, 'next_at': 0.0}
gate = threading.Lock()


def make_call(key, min_interval, max_requests):
    def call(model, prompt, timeout=90):
        for attempt in (1, 2, 3, 4, 5):
            with gate:
                if STOP.is_set():
                    return {'error': 'stopped: ' + str(state['reason'])}
                if state['sent'] >= max_requests:
                    STOP.set(); state['reason'] = 'max-requests reached'
                    return {'error': 'stopped: max-requests reached'}
                time.sleep(max(0.0, state['next_at'] - time.time()))
                state['next_at'] = time.time() + min_interval; state['sent'] += 1
            body = {'model': nb.MODEL, 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 1500,
                    'temperature': 0.0, 'reasoning': {'enabled': False}}
            try:
                response = nb.api('/chat/completions', key, body)
                nb.audit_response(response)
                return response
            except nb.TransientProviderError as error:
                if attempt == 5:
                    return {'error': str(error)}
                time.sleep(10 * attempt)
            except nb.GuardError as error:  # 429, 402/403, cost/credit field, wrong model
                STOP.set(); state['reason'] = str(error)
                return {'error': 'stopped: ' + str(error)}
    return call


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slug', default='nemotron_ultra')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--min-interval', type=float, default=3.0)
    ap.add_argument('--max-requests', type=int, default=1100)
    args = ap.parse_args()
    key = nb.DEFAULT_KEY.read_text()
    nb.preflight(key)
    cb._call = make_call(key, args.min_interval, args.max_requests)
    order = cb.gold_bundle_order()
    lock, budget = threading.Lock(), {'in': 0, 'out': 0, 'cost': 0.0}
    start = time.time()
    print(f'[START] {nb.MODEL} via {nb.API} slug={args.slug} bundles={len(order)}', flush=True)
    for i, name in enumerate(order, 1):
        out = cb.EVAL / f'ref_{args.slug}' / f'{name}.jsonl'
        if out.exists():
            print(f'[skip] {name}', flush=True)
            continue
        rows = cb.classify_bundle(cb.BUNDLES / f'{name}.txt', nb.MODEL, args.slug, out, float('inf'), budget, lock, args.workers)
        api_errors = sum(1 for r in rows if r.get('_api_error'))
        parse_errors = sum(1 for r in rows if r.get('_parse_error'))
        if api_errors:
            out.unlink()
        print(f'[{i}/{len(order)}] {name} rows={len(rows)} api_errors={api_errors} parse_errors={parse_errors} '
              f'requests={state["sent"]} elapsed={time.time() - start:.0f}s{" (deleted for rerun)" if api_errors else ""}', flush=True)
        if STOP.is_set():
            break
    missing = [n for n in order if not (cb.EVAL / f'ref_{args.slug}' / f'{n}.jsonl').exists()]
    if missing:
        print(f'[PARTIAL] {len(order) - len(missing)}/{len(order)} bundles; not flattened. stop={state["reason"]} requests={state["sent"]}', flush=True)
        return 1
    total = cb.flatten(args.slug, order)
    print(f'[DONE] {total} rows at {cb.EVAL}/ref_{args.slug}.jsonl requests={state["sent"]} elapsed={time.time() - start:.0f}s', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
