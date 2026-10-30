#!/usr/bin/env bash
# finalize.sh — run after all classifications complete.
# Regenerates cards.json, cost_summary.json, charts, and prints a summary.
set -euo pipefail

cd .

echo "[1/4] reducing all classified → cards.json ..."
python3 scripts/reduce_to_cards.py data/classified data/cards.json --prose-dir data/prose 2>&1 | tail -3

echo
echo "[2/4] updating cost_summary.json ..."
python3 -c "
import json
from pathlib import Path
tot_our_cost = 0.0
tot_msgs = 0
tot_in = 0
tot_out = 0
for p in sorted(Path('data/classified').glob('*.jsonl')):
    for l in open(p):
        o = json.loads(l)
        u = o.get('usage', {})
        tot_our_cost += u.get('cost_usd', 0)
        tot_msgs += 1
        tot_in += u.get('in', 0)
        tot_out += u.get('out', 0)
summary = {
    'ours': {'cost_usd': round(tot_our_cost, 4), 'model': 'Qwen/Qwen3-30B-A3B-Instruct-2507', 'tier': 'cheap'},
    'msgs': tot_msgs, 'tokens_in': tot_in, 'tokens_out': tot_out,
    'baselines': {
        'claude-haiku-3.5': {'cost_usd': round(tot_in/1_000_000*0.8 + tot_out/1_000_000*4, 4), 'ratio': round((tot_in/1_000_000*0.8 + tot_out/1_000_000*4)/tot_our_cost, 1) if tot_our_cost else 0},
        'gpt-4o-mini': {'cost_usd': round(tot_in/1_000_000*0.15 + tot_out/1_000_000*0.6, 4), 'ratio': round((tot_in/1_000_000*0.15 + tot_out/1_000_000*0.6)/tot_our_cost, 1) if tot_our_cost else 0},
        'gpt-4o': {'cost_usd': round(tot_in/1_000_000*2.5 + tot_out/1_000_000*10, 4), 'ratio': round((tot_in/1_000_000*2.5 + tot_out/1_000_000*10)/tot_our_cost, 1) if tot_our_cost else 0},
        'claude-sonnet-3.7': {'cost_usd': round(tot_in/1_000_000*3 + tot_out/1_000_000*15, 4), 'ratio': round((tot_in/1_000_000*3 + tot_out/1_000_000*15)/tot_our_cost, 1) if tot_our_cost else 0},
    },
}
json.dump(summary, open('data/cost_summary.json', 'w'), indent=2)
print(f'  msgs={tot_msgs:,}  ours=\${tot_our_cost:.4f}  haiku=\${summary[\"baselines\"][\"claude-haiku-3.5\"][\"cost_usd\"]:.4f}')
"

echo
echo "[3/4] regenerating charts ..."
python3 scripts/make_charts.py

echo
echo "[4/4] copying charts to UI public ..."
cp docs/charts/architecture.png ui/public/architecture.png
cp docs/charts/cost_pareto.png ui/public/cost_pareto.png
cp docs/charts/agreement_bars.png ui/public/agreement_bars.png

echo
echo "=== SUMMARY ==="
python3 -c "
import json
cards = json.load(open('data/cards.json'))
summary = json.load(open('data/cost_summary.json'))
from collections import Counter
by_proj = Counter(c['project'] for c in cards)
by_state = Counter(c['state'] for c in cards)
print(f'cards: {len(cards)}  by project: {dict(by_proj)}  by state: {dict(by_state)}')
print(f'cost: \${summary[\"ours\"][\"cost_usd\"]:.4f} on {summary[\"msgs\"]:,} msgs ({summary[\"tokens_in\"]:,} in / {summary[\"tokens_out\"]:,} out)')
print(f'vs frontier: Haiku \${summary[\"baselines\"][\"claude-haiku-3.5\"][\"cost_usd\"]:.2f} ({summary[\"baselines\"][\"claude-haiku-3.5\"][\"ratio\"]}x) · Sonnet 3.7 \${summary[\"baselines\"][\"claude-sonnet-3.7\"][\"cost_usd\"]:.2f} ({summary[\"baselines\"][\"claude-sonnet-3.7\"][\"ratio\"]}x)')
"
echo
echo "Dashboard: http://localhost:4321/t/cheap"
echo "About: http://localhost:4321/about"
