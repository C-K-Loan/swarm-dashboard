#!/usr/bin/env python3
"""Offline smoke test for a harness adapter's output: no model calls, no network.

usage: smoke_adapter.py DIR   (DIR holds turns.json + actors.json, optionally patches.jsonl)

1. checks the canonical turn format (fields, kinds, roles, ordering, actor references);
2. builds the v0.3-lean context the labeler would see for every turn;
3. if patches.jsonl exists, validates each patch with the production validator and replays the graph.
In patches, an existing task may be written as "ref:<turn-id>#<local-ref>" (e.g. "ref:turn-0002#new:t1").
"""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lean_context as L  # noqa: E402
import pipeline as P  # noqa: E402

KINDS = {'agent', 'human', 'a2a', 'system'}
ROLES = {'assistant', 'user'}
TURN_KEYS = ('id', 'session_id', 'actor_id', 'kind', 'start_ts', 'end_ts', 'messages')
MSG_KEYS = ('id', 'ts', 'role', 'text')
REF = re.compile(r'^ref:(turn-[\w-]+)#(new:[\w-]+)$')


def fail(msg):
    raise SystemExit('FAIL: ' + msg)


def check_format(turns, actors):
    ids = {a['id'] for a in actors}
    for k in ('human', 'harness'):
        if k not in ids:
            fail(f'actors.json needs the "{k}" actor')
    last = float('-inf')
    for t in turns:
        miss = [k for k in TURN_KEYS if k not in t]
        if miss:
            fail(f'{t.get("id")}: missing {miss}')
        if t['kind'] not in KINDS:
            fail(f'{t["id"]}: kind {t["kind"]!r} not in {sorted(KINDS)}')
        if t['actor_id'] not in ids:
            fail(f'{t["id"]}: actor {t["actor_id"]} not in actors.json')
        if t['end_ts'] < last:
            fail(f'{t["id"]}: turns must be ordered by end_ts')
        last = t['end_ts']
        if not t['messages']:
            fail(f'{t["id"]}: no messages')
        for m in t['messages']:
            miss = [k for k in MSG_KEYS if k not in m]
            if miss or m['role'] not in ROLES or not str(m['text']).strip():
                fail(f'{t["id"]}/{m.get("id")}: message needs {MSG_KEYS}, role in {sorted(ROLES)}, non-empty text')


def resolve(o, mapping):
    if isinstance(o, str):
        m = REF.match(o)
        return mapping.get((m.group(1), m.group(2)), o) if m else o
    if isinstance(o, list):
        return [resolve(v, mapping) for v in o]
    if isinstance(o, dict):
        return {k: resolve(v, mapping) for k, v in o.items()}
    return o


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    d = Path(sys.argv[1])
    turns = json.loads((d / 'turns.json').read_text())
    actors = json.loads((d / 'actors.json').read_text())
    check_format(turns, actors)
    print(f'format ok: {len(turns)} turns, {len({t["session_id"] for t in turns})} sessions, {len(actors)} actors')
    graph = {'actors': {a['id']: a for a in actors}, 'tasks': {}, 'decisions': {}, 'relationships': [], 'ambiguities': []}
    pfile = d / 'patches.jsonl'
    patches = [json.loads(l) for l in pfile.read_text().splitlines() if l.strip()] if pfile.exists() else []
    mapping = {}
    for i, t in enumerate(turns):
        ctx, _ = L.build(t, graph, 'Sample project.')
        tokens = L.estimate_tokens(ctx)
        if i < len(patches):
            patch = resolve(patches[i], mapping)
            try:
                P.check(patch, t, graph)
            except Exception as e:  # report which turn failed and why
                fail(f'{t["id"]}: patch rejected: {e}')
            for task in patch['tasks']:
                if task['resolution'] != 'existing':
                    mapping[(t['id'], task['ref'])] = P.uid('task', t['id'], task['ref'])
            graph, _ = P.apply(graph, patch, t)
        print(f'  {t["id"]} {t["kind"]:<6} {t["actor_id"]:<16} context ~{tokens} tokens')
    if patches:
        status = {k: v.get('status') for k, v in graph['tasks'].items()}
        print(f'replay ok: {len(graph["tasks"])} tasks {status}, {len(graph["decisions"])} decisions, {len(graph["relationships"])} relationships')
    print('SMOKE OK')


if __name__ == '__main__':
    main()
