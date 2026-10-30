# CCT Summarizer V0.2 prototype

V0.2 lives in `.`, branch `feat/v0.2-state-graph`.
The current V0.1 source was captured in commit `907cb2a`, tag
`v0.1-dashboard-snapshot-20261028`. The original live checkout is untouched.

Source/prediction correction UI: **[REDACTED_URL]
Review, evaluation, training readiness, and the timeline's V0.2 task summary are available.
The previous evaluation/training pages remain at `/eval-v01` and `/training-v01`.

## First-day run — current status

CKL authorized a fresh 5 percentage-point LOCAL Codex quota budget on 2026-10-28.
The active dataset is now `day1`: **1,902 completed source turns across 34 sessions**,
2026-10-10 14:15:25.380 through 2026-10-11 14:15:25.380 UTC (half-open).
The original `prototype` remains preserved. Its entire 72-turn prefix matches day1 exactly;
its 43 predictions were copied unchanged, including exact saved model inputs.

The refreshed run stopped at **57/1,902 predictions** (14 new, 43 reused), through
2026-10-10 15:05:14.421 UTC. **The first day is not fully labeled.** The shared meter
rose from 49% to 53%; the guard stopped at +4 points, reserving one point beneath
CKL's 5-point ceiling. Other sessions count toward this shared-account guard; this
is not an isolated measurement that these 14 labels consumed all four points.
The CCT worker is stopped and no automatic annotation restart is installed.

All inference used local Codex with ChatGPT login, model `gpt-6-luna`, effort low.
No OpenRouter, Nebius, API-key inference or training. Paid API spend: $0.
The CCT worker produced seven new labels, then was paused to avoid carrying its
large tool/chat history. `run_local_codex.py` generated the remainder through fresh
local Codex CLI calls with tools disabled and the exact state supplied each time.
Changing CLI sessions preserves the same authorized quota baseline; it never
renews the budget. API-key login is refused and API-key/base-URL environment
variables are removed from the labeling subprocess.

Raw outputs, prompts and per-call events are kept privately under
`data/v02/day1/local-codex-jobs/`. Strict schema, references and exact source quotes
are checked before acceptance. Two deterministic, audited evidence-format repairs
are permitted: restoring omitted Markdown bold delimiters when the text matches
uniquely, and splitting concatenated sentences only when every unchanged sentence
is an exact, unique source span. No fuzzy matching or factual label changes occur;
other invalid output is rejected. Repair provenance is stored with the accepted row.

Usage recorded for this refreshed run (includes rejected attempts/bootstrap):
CCT worker 4,933,137 input / 17,187 output; 4,784,768 input cached. Ten fresh CLI
calls 319,096 input / 5,871 output; 106,496 input cached. These token counts are
separate from the account-wide quota percentage. Full run report is private at
`data/v02/day1/run-report.json`.

`data/v02/active.json` selects the UI/CLI dataset; `CCT_V02_DATASET=prototype|day1`
provides an explicit local override. No quota configuration means generation is
denied. Review now shows both start and end dates. Replay uses a canonical window
union so equivalent cell/bus updates do not reset the cursor.

Validation: 16 Python checks, 21 JavaScript unit checks, 6 archived first-hour
review browser checks, and 3 active-day/timeline browser checks pass. Browser
regressions use the frozen original first-hour data while new labels are generated;
the separate day1 check reads the active dataset. Tests do not write human reviews.

## Original first-hour run (preserved history)

- Frozen first-hour Gamejam window: 2026-10-10 **14:15:25.380–15:15:25.380 UTC**, half-open.
- 72 completed turns across 9 source sessions; 43 machine annotations accepted, 29 pending.
- Local CCT/Codex worker `swarm-v02-gold`, model **gpt-6-luna**, effort low.
- Shared-account quota meter rose from 10% to 14%. Generation stopped at +4 percentage points,
  reserving one point beneath CKL's 5-point labeling budget. This is a conservative bound
  including other sessions, not an isolated per-worker consumption measurement.
- No expansion to 24 hours. No paid token API, Nebius call, upload, or training job.
- Labels are machine drafts. There are no human-reviewed GT labels yet and no trained-model scores.
- Worker stopped cleanly after turn-0043; completed output is materialized for review.

## Authoritative contract

This revision follows CKL's **CCT Summarizer Schema**, **CCT Summarizer EVAL Metrics**,
**Summarizer Pipeline Reasoning**, and **NEBIUS CCT SUMMARIZER Compass** in the private [REDACTED]'s
Nebius-x-nvida-hackathon project. Older `docs/eval/schema-v0.2-*` files describe the earlier
classifier experiment; they are not this state-graph contract.

- `schemas/v02/turn-patch.schema.json`: actor/task/decision/relation delta schema.
- `prompts/v02/extract-turn.txt`: full annotation rubric, identity and evidence rules.
- `prompts/v02/judge-claims.txt`: independent matching/claim-check/readability rubric; prepared, not run.
- `scripts/v02/prepare.py`: freeze prose-only turns and provenance, finished-turn ordering.
- `scripts/v02/pipeline.py`: validate, allocate IDs, replay graph, publish review data, save reviews.
- `scripts/v02/quota.py`: read local Codex telemetry; no provider requests.
- `scripts/v02/evaluate.py`: offline human-review audit and optional independent judge verdicts.
- `scripts/v02/export_training.py`: local, reviewed-prefix-only training preparation.

Turns are processed globally by finish time. Human inputs are instantaneous. Assistant tool
calls/results are excluded; commentary belongs to its completed turn. Turns not completed
within the prototype hour are excluded, not cut into misleading completed turns. Input
classification and duplicate suppression are recorded in the manifest; review source attribution
as well as labels. The human project brief is not supplied before its original turn.

Task IDs come from the harness, deterministically from turn/local reference. Existing updates
are sparse: omitted fields persist; null clears. Unmentioned tasks do not change. Ambiguous
identity creates an explicitly ambiguous provisional entity, never a silent merge. Every changed
entity requires exact current-turn evidence. Validation proves schema/references/quote presence,
not semantic truth—human review remains necessary.

Context includes related tasks, active roots, recently touched tasks, actor catalog, relationships,
and recent deltas. The actor catalog contains source-derived identities; task/actor state and
project prose remain causal. Model-generated context is saved with each prediction for auditing.

## Review loop

Open `/review`; filter by actor/review state, inspect source and evidence, then approve, correct,
or reject. J/K navigates turns. Structured task editing and a full JSON editor are available.
Corrections need evidence and valid references, just like predictions. Save failures retain the
editor. Optimistic revisions reject stale saves from another tab. Tests intercept browser saves
and use temporary Python data; they never write reviews into this corpus.

`predictions.jsonl` is append-only. `reviews.jsonl` is a separate append-only revision history;
no approval rewrites original machine output. The prediction graph shown on the timeline remains
explicitly a **machine draft** graph. Review corrections are shown in the review pane and used for
evaluation/export; they do not silently regenerate downstream predictions. Corrected-graph
relabeling is a later bounded run, after CKL reviews the current experiment.

Re-approving an already corrected row retains the correction. Export requires a contiguous
reviewed prefix and replays corrected state. A correction that breaks later task references makes
export fail validation so the dependent row can be corrected explicitly.

## Per-turn state inspector

The review page now shows two historical views below the source/prediction panes. Use the
**Turn state** link or open `/review?turn=turn-0043#state-heading` directly.

1. **Overall state after this turn** contains every accumulated task (including unchanged and
   completed tasks), actors, decisions, relationships, ambiguity flags, and the project description
   known at that point. Tasks changed by the selected turn are marked. Full JSON is expandable.
2. **State presented to the LLM** displays the original saved `input` from that prediction's ledger
   record: selected prior tasks, actor state, actor/relationship context, recent turns, project
   description, and current source. The exact recorded input JSON is expandable. It is never
   reconstructed from the final graph or human corrections. This is the structured turn input,
   not a dump of the annotation worker's entire chat history.

Both views follow turn selection and J/K navigation. Unprocessed turns have neither snapshot nor
recorded input and say so explicitly. Accumulated state represents original machine predictions;
review corrections remain separate, as in the rest of this prototype. No new labels were generated
for this feature. Verified with 12 pipeline tests and 5 review-browser tests.

## Local commands

Run from the V0.2 worktree:

```bash
python3 scripts/v02/pipeline.py status
python3 scripts/v02/pipeline.py publish
python3 scripts/v02/evaluate.py --stdout
python3 scripts/v02/export_training.py
python3 -m unittest discover -s scripts/v02/tests -v
npm --prefix ui run test:unit
BASE_URL=[REDACTED_URL] npm --prefix ui run test:e2e
```

To start the dashboard: `cd ui && npm run dev` (127.0.0.1, port 4322).
Runtime Python dependency: `jsonschema`; JavaScript dependencies follow `ui/package-lock.json`.
No extra API credentials are needed. Runtime data is ignored by Git and stays private.
Legacy data paths are read-only symlinks to V0.1; all V0.2 writes go under `data/v02/`.

The annotation protocol is `pipeline.py next <prompt-file>` then model-written JSON then
`pipeline.py accept <draft-file>`. `next` refuses stale/missing telemetry, a changed quota window,
or consumption at the stop threshold. Do not reset the baseline or bypass the stop. This run is
finished at its quota ceiling; these protocol details are for reproducibility, not authorization
to spend another budget. No annotation cron/automatic restart was installed.

## Evaluation and training limits

This UI reports a **human-review audit**, not an independent held-out model benchmark. Task and
decision references are aligned by the reviewer. Text-equivalence matching is a separate judge
step. Field macro F1, relation P/R/F1, identity resolution, importance MAE/Spearman, status-delta
accuracy and compression are implemented. Sparse fields are scored as deltas (UNCHANGED is
explicit), not as a claim of independently reconstructed full historical state.

Candidate-only claims have three independent-source verdicts: SUPPORTED, CONTRADICTED, or
INSUFFICIENT_EVIDENCE. A supported extra is a reference-coverage error, not a hallucination.
GT-only facts count toward recall misses. Readability uses clarity/organization/conciseness 1–5.
No judge has run yet; missing metrics display “Not measured,” never invented zeros.

Training export emits chat examples using the same rubric/context. With fewer than ten reviewed
prefix turns it produces only `reviewed_all.jsonl`; otherwise it prepares chronological train and
validation files separated by two turns. Validation receives causally prior reviewed state, so this
is a teacher-forced sequential split, not an autonomous rollout benchmark. Ten examples is only
an export threshold; the first-hour prototype is not enough to claim training generalization.
Paid training remains disabled; no model has been trained by this run.

## Verification

- 11 Python contract tests pass (temporary test data only).
- 11 inherited timeline unit tests pass.
- Full browser suite: 19 pass, 2 pre-existing data-dependent navigation skips.
- Four V0.2 browser checks pass again after final review refinements.
- All 43 saved predictions replay and validate; graph contains 15 tasks, 9 decisions, 23 relations.
- Desktop/mobile review checked.
- No human reviews were inserted by tests. Preview/build publishing was not performed.

Prediction ledger SHA-256 at this checkpoint:
`bab994209198bbb08ab8d865ac4caa004a5bdc92462b1a2e9238cea680600919`.

## Interactive state graphs

Both historical state panels include a local SVG graph with Whole state / Tasks / Actors filters.
Hover or keyboard focus highlights connected entities and reveals details/evidence. Click or Enter
pins a node or edge; Escape or Reset clears it. Drag the background to pan, use +/- or Ctrl/Command
plus wheel to zoom, Fit to restore framing, and Expand for fullscreen. The two graphs are independent.

Task mode includes recorded `depends_on` and `is_sub_task` edges. Actor mode includes delegation;
its task identity remains in the edge details. Whole-state mode adds work/review edges and decision
connections derived explicitly from author/affected-task fields. Referenced entities absent from a
supplied context become dashed reference-only nodes; no later state fills them in. Unprocessed turns
have no graph. All operations are local and leave labels/reviews unchanged.

See [labeling-cost.md](labeling-cost.md) for measured worker token usage and clearly separated
hypothetical OpenRouter costs. No additional annotation or paid inference was run for these views.

## Integrated timeline state explorer

Summary + Labels now embeds scoped accumulated state, selected changes, and exact recorded
LLM input, with an interactive graph and persistent task list. Turn/hour replay and the scrubber
move only the state cursor; the original widgets retain timeline selection. The V0.1 bullet
selector remains available. See [selection-contract.md](selection-contract.md) for exact interval,
causality, replay, coverage and legacy-pane semantics. No new labels or paid inference were run.

Integration validation: 20 JavaScript unit tests and 23 browser tests pass (2 inherited
data-dependent navigation skips). All 43 historical cursors were checked for future task-state
leaks and source mutation. The prediction-ledger SHA-256 above is unchanged.

## Full Day 1 continuation and run review (2026-10-28)

CKL authorized completing all 1,902 frozen Day 1 turns, replacing the earlier 5pp
labeling stop for this dataset only. The former config and run audit remain intact.
`quota-config.json` now binds authorization to the exact source SHA-256 and turn count.
The runner uses ChatGPT-login Codex CLI, gpt-6-luna low, outside CCT. It strips skills,
coding instructions and tools per call without changing global config or authentication.
It stops at native subscription exhaustion or three invalid candidates; no paid fallback.
`STOP_LOCAL` in the dataset directory requests a stop after the current call.

Accepted labels remain append-only. The preserved 43-turn seed and later accepted prefix
are reused, not recomputed. Day 1 is a machine teacher baseline, not human-reviewed gold.
Training preparation must distinguish these reference classes. No paid training has launched.

Review, Eval and Training have a prediction-run picker: `?dataset=day1`,
`?dataset=prototype`, or `?dataset=nemotron-day1`. Reviews and metrics stay bound to the
selected run. Current metrics audit each run against its own human reviews, not cross-model
quality; see the trainer's preparation plan for the independent comparison contract.
Nemotron is a separate causal rollout: see [nemotron-free-comparison.md](nemotron-free-comparison.md).

Fleet responsibilities: project-manager-assistant-codex is top manager and sole
fleet-report writer; project-manager is sub-master for timeline correctness;
nebius-hack-trainer prepares V0.2 training/eval. The management role does not change the
current root model, gpt-6.1-sol.

Measured lean-run check: the first 22 resumed calls averaged 20,477 input tokens
versus ~31,910 in the earlier fresh-CLI sample (different source turns, not a
controlled quality benchmark). Input beyond the written prompt fell from ~17,606
to ~6,872 tokens. Removing the remaining plugin catalog then reduced that excess
to ~5,942 in turns87–89; saved sessions show no skill catalog. Native protocol/
provider overhead is still present. No schema fields were removed.

`local_codex_background.py` supervises the current Day1 worker, waits for included
subscription quota resets, and stops on validation/runtime failures for inspection.
It has a 14-day lifetime bound and never enables paid credits or API fallback.

## Haiku4.5 subscription quota comparison

`benchmark_claude_quota.py` runs the explicitly requested 160-turn comparison
through Claude Code OAuth with the pinned `claude-haiku-4-5-20251001` model.
It uses each Luna row's recorded prior-state context. Where direct CLI prompts
exist, the exact first-attempt prompt is reused. One candidate is measured per
turn, with no repair calls and no modification of the baseline ledger.
This is teacher-forced measurement, not an independent accumulated-state rollout.

The runner checks provider-native quota events for rejected overage and
`isUsingOverage=false`, and refuses unexpected tools, models, API-key auth or
subagents. It stops at 160 calls, one hour, a5pp shared weekly increase, a15pp
five-hour increase, or a subscription warning. The first quota reading is from
the first real call; percentages are rounded and include other account activity.
CLI dollar fields are list-price equivalents, not subscription invoices.

Private receipts, prompts and outputs: `data/v02/haiku45-matched/`.
Live aggregate table: `/eval#quota-benchmark`, polling `/api/v02/benchmark`.
`report.json` and `report.md` update as the test advances. The comparable fully
lean Luna subset starts at turn87; all counts use each provider's own tokenizer.
A failed format/evidence check is kept in the cost totals and is not accepted as
a baseline label. Literal evidence validation alone does not establish factual
quality.
