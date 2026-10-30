#!/usr/bin/env bash
# CKL plan B (2026-10-29): untrained Qwen3.5-4B on Day1 turns 1-500, then Qwen3.5-9B on turns 1-250.
# MLX bf16 unquantized on the MacBook Air, thinking OFF, served on laptop 127.0.0.1:8090 behind the
# swarm-mac-qwen-tunnel unit (ckl 127.0.0.1:18091). $0. Run as a systemd user unit.
set -u
LAPTOP_HOST=${LAPTOP_HOST:?set LAPTOP_HOST to the ssh host of your Apple-silicon laptop}
cd .
URL=http://127.0.0.1:18091/v1
VENV=${LAPTOP_PYTHON:-python3}
log(){ echo "{\"plan_b\":\"$*\",\"at\":\"$(date -u +%FT%TZ)\"}"; }

serve(){ # $1 = HF repo
  ssh -o BatchMode=yes "$LAPTOP_HOST" "kill \$(cat /tmp/qwen-serve.pid 2>/dev/null) \$(cat /tmp/qwen-serve.pid 2>/dev/null) 2>/dev/null; sleep 5; cd ~ && (nohup caffeinate -i $VENV -m mlx_lm server --model $1 --host 127.0.0.1 --port 8090 --prompt-cache-size 1 --prompt-cache-bytes 1000000000 > /tmp/qwen-serve.log 2>&1 & echo \$! > /tmp/qwen-serve.pid)"
  for i in $(seq 1 60); do curl -s -m 5 "$URL/models" >/dev/null && return 0; sleep 10; done
  log "server for $1 did not come up"; return 1
}

hash_weights(){ # $1 = HF repo, $2 = output json
  ssh -o BatchMode=yes "$LAPTOP_HOST" "$VENV - <<'EOF'
from huggingface_hub import snapshot_download,HfApi
import hashlib,os,json,glob
repo='$1';rev=HfApi().model_info(repo).sha;d=snapshot_download(repo,revision=rev)
def h(p):
  s=hashlib.sha256()
  with open(p,'rb') as f:
    for b in iter(lambda:f.read(1<<20),b''):s.update(b)
  return s.hexdigest()
sh=sorted(glob.glob(d+'/*.safetensors'))
print(json.dumps({'repo':repo,'revision':rev,'host':'mac (MacBook Air M3, 24 GB)','config_sha256':h(d+'/config.json'),'index_sha256':h(d+'/model.safetensors.index.json') if os.path.exists(d+'/model.safetensors.index.json') else None,'first_shard':[os.path.basename(sh[0]),h(sh[0])],'last_shard':[os.path.basename(sh[-1]),h(sh[-1])],'n_shards':len(sh),'bytes':sum(os.path.getsize(s) for s in sh),'runtime':'mlx-lm 0.32.2 (python 3.12), unquantized bf16, thinking OFF'}))
EOF" > "$2"
}

run(){ # $1 dataset, $2 repo, $3 stop-at, $4 weights json
  python3 scripts/v02/local_qwen_day1.py --run --no-thinking --record-failures --max-corrections 1 --max-accepted 1902 --max-tokens 1280 \
    --stop-at "$3" --dataset "$1" --model "$2" --base-url "$URL" \
    --serving-note "mac MacBook Air M3 24GB, mlx-lm 0.32.2 server under caffeinate, bf16 unquantized, ssh tunnel; thinking OFF" \
    --weights-note "$4"
}

log "4B to 500"
serve Qwen/Qwen3.5-4B || exit 1
run qwen35-4b-base-day1 Qwen/Qwen3.5-4B 500 "data/v02/_mac-weights/qwen35-4b.json (rev 851bf6e)"
log "download + hash 9B"
hash_weights Qwen/Qwen3.5-9B data/v02/_mac-weights/qwen35-9b.json || { log "9B download failed"; exit 1; }
serve Qwen/Qwen3.5-9B || exit 1
log "9B to 250"
run qwen35-9b-base-day1 Qwen/Qwen3.5-9B 250 "data/v02/_mac-weights/qwen35-9b.json"
ssh -o BatchMode=yes "$LAPTOP_HOST" 'kill $(cat /tmp/qwen-serve.pid) 2>/dev/null'
log "done"
cct send --from swarm-dashboard-qwen swarm-dashboard-qwen "Qwen lane MILESTONE: mac plan B finished (4B turns 1-500, 9B turns 1-250, thinking OFF). Mac server stopped. Results in data/v02/qwen35-4b-base-day1 and qwen35-9b-base-day1." || true
