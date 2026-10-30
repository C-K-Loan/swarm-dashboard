# Evaluation

What every number on the dashboard's **eval -> Summary** tab means, where it comes from, and how to recompute it. The Summary tab reads one JSON (`GET /api/v02/eval-summary`, built by `scripts/v02/eval_summary.py`), plus two V0.1/V0.1.B files. Nothing on it is typed by hand except the run roster and the training cards (`data/v02/_eval/training-cards.json`).

All agreement numbers in this project are **agreement with a reference model**, not verified truth. No human-labeled ground truth exists for any stage.

## Headline numbers (as rendered, data generated 2026-10-30)

| Tile | Shown | Definition |
|---|---|---|
| Stage 1 · Prose v1 | **55.6% per-field agreement** (Qwen3-30B-A3B vs Claude Opus labels, mean of 5 fields; majority baseline **59.1%**) | share of messages where a field equals the Opus label, averaged over the 5 fields |
| Stage 2 · Prose v2 | **94% F1 · ~90% cheaper** (vs Haiku merges, Qwen3-235B, lenient F1; strict 0.89) | merge F1 vs the Claude Haiku 4.5 merge; cost per 1k merged nodes |
| State graph · v0.3 lean | **59% agreement · ~95% cheaper** (touched-task F1 vs Haiku, LoRA v2, relationships 34% of Haiku's) | touched-task F1 on 131 held-out turns; cost per 1k turns |
| Overall · Stage 2 + state graph | **~92% cheaper** | mean of the two stage savings (unweighted): (89.8% + 94.8%) / 2 |

`~` marks list-price estimates (tokens x list rate), not invoices.

## Stage 1

**V0.1 per-message labels**, 5 fields (enums in [data-formats.md](data-formats.md#v01-per-message-labels-stage-1)).

**Reference:** 1000 messages (800 agent, 200 user) labeled by Claude Opus 5.5. `agent_intent`, `agent_role` and `blocker_type` are scored on agent messages only.

**Metrics** (`stage1_opus()` in `eval_summary.py`):
- *accuracy* per field: exact match, `null` counts as a value;
- *macro-F1* per field: mean F1 over the classes seen in either labeling, `null` excluded as a class; `agent_role` is a set (exact set match for accuracy, per-role F1);
- *mean acc* / *mean F1*: unweighted mean over the 5 fields;
- *all fields*: share of messages with every applicable field right;
- *majority label*: always predict the most common Opus value per field (`feature-def`, `report-progress`, `["dev"]`, `null`, `low`).

| Model | Mean acc | Mean macro-F1 | All fields |
|---|---|---|---|
| majority label | 59.1% | 0.12 | 9.7% |
| **Qwen3-30B-A3B (production Stage 1)** | **55.6%** | **0.30** | 7.7% |
| Gemma-3-27B | 54.2% | 0.31 | 9.3% |
| DeepSeek-V4-Flash | 53.7% | 0.36 | 8.9% |
| Qwen3-235B | 52.0% | 0.33 | 6.9% |
| Nemotron-30B | 43.8% | 0.29 | 4.0% |
| Claude Haiku (for scale) | 71.6% | 0.62 | 24.4% |
| Claude Sonnet (for scale) | 71.5% | 0.58 | 23.3% |
| Claude Fable (for scale) | 73.7% | 0.65 | 26.6% |

**Reading it honestly:** all 5 open models score *below* always-predict-the-majority on accuracy and above it on macro-F1. The fields are dominated by one class, so accuracy rewards guessing it. Stage 1 is the weakest stage. The Qwen3-14B LoRAs trained for Stage 1 were not run on the Opus set. No cost is on record for this set, so Stage 1 is left out of the cost scatter and of "Overall".

**Reproducibility:** Stage 1 was evaluated on a private 1,000-message set labeled by Claude Opus (not released); a 30-message gamejam subset is included for reproduction. It is `stage1-gamejam/` in the dataset: `gold_v2.jsonl` (sanitized text + Opus 5.5 labels) and `ref_<model>.jsonl` (each model's predictions on the same rows). The same scorer reproduces this table:

```bash
python3 -c "import sys; sys.path.insert(0, 'scripts/v02'); from pathlib import Path; import eval_summary as E, json; \\
print(json.dumps(E.stage1_opus(Path('data/eval-stage1-gamejam')), indent=1))"     # after copying stage1-gamejam/ there
```

| Model (game-jam subset, n = 30) | Mean acc | Mean macro-F1 | All fields |
|---|---|---|---|
| majority label | 58.4% | 0.14 | 6.7% |
| **Qwen3-30B-A3B (production)** | **56.5%** | 0.27 | 3.3% |
| Gemma-3-27B | 60.4% | 0.28 | 13.3% |
| Qwen3-235B | 56.1% | 0.34 | 13.3% |
| DeepSeek-V4-Flash | 55.9% | 0.25 | 3.3% |
| Nemotron-30B | 47.7% | 0.25 | 3.3% |
| Claude Sonnet | 73.5% | 0.48 | 26.7% |
| Claude Haiku | 70.9% | 0.41 | 30.0% |
| Claude Fable | 73.1% | 0.48 | 30.0% |

The majority baseline here is computed on the 30-message subset.

The older V0.1 exact-match table (Qwen3-14B base 71.1%, LoRA r16 87.2%, LoRA r32 85.1%) is scored against **Qwen3-30B-A3B pseudo-labels** on 685 messages, where the majority baseline is 92.6%. It measures imitation of the labeler, not quality, and the dashboard labels it that way.

## Stage 2

**V0.1.B hierarchical merge.** Reference: the same tree merged by Claude Haiku 4.5 (`v01b/gamejam-day1-haiku`). Every merger sees the same nodes.

**Metric:** lenient F1 per level. A candidate bullet is a hit if it matches a Haiku bullet or is supported by its own cited source bullets; recall is over Haiku's bullets. Judge: Claude Sonnet 4.5 (not a merger, but the same vendor as the reference). *Strict* precision counts only matches to Haiku bullets. The tile shows the mean over the four levels `row-4h`, `row-day`, `col-1h`, `col-4h` (`col-day` is one node and is left out).

| Merger | row-4h | row-day | col-1h | col-4h | Mean (shown) |
|---|---|---|---|---|---|
| **Qwen3-235B** | 0.960 | 0.924 | 0.939 | 0.932 | **0.94** |
| Qwen3-8B | 0.835 | 0.811 | 0.665 | 0.711 | 0.76 |
| Llama-3.1-8B | 0.683 | 0.632 | 0.601 | 0.442 | 0.59 |
| Haiku 4.5 (reference) | | | | | 1.00 |

Strict F1 for Qwen3-235B: 0.89.

**Recompute:**

```bash
python3 - <<'EOF'
import json
a = json.load(open('data/v01b/gamejam-day1-haiku/agreement_report.json'))
for d in ['gamejam-day1-q235b', 'gamejam-day1', 'gamejam-day1-llama8b']:
    v = [a['levels'][l][d]['f1'] for l in ['row-4h', 'row-day', 'col-1h', 'col-4h']]
    print(d, round(sum(v) / len(v), 2))
EOF
```

**Cost (~90% cheaper):** USD per 1k merged nodes. Qwen3-235B: tokens x Token Factory list rate = USD 0.62. Haiku merges: subscription tokens x the plain Haiku input/output list rate = USD 6.09. Saving = 1 - 0.62 / 6.09 = 0.898.

## State graph

**v0.3-lean patches.** Reference: Claude Haiku 4.5 (`haiku45-lean-day1`). Held-out set: the 131 validation turns 1203 to 1335 that Haiku labeled (never trained on; training used turns below 1201).

**Protocol: teacher-forced.** For every held-out turn the model gets exactly the exported prompt (`sft/*/validation.jsonl`: system + lean context built from *Haiku's* graph before that turn) and writes one patch. The patch is decoded and validated with the same strict validator. An invalid or missing patch counts as an empty patch. (`scripts/v02/val_eval_concurrent.py` -> `teacher-forced.jsonl`; scored by `scripts/v02/score_teacher_forced.py`.)

**Metrics:**
- *valid output*: share of turns whose patch passes validation within the allowed corrections;
- *touched-task F1*: per turn, F1 between the sets of existing tasks the model and Haiku touched; a turn where neither touches a task scores 1.0; averaged over turns. Only tasks that exist before the turn are comparable (new tasks have patch-local ids);
- *touched F1 on teacher-touched turns*: the same, only over the 98 turns where Haiku touched at least one task;
- *relationships / turn*: relationships emitted per turn (Haiku: 0.47);
- *status agreement*, *actor state / role match*, *new-task count difference*: in `eval-summary.json` -> `val`.

| Metric | Base | LoRA v1 | **LoRA v2** |
|---|---|---|---|
| Valid output | 77.1% | 88.5% | **93.9%** |
| Touched-task F1 | 37.7% | 57.2% | **58.7%** |
| Touched F1, 98 teacher-touched turns | 17.7% | 57.1% | **58.0%** |
| Relationships / turn (Haiku 0.47) | 0.00 | 0.07 | **0.16** |

Other untuned models on the same 131 turns: NVIDIA Nemotron 3 Ultra 64.6% touched-task F1 (99.2% valid); Qwen3-235B fp8 (OpenRouter) 57.8% (93.9% valid). Both are in the dataset's `eval/`.
| Actor role match | 36.6% | 97.6% | 97.6% |
| Actor state match | 36.6% | 22.0% | 26.8% |

Base = Qwen3-30B-A3B-Instruct-2507, bf16. All three arms ran on the same Modal H100 vLLM server (`scripts/v02/modal_lora_serve.py`), thinking off.

**Recompute:**

```bash
python3 scripts/v02/score_teacher_forced.py data/v02/qwen30b-bf16-val-base-modal \
  data/v02/qwen30b-bf16-val-lora-r16-modal data/v02/qwen30b-bf16-val-lora-mt2-modal   # one JSON per arm
python3 scripts/v02/eval_summary.py > /tmp/eval-summary.json                            # the whole Summary payload
```

The public dataset holds all 131 held-out turns for every arm, so the scorer reproduces the table above exactly.


**Re-run the predictions** (needs your own vLLM endpoint with the adapters, see [training-nebius-token-factory.md](training-nebius-token-factory.md#serving)): `scripts/v02/modal_h100_eval.sh` (base + v1) and `scripts/v02/modal_mt2_val.sh` (v2). Both read `MODAL_ENDPOINT` and `MODAL_VLLM_KEY`.

**Rollouts (not teacher-forced).** The leaderboard on the v0.3 lean tab shows causal rollouts, where each model builds its own graph over Day 1 (`scripts/v02/window_metrics.py`: valid share, calls/turn, relationships/turn, task updates/turn, USD, throughput). These count output only; they are not quality scores. On the held-out slice 1523 to 1902 the base emits 0.02 relationships/turn and LoRA v1 0.17. LoRA v2 was not rolled out.

**Cost (~95% cheaper, hypothetical):** a token-price comparison, not a bill: the LoRA is priced at the Qwen3-30B-A3B Token Factory base-model rate, but it actually ran on Modal GPU-hours, and its prompt differs from Haiku's. USD per 1k turns, list-price estimates. Haiku: same turns, ledger tokens x plain Haiku rate, including its harness prompt and retries = USD 14.06. LoRA v2: its own tokens x the Qwen3-30B-A3B Token Factory rate = USD 0.73. Saving = 0.948. If Haiku is priced on the LoRA's own prompt tokens instead, the saving is ~91%. The LoRA actually ran on Modal GPU-hours, which have no per-token ledger.

## Caveats

1. Every reference is a model. Stage 1: Opus 5.5. Stage 2 and the state graph: Haiku 4.5. The Stage 2 judge is from the same vendor as its reference.
2. The state-graph validation turns (1203-1335) come from the same day as training; they are later in time, with an embargo, but not a different project.
3. Teacher-forced scores give the model the teacher's graph. A causal rollout compounds errors and scores lower.
4. "Cheaper" is computed from list prices and token counts. The real bills (Token Factory training, Modal GPU-hours, subscriptions) are not in these numbers.
5. Stage 1 is not better than the majority baseline on accuracy.
6. Four short agent sessions (shell commands and acknowledgements, 7 turns) were removed from the release as off-topic. Stage 2 column merges were computed with them included.
