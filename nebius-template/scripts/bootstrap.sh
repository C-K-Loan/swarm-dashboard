#!/usr/bin/env bash
set -euo pipefail

# bootstrap.sh — 1-command bring-up for the CCT Fleet Compressor template
# usage: ./bootstrap.sh [--fleet-dir DIR] [--cost-tier fast|balanced|precise] [--dashboard-port N]
# (aliases: cheap=fast, mid=balanced, expensive=precise for 1-week backwards compat)

FLEET_DIR="${HOME}/.cct/sessions"
COST_TIER="fast"
DASHBOARD_PORT=4321
SKIP_CLASSIFY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fleet-dir) FLEET_DIR="$2"; shift 2 ;;
    --cost-tier) COST_TIER="$2"; shift 2 ;;
    --dashboard-port) DASHBOARD_PORT="$2"; shift 2 ;;
    --skip-classify) SKIP_CLASSIFY=1; shift ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Env + API key check
if [[ -z "${NEBIUS_TOKEN_FACTORY_KEY:-}" ]]; then
  if [[ -f ".env" ]]; then
    # shellcheck disable=SC1090
    set -a; source ".env"; set +a
  else
    echo "[err] NEBIUS_TOKEN_FACTORY_KEY not set and [REDACTED_PATH] missing" >&2
    exit 1
  fi
fi

echo "[1/4] Scanning fleet dir: $FLEET_DIR"
if [[ ! -d "$FLEET_DIR" ]]; then
  echo "[err] fleet-dir does not exist" >&2
  exit 1
fi
PROJECT_COUNT=$(find "$FLEET_DIR" -name "*.jsonl" | wc -l)
echo "    found $PROJECT_COUNT jsonl files"

if [[ "$SKIP_CLASSIFY" -eq 0 ]]; then
  echo "[2/4] Extracting prose..."
  mkdir -p data/prose data/classified
  python3 scripts/extract_prose_generic.py "$FLEET_DIR" --out data/prose

  TIER_MODEL=""
  case "$COST_TIER" in
    fast|cheap)      TIER_MODEL="Qwen/Qwen3-30B-A3B-Instruct-2507"; COST_TIER="fast" ;;
    balanced|mid)    TIER_MODEL="google/gemma-3-27b-it";            COST_TIER="balanced" ;;
    precise|expensive) TIER_MODEL="nvidia/Nemotron-Nano-30B-A3B";   COST_TIER="precise" ;;
    *) echo "[err] unknown cost tier: $COST_TIER (use fast|balanced|precise)" >&2; exit 1 ;;
  esac
  echo "[3/4] Classifying via $TIER_MODEL (tier: $COST_TIER)..."
  for jf in data/prose/*.jsonl; do
    name=$(basename "$jf" .jsonl)
    python3 scripts/classify_batch.py \
      "$jf" \
      "data/classified/${name}__${COST_TIER}.jsonl" \
      --model "$TIER_MODEL" \
      --workers 15
  done
else
  echo "[2-3/4] Classification skipped"
fi

echo "[4/4] Starting dashboard on port $DASHBOARD_PORT..."
cd ui
if [[ ! -d node_modules ]]; then
  npm install --silent
fi
exec npm run dev -- --port "$DASHBOARD_PORT"
