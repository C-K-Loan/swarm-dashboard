#!/usr/bin/env python3
"""Offline, resumable publication migration. Pause legacy writers before --run.

No inference; source, predictions, reviews and stop markers are never modified.
"""
import argparse
import fcntl
import json
import re
import time
from pathlib import Path

import pipeline as p


def backfill(directory):
    previous_data = p.DATA
    started = time.monotonic()
    try:
        p.DATA = Path(directory)
        with (p.DATA / '.writer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status = p.read('status.json')
            records = p.ledger()
            result = p.publish(records)
            # Migration must not make a stopped/paused run look like it resumed.
            if status and status.get('completed') == len(records):
                for name in ('turn-index.json', 'review.json'):
                    index = p.read(name)
                    index['status'] = status
                    p.atomic(p.DATA / name, index)
                p.atomic(p.DATA / 'status.json', status)
            return {'dataset': p.DATA.name, 'completed': len(records),
                    'total': result['total'], 'seconds': round(time.monotonic() - started, 3),
                    'index_bytes': (p.DATA / 'turn-index.json').stat().st_size}
    finally:
        p.DATA = previous_data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--dataset', action='append')
    group.add_argument('--all', action='store_true', help='prototype/day1/Haiku/Nemotron/Gemma datasets')
    ap.add_argument('--run', action='store_true', help='write the publication files, without inference')
    args = ap.parse_args()
    root = p.ROOT / 'data/v02'
    names = args.dataset or sorted(d.name for d in root.iterdir() if d.is_dir() and (
        d.name in ('prototype', 'day1', 'haiku45-day1') or d.name.startswith(('nemotron-', 'nemotron3', 'gemma4-'))))
    for name in names:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            ap.error('Invalid dataset name')
        directory = root / name
        if not all((directory / f).exists() for f in ('turns.json', 'actors.json', 'manifest.json')):
            print(json.dumps({'dataset': name, 'skipped': 'source not prepared'}))
            continue
        print(json.dumps(backfill(directory) if args.run else {'dataset': name, 'planned': True}), flush=True)


if __name__ == '__main__':
    main()
