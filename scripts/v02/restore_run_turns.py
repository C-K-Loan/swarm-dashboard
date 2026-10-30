#!/usr/bin/env python3
"""Rebuild each run's turns.json (and its frozen rubric + schema copies) from shared files; the public dataset ships one copy.

Every run in data/v02/<run>/ labeled a prefix of the source turns; manifest.json's turn_count gives its window.
usage: restore_run_turns.py [--source data/v02/gamejam-full-anchored]   (never overwrites an existing turns.json)
"""
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=ROOT / 'data/v02/gamejam-full-anchored')
    a = ap.parse_args()
    src = json.loads((a.source / 'turns.json').read_text())
    num = lambda t: int(t['id'].split('-')[1])
    made = 0
    for man in sorted((ROOT / 'data/v02').glob('*/manifest.json')):
        run = man.parent
        if run == a.source:
            continue
        for name, src_file in (('extract-turn.txt', ROOT / 'prompts/v02/extract-turn.txt'), ('turn-patch.schema.json', ROOT / 'schemas/v02/turn-patch.schema.json')):
            if not (run / name).exists():
                (run / name).write_text(src_file.read_text())  # byte-identical to every run's frozen copy
        if (run / 'turns.json').exists():
            continue
        n = json.loads(man.read_text()).get('turn_count')
        if not n:
            continue
        (run / 'turns.json').write_text(json.dumps([t for t in src if num(t) <= n], ensure_ascii=False) + '\n')
        if not (run / 'actors.json').exists():
            (run / 'actors.json').write_text((a.source / 'actors.json').read_text())
        made += 1
    print(f'turns.json written for {made} runs')


if __name__ == '__main__':
    main()
