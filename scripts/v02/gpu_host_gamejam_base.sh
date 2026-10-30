#!/usr/bin/env bash
# CKL 2026-10-29: untrained Qwen3.5-4B then 9B (bf16, thinking OFF, JSON-schema guided decoding) as causal
# v0.3-lean rollouts over the full gamejam (10942 turns). vLLM on gpu-host 127.0.0.1:8000 behind an ssh tunnel
# (ckl 127.0.0.1:18092). $0. Run as a systemd user unit; touch data/v02/<dataset>/STOP to halt.
set -u
GPU_HOST=${GPU_HOST:?set GPU_HOST to the ssh host of your GPU machine}
cd .
URL=http://127.0.0.1:18092/v1
PY=${REMOTE_PYTHON:-python3}  # uv python 3.12: ships Python.h, which Triton's JIT needs (gpu-host has no python3-dev)
log(){ echo "{\"gpu_host_base\":\"$*\",\"at\":\"$(date -u +%FT%TZ)\"}"; }

serve(){ # $1 repo, $2 max_model_len
  ssh -o BatchMode=yes "$GPU_HOST" "kill \$(cat /tmp/qwen-serve.pid 2>/dev/null) 2>/dev/null; sleep 10; cd ~ && (VLLM_USE_FLASHINFER_SAMPLER=0 nohup $PY -m vllm.entrypoints.openai.api_server --model $1 --host 127.0.0.1 --port 8000 --dtype bfloat16 --max-model-len $2 --gpu-memory-utilization 0.92 --enable-prefix-caching --max-num-seqs 4 --limit-mm-per-prompt '{\"image\":0,\"video\":0}' > /tmp/qwen-serve.log 2>&1 & echo \$! > /tmp/qwen-serve.pid)"
  for i in $(seq 1 90); do curl -s -m 5 "$URL/models" | grep -q "$1" && return 0; sleep 10; done
  log "server for $1 did not come up"; return 1
}

run(){ # $1 dataset, $2 repo, $3 ctx
  python3 scripts/v02/local_qwen_day1.py --run --renderer v0.3-lean --structured --quote-spans --no-thinking --source gamejam-full-anchored \
    --max-corrections 2 --max-tokens 2048 --ctx "$3" --publish-every 25 --dataset "$1" --model "$2" --base-url "$URL" \
    --serving-note "gpu-host RTX 3090 24GB, vLLM $(ssh -o BatchMode=yes "$GPU_HOST" "$PY -c 'import vllm;print(vllm.__version__)'" 2>/dev/null) bf16, max_model_len $3, prefix caching, ssh tunnel; thinking OFF; json_schema guided decoding" \
    --weights-note "$2 rev $(ssh -o BatchMode=yes "$GPU_HOST" "$PY -c \"from huggingface_hub import HfApi;print(HfApi().model_info('$2').sha)\"" 2>/dev/null)"
}

stage(){ # $1 dataset, $2 repo, $3 ctx; the runner resumes from its ledger, so a crash (tunnel/server blip) re-serves and retries
  for try in 1 2 3 4 5; do
    log "$1 try $try"
    serve "$2" "$3" || continue
    run "$1" "$2" "$3" && return 0
    [ -e "data/v02/$1/STOP" ] && return 0
    sleep 60
  done
  log "$1 failed 5 tries"; return 1
}

if [ -z "${SKIP_4B:-}" ]; then  # SKIP_4B=1: CKL stopped the 4B at ~600 turns (2026-10-29) and switched to 9B
  stage qwen35-4b-base-gamejam Qwen/Qwen3.5-4B 16384 || exit 1
  [ -e data/v02/qwen35-4b-base-gamejam/STOP ] && { log "4B stopped"; exit 0; }
fi
stage qwen35-9b-base-gamejam Qwen/Qwen3.5-9B "${CTX9:-12288}" || exit 1
ssh -o BatchMode=yes "$GPU_HOST" 'kill $(cat /tmp/qwen-serve.pid) 2>/dev/null'
log "done"
cct send --from swarm-dashboard-qwen swarm-dashboard-qwen "Qwen lane MILESTONE: gpu-host base gamejam rollouts finished (4B then 9B, v0.3-lean, structured). vLLM stopped. Datasets qwen35-4b-base-gamejam and qwen35-9b-base-gamejam." || true
