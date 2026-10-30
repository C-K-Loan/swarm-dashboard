#!/usr/bin/env bash
# CKL GO 2026-10-29: served LoRA (Qwen3-30B-A3B-Instruct-2507 + cct-v03-lean-30b-r16-mt1) over FULL Day1, setup identical to the
# 30B base baseline (qwen30b-tf-lean-day1-full). Usage: tf_lora_day1.sh <served-model-id> <price-in/token> <price-out/token>
set -eu
cd .
MODEL=$1; PIN=$2; POUT=$3; D=qwen30b-lora-r16-lean-day1
exec systemd-run --user --unit swarm-tf-qwen30b-lora-r16 -p EnvironmentFile=$PWD/.env --working-directory="$PWD" \
  /usr/bin/python3 scripts/v02/local_qwen_day1.py --run --renderer v0.3-lean --structured --quote-spans --no-thinking --source day1 \
  --max-corrections 2 --max-tokens 2048 --ctx 32768 --publish-every 25 --dataset $D --model "$MODEL" \
  --base-url https://api.tokenfactory.nebius.com/v1 --api-key-env NEBIUS_TOKEN_FACTORY_KEY --budget-usd 3.00 \
  --spend-ledger "$PWD/data/v02/_tf-spend-2026-10-29-30b-lora.json" --price-in "$PIN" --price-out "$POUT" \
  --serving-note "Nebius Token Factory serverless LoRA; CKL paid GO 2026-10-29 (full Day1 1902 turns, post-LoRA eval vs qwen30b-tf-lean-day1-full, cap USD 3.00); thinking OFF; json_schema guided decoding" \
  --weights-note "Qwen/Qwen3-30B-A3B-Instruct-2507 + LoRA cct-v03-lean-30b-r16-mt1 (served id $MODEL)"
