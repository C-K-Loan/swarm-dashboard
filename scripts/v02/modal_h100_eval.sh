#!/usr/bin/env bash
# CKL GO 2026-10-30: bf16 Qwen3-30B-A3B-Instruct-2507 base vs r16 LoRA on master's Modal H100 vLLM (scripts/v02/modal_lora_serve.py).
# 1) 131 held-out val rows teacher-forced, both arms concurrently; 2) both arms full Day1 causal rollouts in parallel.
# Cost guard: GPU-hour billing, so a wall-clock hard stop (unit swarm-modal-hardstop) plus `modal app stop` at the end.
set -u
cd .
URL=${MODAL_ENDPOINT:?set MODAL_ENDPOINT to your Modal vLLM base URL (.../v1)}
BASE=Qwen/Qwen3-30B-A3B-Instruct-2507; LORA=lora-mt1
VAL=data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k/validation.jsonl
NOTE="Modal H100 (CKL account, deployed by project-manager), vLLM 0.31.0, $BASE bf16 unquantized, --enable-lora r16 mt1 adapter served as $LORA, max_model_len 16384; label modal-h100-bf16; thinking OFF"
S=${TMPDIR:-/tmp}/modal-eval; mkdir -p $S
log(){ echo "{\"modal_eval\":\"$*\",\"at\":\"$(date -u +%FT%TZ)\"}"; }
send(){ CCT_SEND_FAST=1 cct send --from swarm-dashboard-qwen swarm-dashboard-qwen --file "$1" || true; }  # detached unit: carry a sender

ready(){ python3 - <<'EOF'
import json,os,urllib.request
k=os.environ['MODAL_VLLM_KEY']
r=urllib.request.urlopen(urllib.request.Request(os.environ['MODAL_ENDPOINT']+'/models',headers={'Authorization':'Bearer '+k}),timeout=120)
ids=[m['id'] for m in json.load(r)['data']];print(ids);raise SystemExit(0 if 'lora-mt1' in ids else 1)
EOF
}
for i in $(seq 1 60); do ready && break; sleep 30; done
ready || { log "endpoint never ready"; exit 1; }
T0=$(date +%s); log "endpoint ready"

if [ ! -e $S/val-done ]; then
  python3 scripts/v02/val_eval_concurrent.py --val $VAL --dataset qwen30b-bf16-val-base-modal --model $BASE --base-url $URL --api-key-env MODAL_VLLM_KEY --note "$NOTE" & A=$!
  python3 scripts/v02/val_eval_concurrent.py --val $VAL --dataset qwen30b-bf16-val-lora-r16-modal --model $LORA --base-url $URL --api-key-env MODAL_VLLM_KEY --note "$NOTE" & B=$!
  wait $A $B
  log "val eval done in $(( $(date +%s)-T0 ))s"
  python3 scripts/v02/score_teacher_forced.py data/v02/qwen30b-bf16-val-base-modal data/v02/qwen30b-bf16-val-lora-r16-modal > $S/val-scores.json
  python3 - <<'EOF' > $S/val-report.txt
import json
from pathlib import Path
R=Path('data/v02')
txt=Path('${TMPDIR:-/tmp}/modal-eval/val-scores.json').read_text();dec=json.JSONDecoder();i=0;S=[]
while i<len(txt.strip()):
 o,j=dec.raw_decode(txt,i);S.append(o);i=j
 while i<len(txt) and txt[i].isspace():i+=1
def rels(name):
 rows=[json.loads(x) for x in (R/name/'teacher-forced.jsonl').read_text().splitlines() if x.strip()]
 return sum(len((r.get('patch') or {}).get('relationships',[])) for r in rows if r['valid'])/len(rows),sum(len(r['teacher_patch'].get('relationships',[])) for r in rows)/len(rows)
keys=[('valid_rate','valid'),('calls_per_turn','calls/turn'),('actor_state_match','actor state'),('actor_role_match','actor role'),('touched_existing_f1','touched-task F1'),('touched_existing_p','  P'),('touched_existing_r','  R'),('status_agreement','status agree'),('existing_rel_f1','rel F1 (existing refs)'),('new_tasks_abs_diff_mean','new-task count |err|')]
print('bf16 H100 teacher-forced val (131 held-out rows, teacher Haiku 4.5 lean, never trained on)')
print('metric | base | LoRA r16')
for k,n in keys:print(f'{n} | {S[0][k]} | {S[1][k]}')
b,t=rels(S[0]['dataset']);l,_=rels(S[1]['dataset'])
print(f'rels/turn | {b:.3f} | {l:.3f}   (teacher {t:.3f})')
EOF
  { echo "Qwen lane HEADLINE (bf16, exact training precision):"; cat $S/val-report.txt; echo "Datasets: data/v02/qwen30b-bf16-val-base-modal, qwen30b-bf16-val-lora-r16-modal. Rollouts (both arms, full Day1) starting now."; } > $S/val-msg.txt
  send $S/val-msg.txt; touch $S/val-done
fi

roll(){ # $1 dataset, $2 model
  for try in 1 2 3 4 5; do
    python3 scripts/v02/local_qwen_day1.py --run --renderer v0.3-lean --structured --quote-spans --no-thinking --source day1 \
      --max-corrections 2 --max-tokens 2048 --ctx 16384 --publish-every 25 --dataset "$1" --model "$2" --base-url $URL \
      --api-key-env MODAL_VLLM_KEY --hourly-billed --serving-note "$NOTE; json_schema guided decoding" \
      --weights-note "$BASE bf16$([ "$2" = "$LORA" ] && echo " + LoRA cct-v03-lean-30b-r16-mt1")" && return 0
    [ -e "data/v02/$1/STOP" ] && return 0; sleep 30
  done
}
roll qwen30b-bf16-base-lean-day1-modal $BASE & A=$!
roll qwen30b-bf16-lora-r16-lean-day1-modal $LORA & B=$!
wait $A $B
log "rollouts done in $(( $(date +%s)-T0 ))s"
modal app stop --yes qwen30b-lora-eval && log "modal app stopped"
