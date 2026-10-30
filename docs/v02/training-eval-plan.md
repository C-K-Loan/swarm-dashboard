# V0.2 Training & Evaluation Refactor Plan

Preparation only. No API inference, remote uploads, training jobs, or paid spend
are authorized by this document.

## 1. Current-state audit

### 1.1 What V0.2 predicts

V0.2 is a **state-graph delta predictor**, not a V0.1-style 5-field classifier.
Per turn, the model receives graph context (actors, tasks, decisions,
relationships) plus current source prose and emits a `turn-patch` JSON delta
validated against `schemas/v02/turn-patch.schema.json`. Graph state is
reconstructed deterministically by replaying the ledger.

Shape:

- `actor_patch`: role, state deltas with evidence
- `tasks[]`: resolution (`new`/`existing`/`ambiguous`), field changes, evidence
- `decisions[]`: summary, affected tasks, importance, actor, evidence
- `relationships[]`: typed edges (`delegates_to`, `works_on`, `reviews`,
  `depends_on`, `is_sub_task`) with evidence
- Every change requires a verbatim current-turn quote

### 1.2 Data as of this audit

| asset | state |
|---|---|
| `data/v02/active.json` | `{"dataset": "day1"}` |
| `data/v02/day1/turns.json` | 1,902 completed turns, 34 sessions, one gamejam window (2026-10-10/11 UTC) |
| `data/v02/day1/predictions.jsonl` | 67 machine_draft rows by `gpt-6-luna` (CCT engine `codex`, effort low) |
| `data/v02/day1/reviews.jsonl` | absent — zero human reviews |
| `data/v02/day1/manifest.json` | `teacher_model: gpt-6-luna`, `label_status: machine_draft`, `root_turn_id: turn-0007` |

Consequences for the current scripts:

- `export_training.py` would produce zero training pairs — it stops at the first
  turn without an `approved`/`corrected` review, and there are none.
- `evaluate.py` would report zero reviewed turns.

### 1.3 Current training pipeline (`scripts/v02/export_training.py`)

- One chat example per turn: `system` = `prompts/v02/extract-turn.txt`, `user` =
  context JSON, `assistant` = reviewed patch JSON.
- Chronological 80/20 split with a 2-turn embargo between train and validation
  when at least 10 reviewed turns exist.
- Reads `review['correction'] or row['patch']`, so corrected deltas replace
  machine drafts; approved deltas pass through unchanged.
- Stops at the first non-reviewed turn, preserving a contiguous reviewed prefix.
- Manifest records `training_allowed: False` and a SHA-256 of the pair list.

### 1.4 Current evaluation pipeline (`scripts/v02/evaluate.py`)

Mode: **human_review_audit of saved predictions**, explicitly not an independent
held-out benchmark and explicitly not newly-computed teacher-forcing inference.
It compares the already-saved ledger patches to the already-saved review
corrections. No model is called during `evaluate.py`.

Metrics implemented:

- Task set metrics: `tp/fp/fn` for common/extra/missing refs (ref-based match)
- Relationship set metrics: tuple-based `(type, from_ref, to_ref, task_ref)`
- Decision set metrics: ref-based
- Task identity: `resolution` match on common tasks
- Field macro-F1 on common tasks: `task.status`, `task.importance`,
  `task.complexity`, `blocker.present`, `blocker.need_human_help`,
  `blocker.waiting_on_actor`, `actor.role`, `actor.state`
- Importance MAE + Spearman (ordinal low/medium/high → 1/2/3)
- Importance-weighted task TP/FP/FN
- State-transition accuracy on `task.status`
- Compression: source-chars → prediction-chars → reference-chars, estimated
  tokens via chars/4
- Hallucination (`SUPPORTED` / `CONTRADICTED` / `INSUFFICIENT_EVIDENCE`) from a
  `judge-verdicts.jsonl` file — prepared, no judge has run.
- Readability (clarity, organization, conciseness 1-5) — same judge path.

Explicit limits already in the output: metrics cover only processed-and-reviewed
turns; machine drafts are not independent ground truth; no held-out
generalization score exists.

### 1.5 Provenance

Each prediction row records `model`, `annotator_kind`, `label_status`,
`generated_at`. Model is set at ledger-append time (`pipeline.accept` hard-codes
`'gpt-6-luna'`). An optional `inference_provenance` slot already exists in
`pipeline.accept(path, provenance=None)` but no accepted row sets it today.

External receipts that already exist outside the ledger:
- `data/v02/day1/local-codex-jobs/` — raw per-turn Luna events, prompts, stderr.
- `data/v02/nemotron-day1/` — independent Nemotron rollout per-turn
  events/prompts/comparison-source.
- `data/v02/day1/seed-provenance.json`, `data/v02/day1/run-report.json` — run-
  level metadata.

Multi-model slicing today requires joining the ledger to those receipts
externally. A **run/ledger wrapper manifest** (new file, not a turn-patch schema
change) is the recommended place to make this joinable in-process, without
rewriting any accepted prediction row.

## 2. Issues the current setup does not yet handle

### 2.1 Machine-reference vs human-approved policy is not expressed in the schema

CKL has designated the Luna Day1 run as a **machine teacher reference**,
distinct from human-approved gold. Current ledger has one `label_status`
(`machine_draft`), and `evaluate.py` folds reviewed patches back into the same
field. There is no first-class way for training export or eval to say "treat
Luna as weak supervision, treat reviewer-corrected rows as reference".

### 2.2 One gamejam is not independent generalization

Day1 covers a single hackathon run. A chronological 80/20 split within one
project does not isolate the model from style, actor set, project vocabulary, or
tool choices. Any F1 published from this split must be labeled "within-project
temporal split" and cannot claim transfer.

### 2.3 Rollout eval is absent

`evaluate.py` scores each turn's delta against the reviewer-corrected graph. The
model never sees its own earlier predictions during eval. Rollout eval (the
model's own prior patches feed the next turn's context) would measure cumulative
state drift. Teacher-forced and rollout numbers can differ sharply; shipping
only teacher-forced is a story risk.

### 2.4 Thin provenance blocks model comparison

To compare Luna vs Nemotron-550B vs any future LoRA adapter on the same turns,
each row needs `provenance.engine`, `provenance.model`, `provenance.version`,
and `provenance.trained_from` (nullable). Right now only `model` exists.

### 2.5 LoRA export compatibility is implicit

The current chat-format export works with Nebius Token Factory SFT on the bases
in the supported catalog (Qwen3-14B / Qwen3-32B / Llama 3.x). But the export
does not:

- Verify the target base's chat template matches the system/user/assistant
  shape used in `export_training.py`.
- Check that `context_length` on the target base fits the longest serialized
  example.
- Emit per-row token-count estimates.

Without these, firing an SFT job risks silent truncation or chat-template
mismatch.

## 3. Recommended refactor

Each item lists the owning file and whether the change is a schema addition,
new script, or doc. **No existing scripts are edited in this round.**

### 3.1 Three-tier reference policy (reference-manifest, NOT a schema change)

CKL has already designated the full Luna Day1 run as the baseline
**machine_teacher_reference**. No new confirmation is required and no accepted
prediction row is rewritten.

Three tiers, carried in a separate reference manifest (new file,
`data/v02/<dataset>/reference-manifest.json`) that maps run_id → tier:

- `machine_draft` (default; a machine run that has not been designated).
- `machine_teacher_reference` (Luna Day1 is this today, by prior CKL fiat).
- `human_approved_gold` (approved or corrected via `/review`; the only eval
  reference).

The tier never lives on individual prediction rows; it lives on a run_id in the
reference-manifest, so an older ledger stays byte-for-byte stable. Training
export and eval consult the manifest instead of a schema field.

### 3.2 Richer provenance (run-wrapper, NOT a schema change)

The prediction schema already carries `model`, `annotator_kind`, `label_status`,
`generated_at`, and an optional `inference_provenance` field accepted by
`pipeline.accept(path, provenance=None)`. External receipts live in
`data/v02/day1/local-codex-jobs/`, `data/v02/nemotron-day1/`, and
`data/v02/day1/{seed-provenance,run-report}.json`.

Recommendation (no schema change, no history rewrite): a sibling
`data/v02/<dataset>/run-manifest.json` indexed by run_id, carrying:

```
engine                 cct/codex | cct/claude | nebius-serverless | ...
model                  gpt-6-luna | Qwen/Qwen3-14B | nvidia/Nemotron-550B | ...
model_version          optional
prompt_sha256          sha256 of prompts/v02/extract-turn.txt at run time
trained_from_adapter   null | nebius job id
effort                 low | medium | high | null
inference_run_id       optional
reference_tier         machine_draft | machine_teacher_reference | human_approved_gold
turn_ids               list of turn_ids this run contributed to
```

Future `pipeline.accept(...)` callers may also start supplying its already-
accepted `provenance=` kwarg so new rows carry `inference_provenance` inline.
Existing Luna Day1 and Nemotron rows are joined to the manifest, not rewritten.

### 3.3 Split strategy, documented clearly

Day1 enables one real split:

- **Within-project temporal** (current). Train on earliest reviewed turns,
  validate on latest. 2-turn embargo already in place. Honest label:
  "within-project temporal split, one gamejam, cannot support independent
  generalization claim."

Two future splits require more data:

- **Within-project cross-session**. Hash by the actual `session_id` field on
  each turn, hold out whole sessions. Reduces per-session style leakage, but
  the model context (`pipeline.context`) walks cross-session relationships,
  actor state, and the project root — so a cross-session split is **not
  automatically leakage-free**. Shipping a cross-session claim requires a
  context-dependency audit for each held-out session: enumerate which actors,
  tasks, decisions and relationships would already be visible via graph
  context, and whether those visibility paths let the model infer labels from
  training-side state. Until that audit lands, label the number as
  "cross-session with graph-context leakage not ruled out."
- **Cross-project**. Requires a second gamejam (or other project) dataset.
  Only this split justifies a generalization claim.

The plan documents all three and locks the deck language to the one we actually
have.

### 3.4 Rollout eval design (new script, planned, not implemented in this round)

A new `scripts/v02/rollout_evaluate.py` would:

1. Enumerate reviewed turns.
2. For each turn, build context from the model's own prior predicted patches
   (not reviewed corrections), using `pipeline.context(turn, graph, records)`
   where `records` are the model's own earlier outputs.
3. Call the model (requires paid budget approval; not in this round).
4. Score against `human_approved_gold` using the same metric set as
   `evaluate.py`.

Deliverable in this round: the design above and a stub test proving the context
builder accepts an arbitrary `records` list (already true in `pipeline.replay`).

### 3.5 Graph metrics unbundling

`evaluate.py` currently reports one relationship F1 bundling all edge types.
Recommendation: split by edge type (`delegates_to`, `works_on`, `reviews`,
`depends_on`, `is_sub_task`). Also surface task-identity breakdown
(`new`/`existing`/`ambiguous`) with per-class P/R/F1.

Change deferred to a later edit of `evaluate.py`; this plan documents the exact
diff.

### 3.6 LoRA export hardening

The Nebius Token Factory catalog and 8k context limits used in this plan come
from a **historical probe dated 2026-10-23** (my own Nebius session during the
hackathon prep window). Treat the eligibility list and context ceilings as
historical, not current. Any paid launch must first re-verify, per target
base and per inference account:

1. The current supported-fine-tune catalog on Nebius (bases may have been
   added/removed).
2. The target base's actual max context (not the 8k the dated probe assumed).
3. The target base's tokenizer and chat template, applied end-to-end to a
   `[system, user, assistant]` triple from our export.
4. Whether full-SFT (`lora: false`) is still beta-gated for that account
   (the Oct 23 probes showed universal gating, but gates lift).

Deliverable in this round: the plan script runs a char-length + chars/4 token
estimate (an explicit weak proxy), prints the max-length row id, and names the
historical Nebius bases that were LoRA-ready in October. Those are
candidates to re-verify, not eligibility claims.

Observed catalog drift: on 2026-10-29, CKL reports seeing "Qwen 3.5 4B" in the
Token Factory console. That base was NOT in the Oct 23 probe. Treat this as
concrete evidence that the catalog has moved. "Qwen 3.5" is a naming CKL
observed first-hand; the exact repo id and base-vs-instruct variant must be
re-verified against the console before any launch.

### 3.7 Local eval path on gpu-host (RTX 3090, zero paid cost)

Base-vs-LoRA for a V0.2 LoRA target is evaluated locally on gpu-host's RTX 3090 at
$0, using the same harness that produced the V0.1 numbers:

- Loader: `scripts/eval_adapter_batched.py` from the trainer worktree, mirrored
  to gpu-host at `[REDACTED_PATH]`. Supports both
  zero-shot (`--adapter ""`) and PEFT-attached modes.
- Precision: int4 nf4 via bitsandbytes (`load_in_4bit=True`,
  `bnb_4bit_compute_dtype=bfloat16`, `bnb_4bit_quant_type="nf4"`), transformers
  + PEFT, `attn_implementation="sdpa"`.
- Chat template: stock Qwen template, thinking on, `<think>...</think>` block
  stripped post-generation (same `extract_label` as V0.1).
- Context: clipped to the training `context_length` (8192 today). Base-vs-LoRA
  comparison only valid at or below the trained ctx; a longer-ctx base run is a
  separate number, labelled distinctly.
- Prereq: the GPU machine is powered on and running Linux,
  model dir present at `[REDACTED_PATH]<base>`,
  byte-identity to the training base verified via sha256 of
  `config.json` + `model.safetensors.index.json` against the HF revision used
  by the Nebius job. For a 4B base at int4 the 3090 serves comfortably; a 9B
  base is also well inside 24 GB at int4.

CKL candidates for a V0.2 LoRA target, from the Oct 29 console observation:

- `Qwen/Qwen3.5-4B` (or whichever base variant maps to CKL's console sighting,
  pending verification) — smallest Token Factory-trainable Qwen, fastest
  training wall-clock, cheapest launch, apples-to-apples with the local base
  baseline that swarm-dashboard-qwen is wiring up.
- Optionally `Qwen/Qwen3.5-9B` (if present in the console) — adds a mid-sized
  bar for a scale sweep.

Status before launch: re-verify both repo ids against the live console, pull
once to gpu-host, record the HF commit, train, pull the adapter back, eval locally.

### 3.8 Multi-teacher provenance (exporter requirements)

The current `scripts/v02/export_machine_teacher.py` was designed for a single
Luna Day1 teacher. For a mixed-teacher export (Haiku + Qwen3-30B + Nemotron +
Luna, or any subset), the following need to be in place first — none are
blocking V0.2 LoRA on a Luna-only export, but every item is required before a
multi-teacher train pair is emitted:

1. **Run-manifest join (`data/v02/<dataset>/run-manifest.json`)**, indexed by
   `inference_run_id`, carrying `engine`, `model`, `model_version`,
   `prompt_sha256`, `trained_from_adapter_id`, `effort`, `reference_tier`, and
   a `turn_ids` list. The exporter joins ledger rows to this manifest by
   `inference_run_id` (or by `generated_at` ordering as a weaker fallback).
2. **Per-row teacher filter on the exporter**: `--teacher-model` /
   `--teacher-engine` / `--include-run-ids` so a single invocation emits one
   coherent slice. Multi-teacher mixing is behind an explicit `--multi-teacher`
   flag and refused by default.
3. **Richer per-pair provenance block** in the output chat row:
   `provenance.engine`, `provenance.model`, `provenance.model_version`,
   `provenance.prompt_sha256`, `provenance.inference_run_id`,
   `provenance.reference_tier`, `provenance.trained_from_adapter_id`, plus the
   hashes already present (`ledger_sha256`, `turns_sha256`, `rubric_sha256`).
   Downstream eval can then slice/report per teacher.
4. **Per-teacher composition section in the manifest**: counts per
   `(engine, model)` tuple, highest-contribution teacher share, and a warning
   when any single teacher exceeds a threshold (e.g. 80%) of the pairs. This
   makes a "one teacher dominates" training set visible in the manifest instead
   of only in dataset tooling.
5. **Independence/diversity audit flag**: a `provenance.independence_notes`
   string per teacher — e.g. Nemotron rollout at Day1 was labelled on the
   same source turns as Luna, so pairs from both are correlated per turn.
   Pair-wise independence across teachers is NOT free from distinct runs on
   the same turn; the exporter should surface this correlation rather than
   let a reader assume i.i.d.
6. **Teacher-ambiguity rejection**: root integration already added
   excess-rows / wrong-teacher rejection in the current single-teacher path.
   For multi-teacher, that same check becomes a per-run-id consistency check:
   every ledger row referenced must appear in exactly one run-manifest entry.
7. **Current receipts already exist**: `data/v02/day1/local-codex-jobs/` for
   Luna, `data/v02/nemotron-day1/` for Nemotron, and the Haiku/Qwen3-30B
   paths once those teachers run. The run-manifest is the join layer; the
   receipts stay on disk unchanged.

None of the above requires a schema change to `turn-patch.schema.json`.

## 4. Scope and ordering

This document is step 0. Each subsequent step is a bounded edit.

| Step | File(s) | Change | Prereq |
|---|---|---|---|
| 0 | this doc + `scripts/v02/training_v02_plan.py` | audit + dry-run report | - |
| 1 | `scripts/v02/export_machine_teacher.py` + `scripts/v02/tests/test_machine_teacher_export.py` (new) | explicit Day1 machine-teacher export, isolated from reviewed-gold export | - |
| 2 | `data/v02/<dataset>/reference-manifest.json` + `data/v02/<dataset>/run-manifest.json` (new siblings, out of scope here) | tier + provenance join, no row rewrite | step 1 wired |
| 3 | `scripts/v02/export_training.py` (not touched here) | consult reference-manifest; keep behaviour for `human_approved_gold` | step 2 |
| 4 | `scripts/v02/evaluate.py` (not touched here) | per-edge-type F1; identity-resolution breakdown; separate "machine-teacher agreement" mode that is not a factual-quality score | step 2 |
| 5 | `scripts/v02/rollout_evaluate.py` (new, planned) | model-rollout eval | review coverage + budget |

Nothing in steps 1-6 is authorized by this document. They are design.

## 5. Old V0.1 training/eval assets

Preserved under tag `v0.1-dashboard-snapshot-20261028` and routes
`/eval-v01`, `/training-v01`. The V0.1 pipeline predicted five fields
(`user_intent`, `agent_intent`, `agent_role`, `blocker_type`, `urgency`) via
LoRA SFT on Qwen3-14B. Three LoRA adapters exist on gpu-host:

- `qwen14b_r16`: 87.15% exact-match / 0.208 macro-F1 (on V0.1 degenerate corpus)
- `qwen14b_r32`: 85.11% / 0.200
- `qwen30b_r16`: adapter trained on Nebius, local eval OOMs at 24GB VRAM int4

These bars are not comparable to V0.2 metrics; they measure a different task on
a different schema. Keep the V0.1 route alive for the hackathon submission.

Full-SFT (`lora: false`) is **beta-gated universally** on Nebius Token Factory:
API accepts the job and never schedules GPU regardless of base, confirmed
across Qwen3-4B and Llama-3.2-1B. V0.2 training must assume LoRA for Nebius
runs until the beta flag lifts.

## 6. Open questions for CKL

Already-authorized items (not questions, listed only for clarity):

- Reference-tier names are set: `machine_draft` /
  `machine_teacher_reference` / `human_approved_gold`.
- Luna Day1 is designated `machine_teacher_reference` once the Day1 label
  run is complete.

Remaining questions:

1. Which base does V0.2 LoRA target once the Day1 label run finishes and the
   paid launch is authorized? Current direction from CKL (2026-10-29 console
   observation) is the smallest Token Factory-trainable Qwen — reported as
   "Qwen 3.5 4B", with a possible second launch on "Qwen 3.5 9B". Both repo
   ids must be re-verified against the live console before a launch. See
   section 3.7 for the local eval path on gpu-host (zero paid cost).
2. Is cross-project data (a second gamejam, or synthetic project)
   worth collecting before firing a V0.2 train, or do we ship within-project
   splits and label them honestly? Cross-session alone is not leakage-free
   without the context-dependency audit in section 3.3.

Signed off: nebius-hack-trainer (session trainer-dev, 2026-10-28).

## Integration review corrections

The machine-teacher exporter preserves raw Luna targets even if the reviewer has
approved, corrected or rejected a turn. Reviews never enter these examples and
do not block export; use the separate human-reviewed exporter for corrections.
The script is restricted to the designated Luna Day1 manifest, rejects an
overlong or wrong-model ledger, and snapshots ledger bytes before hashing so
concurrent append-only generation cannot mismatch the export's provenance.
Split manifests carry source timestamps as well as generation timestamps.
A two-turn gap does not establish dependency isolation between graph contexts.
