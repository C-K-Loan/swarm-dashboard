#!/usr/bin/env bash
# CKL plan v2 2026-10-30: once master's redeployed Modal server lists lora-mt2, run the 131-row teacher-forced val eval for it
# and send the base | mt1 | mt2 table (base and mt1 rows from the 2026-10-29 bf16 H100 run).
set -u
cd .
URL=${MODAL_ENDPOINT:?set MODAL_ENDPOINT to your Modal vLLM base URL (.../v1)}
VAL=data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k/validation.jsonl
NOTE="Modal H100 (CKL account, redeployed by project-manager with lora-mt1 + lora-mt2), vLLM 0.31.0, Qwen/Qwen3-30B-A3B-Instruct-2507 bf16 unquantized, LoRA served as lora-mt2; label modal-h100-bf16; thinking OFF"
S=${TMPDIR:-/tmp}/modal-eval; mkdir -p $S
ready(){ python3 - <<'EOF'
import json,os,urllib.request
k=os.environ['MODAL_VLLM_KEY']
r=urllib.request.urlopen(urllib.request.Request(os.environ['MODAL_ENDPOINT']+'/models',headers={'Authorization':'Bearer '+k}),timeout=120)
raise SystemExit(0 if 'lora-mt2' in [m['id'] for m in json.load(r)['data']] else 1)
EOF
}
until ready 2>/dev/null; do sleep 30; done
echo "{\"mt2\":\"ready\",\"at\":\"$(date -u +%FT%TZ)\"}"
python3 scripts/v02/val_eval_concurrent.py --val $VAL --dataset qwen30b-bf16-val-lora-mt2-modal --model lora-mt2 --base-url $URL --api-key-env MODAL_VLLM_KEY --note "$NOTE" | tail -1
{ echo "Qwen lane HEADLINE v2: bf16 H100 teacher-forced val, 131 held-out rows (teacher Haiku 4.5 lean, never trained on):"
  python3 scripts/v02/val_table.py base=qwen30b-bf16-val-base-modal mt1=qwen30b-bf16-val-lora-r16-modal mt2=qwen30b-bf16-val-lora-mt2-modal
  echo "Dataset: data/v02/qwen30b-bf16-val-lora-mt2-modal. Modal app left up for you (scales to 0 after 15 min idle; hard stop 03:09Z)."; } > $S/mt2-msg.txt
cat $S/mt2-msg.txt
CCT_SEND_FAST=1 cct send --from swarm-dashboard-qwen swarm-dashboard-qwen --file $S/mt2-msg.txt || true
