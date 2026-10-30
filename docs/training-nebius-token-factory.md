# Training the state-graph LoRA on Nebius Token Factory

This page records how the two released adapters were made: export, upload, job parameters, timing, download and serving. It ends with the cost lesson. Read that section before you launch anything.

| | LoRA v1 | LoRA v2 |
|---|---|---|
| Base | `Qwen/Qwen3-30B-A3B-Instruct-2507` | same |
| Method | LoRA r 16, alpha 32, dropout 0.05, targets `q_proj k_proj v_proj o_proj` | same |
| Training data | `sft/v1-8k-m64`: 1653 rows, 8.35 M tokens | `sft/v2-8k-fit`: 1908 rows, 9.71 M tokens |
| Teachers | Claude Haiku 4.5 (1001 rows) + Nemotron 3 Ultra (652 rows) | Haiku 4.5 + Nemotron 3 Ultra |
| Epochs | 1 | 2 |
| Learning rate / batch | 1e-4 / 16 | 1e-4 / 16 |
| Context / packing | 8192 / on | 8192 / on |
| Final train / val loss | 0.105 / 0.190 (step 89) | 0.086 / 0.171 (step 210) |
| Wall time on Token Factory | ~16 min | ~22 min |
| Released as | `cct-v03-lean-qwen3-30b-a3b-lora-r16-v1` | `cct-v03-lean-qwen3-30b-a3b-lora-r16-v2` |

Both trained on source turns below 1201 and validated on the held-out turns 1203 to 1335 (131 rows that Haiku labeled). The held-out eval in [eval.md](eval.md#state-graph) uses those validation turns.

## 1. Export the SFT set

`scripts/v02/export_multi_teacher.py` replays every teacher ledger through its own graph, rebuilds the v0.3-lean prompt, checks that it is byte-identical to what the teacher saw, alias-encodes the target patch and checks the round trip. Rows that fail a check are refused. It never uploads or calls a model. It needs the teacher runs in `data/v02/` (released under `labels/`) and the `transformers` tokenizer for `Qwen/Qwen3-30B-A3B-Instruct-2507`.

```bash
# v1: export at 8192, then strict-drop rows whose chat-templated length exceeds 8128 (64-token margin)
python3 scripts/v02/export_multi_teacher.py --max-tokens 8192 --name qwen3-30b-a3b-lora-v1-8k
python3 scripts/v02/export_multi_teacher.py --filter-from data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k \
  --max-tokens 8128 --name qwen3-30b-a3b-lora-v1-8k-m64

# v2: trim long contexts step by step (FIT_LADDER) until prompt + 1024 output tokens fit 8128, same teacher snapshot
python3 scripts/v02/export_multi_teacher.py --max-tokens 8128 --fit-reserve 1024 \
  --snapshot-from data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k-m64 --name qwen3-30b-a3b-lora-v1-8k-fit
```

Reproduced from the public dataset (after `scripts/setup_public_data.sh`): the v1 commands above, with `--snapshot-from data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k-m64` on the first one, regenerate all 1653 released training rows byte-identically (messages), plus 10 rows that were originally just over the 8128-token limit and fit after sanitization shortened their text; 129 of 131 validation rows are identical. Without the snapshot cap the exporter stops at turn 1665, where a removed session's turn is missing from the teachers' graph history.

Output per export: `train.jsonl`, `validation.jsonl` (OpenAI chat format, one `messages` triple per row) and `manifest.json` (hashes, split, per-teacher stats, dropped rows). The released files were sanitized after training, so their sha256 no longer equals the hashes in the training cards.

## 2. Upload the files

Token Factory speaks the OpenAI files and fine-tuning API. Keep the key in `.env` (see `.env.example`), never on the command line history of a shared machine.

```bash
set -a; . ./.env; set +a
TF=https://api.tokenfactory.nebius.com/v1
D=data/v02/_multi-teacher/qwen3-30b-a3b-lora-v1-8k-fit
curl -s "$TF/files" -H "Authorization: Bearer $NEBIUS_TOKEN_FACTORY_KEY" -F purpose=fine-tune -F file=@$D/train.jsonl
curl -s "$TF/files" -H "Authorization: Bearer $NEBIUS_TOKEN_FACTORY_KEY" -F purpose=fine-tune -F file=@$D/validation.jsonl
# note both "id" values: TRAIN_ID, VAL_ID
```

## 3. Create the job

**Get the price from the console first** (section 6). Then:

```bash
curl -s "$TF/fine_tuning/jobs" -H "Authorization: Bearer $NEBIUS_TOKEN_FACTORY_KEY" -H "Content-Type: application/json" -d '{
  "model": "Qwen/Qwen3-30B-A3B-Instruct-2507",
  "suffix": "cct-v03-lean-30b-r16",
  "training_file": "'"$TRAIN_ID"'",
  "validation_file": "'"$VAL_ID"'",
  "seed": 42,
  "hyperparameters": {
    "lora": true, "lora_r": 16, "lora_alpha": 32, "lora_dropout": 0.05,
    "learning_rate": 1e-4, "batch_size": 16, "n_epochs": 2,
    "context_length": 8192, "packing": true,
    "warmup_ratio": 0.0, "weight_decay": 0.0, "max_grad_norm": 1.0
  }
}'
```

Use `"n_epochs": 1` and the `v1-8k-m64` files to reproduce v1. The same request body, with the V0.1 Qwen3-14B settings, is in `scripts/train_sft.py` (one job, polls to completion) and `scripts/fire_sft_jobs.py` (several jobs in parallel); both read the key from `.env`.

Notes from the runs:
- `"lora": false` (full fine-tuning) is gated: such a job queues forever without starting. LoRA is the path that works end to end.

## 4. Watch and download

```bash
curl -s "$TF/fine_tuning/jobs/$JOB_ID" -H "Authorization: Bearer $NEBIUS_TOKEN_FACTORY_KEY"              # status, trained_tokens
curl -s "$TF/fine_tuning/jobs/$JOB_ID/checkpoints" -H "Authorization: Bearer $NEBIUS_TOKEN_FACTORY_KEY"  # per-step losses
curl -s "$TF/files/$FILE_ID/content" -H "Authorization: Bearer $NEBIUS_TOKEN_FACTORY_KEY" -o adapter.tar  # each result file
```

`scripts/download_sft_checkpoints.py` downloads every result file of the jobs listed in `data/sft/sft_final.json` into `data/sft/checkpoints/<label>/`. A finished adapter folder holds `adapter_config.json`, `adapter_model.safetensors` (26.8 MB at r 16), `tokenizer_config.json`, `tokenizer.json`, `chat_template.jinja` and `checkpoint.meta` (checkpoint id, step, losses). That is exactly what the Hugging Face model repos contain.

## 5. Serving

Token Factory trains LoRAs on Qwen3-30B-A3B but could not serve the resulting fine-tune for inference at the time of these runs. The adapters were therefore served with vLLM on one Modal H100, in bf16 (no quantization), base and both adapters behind one OpenAI-compatible endpoint:

```bash
# put the adapters at data/sft/checkpoints/qwen30b_r16_mt1 and .../qwen30b_r16_mt2 first
modal secret create vllm-key MODAL_VLLM_KEY=<choose-a-key>
modal deploy scripts/v02/modal_lora_serve.py      # prints the https URL; first start downloads ~61 GB of weights
export MODAL_ENDPOINT=https://<your-app>.modal.run/v1 MODAL_VLLM_KEY=<the-key>
curl -s "$MODAL_ENDPOINT/models" -H "Authorization: Bearer $MODAL_VLLM_KEY"   # base, lora-mt1, lora-mt2
modal app stop qwen30b-lora-eval                  # GPU-hour billing: stop it when done
```

Server settings (`modal_lora_serve.py`): image `vllm/vllm-openai:v0.31.0`, `--dtype bfloat16 --max-model-len 16384 --enable-lora --max-lora-rank 16 --max-loras 2`, GPU memory utilization 0.92, weights cached on a Modal volume, 15 min scale-down, one container. Clients request `model: "lora-mt2"` (v2), `"lora-mt1"` (v1) or the base id; thinking is off for every eval.

Any vLLM with LoRA support works the same way locally:

```bash
vllm serve Qwen/Qwen3-30B-A3B-Instruct-2507 --dtype bfloat16 --max-model-len 16384 \
  --enable-lora --max-lora-rank 16 --lora-modules lora-mt2=/path/to/cct-v03-lean-qwen3-30b-a3b-lora-r16-v2
```

The bf16 base needs about 61 GB of GPU memory (one 80 GB card, or `--cpu-offload-gb` on a smaller one; `scripts/v02/gpu_host_bf16_val.sh` shows a 24 GB card with offload, which is slow).

## 6. The cost lesson

**Token Factory exposes no training price through its API.** The job object has no estimated or final cost field, and there is no pricing endpoint for fine-tuning. `train_sft.py` has a guard that stops a job when a cost field exceeds a cap; on Token Factory that guard never fires, because the field does not exist.

What happened: the inference spend for this project was small and tracked per call (at most USD 1.40 per labeling run, see the spend ledgers in `eval-summary.json`). The two LoRA jobs above were launched on the strength of those numbers. Their bill showed up only in the console afterwards, and it was many times the whole inference spend.

Before any training job:

1. Open the Token Factory console, pick the base model and read the fine-tuning price (per token or per job).
2. Compute `train tokens x epochs x price`. Train tokens are in each export's `manifest.json` (`stats.train.*.tokens`): 8.35 M for v1, 9.71 M for v2.
3. Write the figure down, with a screenshot of the console price, before you send the create request.
4. Afterwards, compare with the console bill. Nothing else will tell you.
