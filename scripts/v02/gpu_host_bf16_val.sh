#!/usr/bin/env bash
# CKL GO 2026-10-29: bf16 Qwen3-30B-A3B-Instruct-2507 on gpu-host (3090 24GB) with --cpu-offload-gb + --enable-lora (r16 adapter),
# teacher-forced eval of the 131 held-out val rows, base and LoRA arms submitted together so each weight-streaming pass is shared. $0.
set -u
GPU_HOST=${GPU_HOST:?set GPU_HOST to the ssh host of your GPU machine}
cd .
URL=http://127.0.0.1:18092/v1
PY=${REMOTE_PYTHON:-python3}
REPO=Qwen/Qwen3-30B-A3B-Instruct-2507
LORA=qwen30b-r16-mt1
OFFLOAD=${OFFLOAD:-54}
VAL=data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k/validation.jsonl
log(){ echo "{\"bf16_val\":\"$*\",\"at\":\"$(date -u +%FT%TZ)\"}"; }

T0=$(date +%s)
ssh -o BatchMode=yes "$GPU_HOST" "kill \$(cat /tmp/qwen-serve.pid 2>/dev/null) 2>/dev/null; sleep 5; cd ~ && (VLLM_USE_FLASHINFER_SAMPLER=0 nohup $PY -m vllm.entrypoints.openai.api_server --model $REPO --host 127.0.0.1 --port 8000 --dtype bfloat16 --cpu-offload-gb $OFFLOAD --max-model-len 10240 --gpu-memory-utilization 0.95 --enable-prefix-caching --max-num-seqs 262 --max-num-batched-tokens 16384 --enable-lora --max-lora-rank 16 --max-loras 1 --lora-modules $LORA=${REMOTE_ADAPTER_DIR:?set REMOTE_ADAPTER_DIR to the adapter folder on the GPU machine} > /tmp/qwen-serve.log 2>&1 < /dev/null & echo \$! > /tmp/qwen-serve.pid)"
ssh -o BatchMode=yes -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18092:127.0.0.1:8000 "$GPU_HOST" || true
up=0; for i in $(seq 1 120); do curl -s -m 5 "$URL/models" | grep -q "$LORA" && { up=1; break; }; sleep 10; done
[ $up = 1 ] || { log "server did not come up"; ssh -o BatchMode=yes "$GPU_HOST" 'tail -40 /tmp/qwen-serve.log'; exit 1; }
VER=$(ssh -o BatchMode=yes "$GPU_HOST" "$PY -c 'import vllm;print(vllm.__version__)'")
REV=$(ssh -o BatchMode=yes "$GPU_HOST" "$PY -c \"from huggingface_hub import HfApi;print(HfApi().model_info('$REPO').sha)\"")
ASHA=$(sha256sum data/sft/checkpoints/qwen30b_r16_mt1/adapter_model.safetensors | cut -c1-16)
log "server up after $(( $(date +%s)-T0 ))s"
NOTE="gpu-host RTX 3090 24GB, vLLM $VER, $REPO rev $REV bf16 (unquantized), --cpu-offload-gb $OFFLOAD, --enable-lora r16 (adapter sha256 $ASHA), base and LoRA arms batched on one server; thinking OFF; no guided decoding (as Mac eval)"
python3 scripts/v02/val_eval_concurrent.py --val $VAL --dataset qwen30b-bf16-val-base --model $REPO --base-url $URL --note "$NOTE" & A=$!
python3 scripts/v02/val_eval_concurrent.py --val $VAL --dataset qwen30b-bf16-val-lora-r16 --model $LORA --base-url $URL --note "$NOTE" & B=$!
wait $A $B
log "eval done after $(( $(date +%s)-T0 ))s"
python3 scripts/v02/score_teacher_forced.py data/v02/qwen30b-bf16-val-base data/v02/qwen30b-bf16-val-lora-r16
ssh -o BatchMode=yes "$GPU_HOST" 'kill $(cat /tmp/qwen-serve.pid) 2>/dev/null'
log "server stopped"
