#!/usr/bin/env bash
# Lay out the public Hugging Face dataset (wow-cod-thps4-gamejam) as the data/ tree the code expects. Local only.
# usage: scripts/setup_public_data.sh /path/to/wow-cod-thps4-gamejam
set -euo pipefail
HF=${1:?usage: scripts/setup_public_data.sh /path/to/wow-cod-thps4-gamejam}
cd "$(dirname "$0")/.."
mkdir -p data/v02/_eval data/v02/_multi-teacher data/v02/_sender-attribution data/v01b
cp -r "$HF/source" data/v02/gamejam-full-anchored
mv data/v02/gamejam-full-anchored/sender-attribution.json data/v02/_sender-attribution/gamejam-full-anchored.json
for r in "$HF"/labels/*/ "$HF"/eval/*/; do cp -r "$r" data/v02/; done
cp "$HF/eval/training-cards.json" data/v02/_eval/
cp "$HF"/spend/*.json data/v02/
cp -r "$HF/sft/v1-8k-m64" data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k-m64
cp -r "$HF/sft/v1-8k-m64" data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k   # the val scripts read this name; same rows
cp -r "$HF/sft/v2-8k-fit" data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k-fit
cp "$HF/prose-v01/bucket_summaries.jsonl" data/
cp -r "$HF"/prose-v01b/* data/v01b/
cp -r "$HF/stage1-gamejam" data/eval-stage1-gamejam
cp -r "$HF/v01-eval" data/v01-eval
python3 scripts/v02/restore_run_turns.py                                      # each run's turns.json from the shared source
echo "data/ ready"
