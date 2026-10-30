#!/usr/bin/env bash
# Rebuild fleet_index.json + augment timeline.json from cct event state.
# Keeps opencode/sandbox agents as first-class citizens on /timeline + fleet dropdown.
# Cheap: just reads `cct top --json` twice; no API calls, no $.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=logs/refresh_fleet_cron.log
mkdir -p logs
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  python3 scripts/build_fleet_index.py 2>&1 | tail -3
  python3 scripts/augment_timeline_from_cct.py 2>&1 | tail -3
  python3 scripts/build_bus_index.py 2>&1 | tail -2
} >> "$LOG" 2>&1
