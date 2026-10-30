#!/usr/bin/env bash
# Nemotron Ultra (free route) Day1 v0.1.B merge, then Sonnet agreement vs the Haiku GT; folds results into agreement_report.json.
set -uo pipefail
cd "$(dirname "$0")"
export CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR:-$HOME/.claude}  # same subscription account as the earlier Sonnet judge runs
D=../../data/v01b; LOG=$D/gamejam-day1-nemotron-ultra.log; mkdir -p $D
{
echo "$(date -u +%FT%TZ) start"
nice -n 19 python3 hier_merge.py --backend or-free --name gamejam-day1-nemotron-ultra --workers 1 --rpm 6 --max-requests 200 --max-tokens 4096 --max-minutes 120
rc=$?; echo merge rc=$rc
REQ=$(python3 -c "import json;print(json.load(open('$D/gamejam-day1-nemotron-ultra/merge_tree.json'))['meta']['usage'].get('requests','?'))" 2>/dev/null || echo '?')
CCT_SEND_FAST=1 cct send swarm-dashboard-free-cooker --from swarm-dashboard-merge "[merge] Nemotron Ultra Day1 merge $([ $rc = 0 ] && echo done || echo "stopped rc=$rc"), requests used: $REQ of 200. Route is yours again."
if [ $rc = 0 ]; then
  nice -n 19 python3 agree_merge.py --gt gamejam-day1-haiku --cand gamejam-day1-nemotron-ultra --out agreement_report_nemotron.json --max-in 120000 --max-out 50000
  echo judge rc=$?
  python3 - <<'EOF'
import json
from pathlib import Path
d = Path('../../data/v01b/gamejam-day1-haiku'); main = json.loads((d / 'agreement_report.json').read_text())
add = json.loads((d / 'agreement_report_nemotron.json').read_text())
main['candidates'].update(add['candidates'])
for lvl, c in add['levels'].items(): main['levels'].setdefault(lvl, {}).update(c)
main['judge_usage_nemotron'] = {k: add[k] for k in ('judge', 'judge_usage', 'judge_calls', 'weekly_meter', 'generated_at')}
tmp = d / 'agreement_report.json.tmp'; tmp.write_text(json.dumps(main, indent=1)); tmp.replace(d / 'agreement_report.json')
print('folded into agreement_report.json')
EOF
fi
echo "$(date -u +%FT%TZ) end"
} >>"$LOG" 2>&1
