# Data formats

Three summary formats sit on one shared source: the turns. All timestamps are Unix seconds (UTC). All files are UTF-8 JSON or JSONL. Paths are relative to `data/` (the layout in [quickstart.md](quickstart.md#2-get-the-data)); the Hugging Face folder for each file is given in brackets.

| Format | Unit | Producer | Released as |
|---|---|---|---|
| Source turns | one finished agent or human turn | `scripts/v02/prepare.py` | `source/` |
| V0.1 per-message labels (Stage 1) | one message, 5 fields | `scripts/classify_batch.py` | `stage1-gamejam/` (30 Opus-labeled messages + model predictions) |
| V0.1 hourly bullets | agent x 1 h bucket, 4 bullet lists | `scripts/summarize_buckets.py` | `prose-v01/` |
| V0.1.B merge tree (Stage 2) | node in a 2-D tree of bullet lists | `scripts/v01b/hier_merge.py` | `prose-v01b/` |
| v0.3-lean state graph | one patch per turn | `scripts/v02/*_day1.py` | `labels/`, `eval/`, `sft/` |

## Source turns

`v02/gamejam-full-anchored/turns.json` [`source/turns.json`]: a JSON array of 10,935 turns from 76 agent sessions, ordered by finish time.

```json
{
  "id": "turn-0006", "ordinal": 5,
  "session_id": "5c1d7031-...", "actor_id": "actor:5c1d7031-...", "actor_name": "gamejam-dev",
  "kind": "agent",                       // agent | human
  "start_ts": 1791641755.825, "end_ts": 1791641760.388,
  "boundary": "provider_end_turn",
  "messages": [{"id": "ca2518d2-...", "ts": 1791641755.825, "role": "assistant", "text": "...",
                "source_file": "[REDACTED_PATH]", "source_line": 41}]
}
```

Rules (from `prepare.py`): only prose is kept, tool calls and tool results are excluded; a turn ends at the provider's end-of-turn; human inputs are instantaneous; turns not finished inside the window are dropped, not cut.

Companion files: `actors.json` (actor catalog: id, name, kind, provider), `manifest.json` (window, counts, input classification, duplicate suppression), `identity-normalization.json` (alias to canonical actor), `sender-attribution.json` (which actor sent each agent-to-agent message).

"Day 1" means turns 1 to 1902 (the first 24 hours). Most labeling runs cover Day 1.

## V0.1 per-message labels (Stage 1)

One JSON object per message, 5 scored fields. The Opus-labeled eval set and the model predictions on it use **schema v0.2** (below); the earliest production classifier, `scripts/classify_batch.py`, used the v0.1 role list `worker | dev | manager | reviewer | planner`.

| Field | Values | Scored on |
|---|---|---|
| `user_intent` | `feature-def`, `feature-update`, `unblock-context`, `unblock-infra`, `status-check`, `praise`, `frustration`, `null` | all messages |
| `agent_intent` | `report-progress`, `report-blocked`, `ask-clarification`, `handoff`, `celebration`, `summary`, `null` | agent messages |
| `agent_role` | list, subset of `dev`, `tester`, `debugger`, `reviewer`, `planner`, `coordinator`, `manager`, `documenter`, `ops`, `analyst`, `researcher` | agent messages |
| `blocker_type` | `waiting-human`, `rate-limit`, `tool-failure`, `retry-loop`, `context-exhausted`, `scope-ambiguous`, `null` | agent messages |
| `urgency` | `low`, `mid`, `high`, `null` | all messages |

A sixth field, `task_phase` (`planning`, `exploring`, `implementing`, `testing`, `debugging`, `reviewing`, `documenting`, `idle`, `null`), is labeled but not scored.

Example: `{"user_intent":"unblock-infra","agent_intent":"report-progress","agent_role":["dev","debugger"],"blocker_type":null,"urgency":"mid","task_phase":"debugging"}`

`stage1-gamejam/gold_v2.jsonl` rows add `id`, `role`, `timestamp`, `text` (sanitized), `labeler` and `_bundle` (`agent_gamejam` or `user_gamejam`; the scorer uses it to tell user rows from agent rows and to align files). `ref_<model>.jsonl` rows carry the same `id` and `_bundle` with that model's labels.

## V0.1 hourly bullets

`bucket_summaries.jsonl` [`prose-v01/bucket_summaries.jsonl`]: one row per (session, hour) with at least one message. 967 rows, all `project = gamejam`.

```json
{
  "sid": "01a126b8-...", "bucket_start": 1791648000, "grain_sec": 3600,
  "project": "gamejam", "agent_title": "gamejam-voxel-dev", "provider": "codex", "msg_count": 3,
  "summary": {"started": [], "finished": [], "blocked": [], "key_decisions": []},
  "usage": {"in": 779, "out": 16, "cost_usd": 0.000083}
}
```

Each bullet is a 4 to 12 word string. Caps per bucket: started 5, finished 5, blocked 3, key_decisions 3. Model: Qwen3-30B-A3B-Instruct-2507 on Nebius Token Factory.

## V0.1.B merge tree (Stage 2)

`v01b/<run>/merge_tree.json` [`prose-v01b/<run>/merge_tree.json`]: the hourly bullets merged along two axes.

- Rows: per agent, 1 h leaves, then 4 h, then day.
- Columns: per time bucket at every level, all agents' row nodes for that bucket (chunked when wide).

```json
{
  "meta": {"version": "v0.1.B", "model": "Qwen/Qwen3-235B-A22B-Instruct-2507", "project": "gamejam",
           "start": 1791640800, "end": 1791727200, "levels": ["1h", "4h", "day"],
           "caps": {"started": 6, "finished": 6, "blocked": 4, "key_decisions": 4},
           "usage": {"calls": 129, "in": 165089, "out": 77987, "retries": 2}},
  "nodes": {
    "r:01a12751:1h:1791666000": {
      "kind": "row", "level": "1h", "sid": "01a12751-...", "agent": "gamejam-data",
      "start": 1791666000, "end": 1791669600, "children": [], "msg_count": 6, "merged": false,
      "bullets": {"finished": [{"id": "r:01a12751:1h:1791666000#f0", "field": "finished",
                                "t": "committed WIP with explicit paths", "src": [], "leaf_src": ["r:01a12751:1h:1791666000#f0"]}],
                  "started": [], "blocked": [], "key_decisions": []}
    }
  }
}
```

Node ids: `r:<sid8>:<level>:<start>` for rows, `c:<level>:<start>[...]` for columns. Every merged bullet cites its child bullets (`src`) and, transitively, the hourly leaf bullets (`leaf_src`), so any bullet traces back to the V0.1 summary it came from.

Other files per run: `judge_report.json` (coverage, faithfulness, duplicates, drift per level; from `judge_merge.py`). The `gamejam-day1-haiku` run also has `agreement_report.json`: every candidate merger scored against the Claude Haiku 4.5 merge (see [eval.md](eval.md#stage-2)).

| Run | Merger |
|---|---|
| `gamejam-day1-haiku` | Claude Haiku 4.5 (reference) |
| `gamejam-day1-q235b` | Qwen3-235B-A22B-Instruct-2507 |
| `gamejam-day1` | Qwen3-8B |
| `gamejam-day1-llama8b` | Llama-3.1-8B-Instruct |
| `gamejam-full-q235b` | Qwen3-235B, whole jam |

## v0.3-lean state graph

The state graph is a set of tasks, decisions, actors and relationships, rebuilt turn by turn. A model never writes the graph; it writes a patch for one turn, the harness validates it against the current graph and applies it.

### The patch (output)

Schema: `schemas/v02/turn-patch.schema.json` (`schema_version` `"0.2"`). Rubric: `prompts/v02/extract-turn.txt`.

```json
{
  "schema_version": "0.2", "turn_id": "turn-0001",
  "actor_patch": {"role": "manager", "state": "working"},
  "actor_evidence": [{"message_id": "...", "quote": "exact span from this turn"}],
  "tasks": [{"ref": "new:t1", "resolution": "new", "candidate_refs": [],
             "changes": {"title": "...", "status": "progressing", "importance": "high"},
             "evidence": [{"message_id": "...", "quote": "..."}]}],
  "decisions": [{"ref": "new:d1", "summary": "...", "affected_task_refs": ["T3"], "importance": "medium",
                 "made_by_actor_id": "A1", "evidence": []}],
  "relationships": [{"type": "works_on", "from_ref": "A1", "to_ref": "new:t1", "evidence": []}]
}
```

| Field | Values |
|---|---|
| `actor_patch.role` | `reviewer`, `devops`, `dev`, `manager`, `demo-recorder`, `demo-player`, `other`, `null` |
| `actor_patch.state` | `blocked`, `working`, `researching`, `reading`, `learning`, `testing`, `idle`, `unknown`, `null` |
| `tasks[].resolution` | `new`, `existing`, `ambiguous` |
| `tasks[].changes.status` | `not_started`, `progressing`, `waiting`, `testing`, `blocked`, `done`, `cancelled`, `null` |
| `importance`, `complexity` | `low`, `medium`, `high`, `null` |
| `relationships[].type` | `delegates_to`, `works_on`, `reviews`, `depends_on`, `is_sub_task` |

Semantics: updates are sparse (an omitted field keeps its value, `null` clears it); tasks not mentioned do not change; an unclear identity becomes an `ambiguous` entity, never a silent merge; every changed entity needs an exact quote from the current turn. Validation checks schema, references and that quotes exist; it does not check that a claim is true.

### The lean context (input)

`scripts/v02/lean_context.py`, version `v0.3-lean`. What the labeler sees, compared with the full v0.2 context:

- done and cancelled tasks, and their relationships, are dropped;
- evidence quotes are never sent;
- only the current actor's connected active tasks get full detail (`title`, `status`, `summary`, `importance`, `complexity`, `blocker`); other active tasks are `[alias, title]`;
- long `task:` and `actor:` ids become per-prompt aliases `T1, T2, A1, ...`; the harness maps them back before validation, and an unknown alias is a validation error.

### Files per run

`v02/<run>/` [`labels/<run>/`]:

| File | Content |
|---|---|
| `predictions.jsonl` | one row per accepted turn: `turn_id`, `model`, `annotator_kind`, `label_status`, `input` (the exact lean context), `patch`, `inference_provenance` (attempts, tokens, timing) |
| `manifest.json` | run window, renderer, schema and prompt hashes |
| `inference-config.json` | endpoint, model id, decoding settings, serving note |
| `status.json` | progress, usage, quota or spend deltas |
| `actors.json` | actor catalog as seen by this run |
| `review.json` | human review verdicts, where any |

`v02/<run>/teacher-forced.jsonl` [`eval/<run>/`]: held-out scoring rows, see [eval.md](eval.md#state-graph).

### SFT export

`v02/_multi-teacher/<name>/` [`sft/<name>/`]: chat triples for LoRA training, from `scripts/v02/export_multi_teacher.py`.

```json
{"turn_id": "turn-0412", "provenance": {"teacher": "haiku45-lean-day1", "...": "..."},
 "messages": [{"role": "system", "content": "<v0.3-lean rubric>"},
              {"role": "user", "content": "<lean context JSON>"},
              {"role": "assistant", "content": "<alias-encoded patch JSON>"}]}
```

| Export | Train rows | Val rows | Notes |
|---|---|---|---|
| `v1-8k-m64` | 1653 | 131 | rows over 8128 tokens dropped; trained LoRA v1 |
| `v2-8k-fit` | 1908 | 131 | long contexts trimmed step by step (`FIT_LADDER`) until prompt + 1024 output tokens fit 8128; trained LoRA v2 |

Split by source turn, shared across teachers: train `turn < 1201`, 2-turn embargo, validation/held-out `1203 <= turn < 1521` (Haiku labeled up to turn 1335, so the set is turns 1203-1335: 131 rows), never-trained rollout eval slice `turn >= 1523` (1523-1902). The exporter rebuilds every lean context from the teacher's ledger and refuses any row whose rebuilt input is not byte-identical to the recorded one.

Teachers: Claude Haiku 4.5 (`haiku45-lean-day1`, 1335 turns) and NVIDIA Nemotron 3 Ultra (`nemotron-ultra-lean-day1`, 817 turns).

## Placeholders

Sanitized values are replaced by typed placeholders: `[REDACTED_PATH]`, `[REDACTED_IP]`, `[REDACTED_HOST]`, `[REDACTED_URL]`, `[REDACTED_EMAIL]`, `[REDACTED]`, `[DROPPED_SESSION]`. Project paths are repo-relative. Agent names from outside the game jam are replaced by generic roles (`infra-agent`, `harness-architect`, `harness-dev`, `orchestrator`, `other-project`).
