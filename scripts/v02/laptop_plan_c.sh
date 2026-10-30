#!/usr/bin/env bash
# CKL plan C (2026-10-29): teacher-forced base eval, preview_n160 (Luna val turns 129-160), renderer v0.3-lean.
# Base Qwen3.5-4B then Qwen3.5-9B, MLX bf16 unquantized on the MacBook Air, thinking OFF, $0.
set -u
LAPTOP_HOST=${LAPTOP_HOST:?set LAPTOP_HOST to the ssh host of your Apple-silicon laptop}
cd .
source <(sed -n '/^URL=/p;/^VENV=/p;/^log()/p;/^serve()/,/^}/p;/^hash_weights()/,/^}/p' scripts/v02/laptop_plan_b.sh)
IDS="turn-0129,turn-0130,turn-0131,turn-0132,turn-0133,turn-0134,turn-0135,turn-0136,turn-0137,turn-0138,turn-0139,turn-0140,turn-0141,turn-0142,turn-0143,turn-0144,turn-0145,turn-0146,turn-0147,turn-0148,turn-0149,turn-0150,turn-0151,turn-0152,turn-0153,turn-0154,turn-0155,turn-0156,turn-0157,turn-0158,turn-0159,turn-0160"
tf(){ # $1 dataset, $2 repo, $3 weights note
  python3 scripts/v02/local_qwen_day1.py --run --teacher-forced day1 --eval-turn-ids "$IDS" --no-thinking --max-corrections 1 --max-tokens 1280 \
    --dataset "$1" --model "$2" --base-url "$URL" \
    --serving-note "mac MacBook Air M3 24GB, mlx-lm 0.32.2 under caffeinate, prompt cache 1/1GB, bf16 unquantized, thinking OFF" --weights-note "$3"
}
log "4B teacher-forced preview_n160"
serve Qwen/Qwen3.5-4B || exit 1
tf qwen35-4b-tf-preview-n160 Qwen/Qwen3.5-4B "data/v02/_mac-weights/qwen35-4b.json (rev 851bf6e)" || { log "4B eval failed"; exit 1; }
log "download + hash 9B"
[ -s data/v02/_mac-weights/qwen35-9b.json ] || hash_weights Qwen/Qwen3.5-9B data/v02/_mac-weights/qwen35-9b.json || { log "9B download failed"; exit 1; }
serve Qwen/Qwen3.5-9B || exit 1
log "9B teacher-forced preview_n160"
tf qwen35-9b-tf-preview-n160 Qwen/Qwen3.5-9B "data/v02/_mac-weights/qwen35-9b.json" || { log "9B eval failed"; exit 1; }
ssh -o BatchMode=yes "$LAPTOP_HOST" 'kill $(cat /tmp/qwen-serve.pid) 2>/dev/null'
log "done"
cct send --from swarm-dashboard-qwen swarm-dashboard-qwen "Qwen lane MILESTONE: mac plan C done (base 4B + 9B teacher-forced on Luna val turns 129-160, v0.3-lean, preview_n160). Results: data/v02/qwen35-4b-tf-preview-n160 + qwen35-9b-tf-preview-n160 teacher-forced.jsonl." || true
