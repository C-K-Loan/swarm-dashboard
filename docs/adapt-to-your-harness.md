# How to adapt to your harness

Built with CKL's private multi-agent harness CCT (to be released in the future); the transcripts in this dataset were produced by agents run under CCT.

The dashboard and the pipelines do not depend on that harness. They read one canonical format: **turns** (who said what, when, in which session) and **actors**. To use them with your own agents, write an adapter that produces that format from your logs, tell the sender classifier which sessions a human types into, and point the model runners at your endpoints.

```
your logs ──adapter──► turns.json + actors.json ──► Stage 1 / Stage 2 prose ─┐
                         (canonical format)     └─► v0.3-lean state graph ───┴─► dashboard
```

## 1. The canonical input

Two files in a run folder, `data/v02/<run>/` (the same format the public dataset ships in `source/`). A complete five-turn example is in [`examples/sample-fleet/`](../examples/sample-fleet/).

**`actors.json`**: every speaker. `human` and `harness` are required ids.

```json
[
  {"id": "human",          "name": "you",             "kind": "human"},
  {"id": "harness",        "name": "Session harness", "kind": "system"},
  {"id": "actor:s-lead",   "session_id": "s-lead", "name": "demo-lead", "kind": "agent"},
  {"id": "actor:s-dev",    "session_id": "s-dev",  "name": "demo-dev",  "kind": "agent"}
]
```

**`turns.json`**: one entry per finished turn, ordered by `end_ts`.

```json
{
  "id": "turn-0003", "ordinal": 2,
  "session_id": "s-dev",               // the session (agent pane / conversation) the turn appeared in
  "actor_id": "actor:s-lead",          // who produced it
  "actor_name": "demo-lead",
  "kind": "a2a",                       // agent | human | a2a | system
  "start_ts": 1800000045.0, "end_ts": 1800000045.0,   // Unix seconds
  "boundary": "instantaneous_input",   // why the turn ended (provider_end_turn, instantaneous_input, ...)
  "messages": [{"id": "m3-0", "ts": 1800000045.0, "role": "user",
                "text": "[from demo-lead] Please build the login form ..."}]
}
```

| `kind` | Meaning | Typical `messages[].role` |
|---|---|---|
| `agent` | an agent's own reply: all assistant prose from one input to the end of its answer | `assistant` |
| `human` | a person typed this into the session | `user` |
| `a2a` | another agent delivered this message into the session (`actor_id` = the sender) | `user` |
| `system` | the harness injected it (kickoff brief, notifications) | `user` |

Rules the pipelines rely on:

- **Prose only.** Leave out tool calls, tool results and hidden reasoning. Keep the agent's visible text.
- **Finished turns only.** An agent turn ends when the provider says the answer is complete. Inputs (`human`, `a2a`, `system`) are instantaneous: `start_ts == end_ts`.
- **Stable ids.** Message ids must be unique and stable, because evidence quotes point at them.
- **A spawn** (an agent starting another agent with a brief) is an `a2a` or `system` input at the start of the new session. The dashboard labels such inputs "spawn" when the sender is the spawner.

## 2. Where the adapter lives

| File | Role | What you change |
|---|---|---|
| `scripts/extract_prose.py` | V0.1 extractor: raw session logs → `data/prose/<project>.jsonl` (one row per prose message) | the readers for your log format; the session index (below) |
| `scripts/v02/prepare.py` | raw logs → `turns.json` + `actors.json` for one time window | the per-provider branches that detect turn ends; the fleet name in the file globs; the root-brief detection |
| `ui/src/lib/transcript-roles.mjs` | classifies every user-role input: human, a2a (and its sender), harness, spawn | `HUMAN_FACING`; the sender patterns |
| `scripts/v02/sender_attribution.py` | applies that same classifier to a frozen dataset, as an overlay file | nothing, usually |

**Session index.** `extract_prose.py` and `prepare.py` find raw logs through JSON files under `$SESSION_BUNDLE_ROOT/projects/<name>.json` and `.../fleets/<name>.json` (default `data/bundles/`):

```json
{"sessions": [
  {"id": "s-lead", "provider": "claude", "path": "logs/s-lead.jsonl", "title": "demo-lead",
   "model": "claude-...", "host": null, "fleet": "demo", "account": null}
]}
```

`provider` selects the reader (`claude` or `codex`); `host: null` means the file is local.

**Sender classifier.** A session often receives text that is not from a human: another agent's message, a harness notification, a kickoff brief. `transcript-roles.mjs` decides per input. It recognises:
- a `[from <agent>]` prefix (an agent-to-agent message);
- harness tags such as `<system-reminder>` and notification lines (harness);
- briefs starting "You are ..." (harness, or spawn when a spawner is known).

Set `HUMAN_FACING` to the sessions where a person actually types. In any other session, an unmarked input is attributed to an agent or to "unknown sender", never to the human.

## 3. Configuration knobs

| Knob | Where | Default | Effect |
|---|---|---|---|
| `SESSION_BUNDLE_ROOT` | env | `data/bundles` | where the session index lives |
| `HUMAN_FACING` | `ui/src/lib/transcript-roles.mjs` | the jam's two coordinator sessions | sessions a human types into, per fleet |
| fleet name | `prepare.py` globs, `scripts/freeze/slice.json` (`fleet`) | `gamejam` | which sessions form the dataset |
| window | `prepare.py --hours` | 1 (max 24) | how much of the log to freeze |
| `CCT_V02_DATASET` | env, or `data/v02/active.json` | `prototype` | which `data/v02/<run>/` the state-graph tools and UI open |
| hourly bucket | `summarize_buckets.py --grain` | 3600 (only value supported) | Stage 1 bullet bucket size |
| Stage 1 model | `summarize_buckets.py --model` | Qwen3-30B-A3B-Instruct-2507 | hourly bullets; `--budget-cents` caps spend |
| Stage 2 model | `hier_merge.py --base-url --key-env --model` (`--backend claude-sub` for the Claude CLI) | Qwen3-8B | hierarchical merge; `--start --hours` pick the window |
| State-graph model | `local_qwen_day1.py --base-url --api-key-env --model --renderer v0.3-lean` | `http://127.0.0.1:8000/v1` | labeler endpoint; `--budget-usd`, `--price-in/--price-out` cap spend |
| `NEBIUS_TOKEN_FACTORY_KEY`, `NEBIUS_TOKEN_FACTORY_BASE` | env / `.env` | | Nebius Token Factory (Stage 1, judges, state-graph runs) |
| `OPENROUTER_API_KEY` | env | | OpenRouter runs |
| `MODAL_ENDPOINT`, `MODAL_VLLM_KEY` | env | | your vLLM server with the adapters |
| `GPU_HOST`, `LAPTOP_HOST` | env | | ssh hosts for the local-GPU and Apple-silicon scripts |
| `HOST`, `ALLOWED_HOSTS` | env | `127.0.0.1` | dashboard dev server |
| `PRIVACY_BLOCKLIST_FILE`, `FLEET_ALIASES_FILE` | env | `config/*.txt/json` (untracked) | names and paths that must never reach an index; fleet name merging |

## 4. Common harnesses

**Claude Code (JSONL transcripts).** Supported as is: `provider: "claude"`.
1. Each session is one `.jsonl` file; list them in the session index.
2. A turn ends at an assistant message with `stop_reason: "end_turn"` or at a `system` record with `subtype: "turn_duration"`.
3. User records that start with harness tags are dropped as system messages.
4. Sub-agent transcripts: set `subagentsDir` on the session.

**Codex sessions.** Supported as is: `provider: "codex"`.
1. One rollout `.jsonl` per session.
2. Messages are `response_item` records with `payload.type == "message"`. A turn ends at an `event_msg` of type `task_complete` or `turn_aborted`.
3. Only `final_answer` prose is kept; commentary is dropped (`INCLUDE_CODEX_COMMENTARY`).

**OpenAI-style chat logs** (`[{"role": "user"|"assistant"|"system"|"tool", "content": ...}]` per conversation):
1. Add a third provider branch in `prepare.py` and `extract_prose.py`, or convert the logs to the canonical format directly.
2. Each conversation is a session. Each assistant message (after dropping `tool` and tool-call messages) ends an `agent` turn. Each `user` message is a `human` turn, unless your app marks it as coming from another agent. `system` messages become `system` turns.
3. Without timestamps, use any monotonic clock (for example the message index): the pipelines need order, not wall time.

**Multi-agent frameworks** (LangGraph, CrewAI, AutoGen style):
1. Make each agent (node, crew member, assistant) a session and an actor.
2. Turn every message passed between agents into two entries: an `agent` turn in the sender's session (its reply), and an `a2a` input in the receiver's session with `actor_id` = the sender.
3. Emit the user's task as a `human` turn in the coordinator's session, and list that session in `HUMAN_FACING`.
4. Map framework handoffs (AutoGen `HandoffMessage`, a LangGraph edge to another agent, a CrewAI delegation) to `a2a` inputs. The state-graph labelers then record them as `delegates_to` relationships.
5. Drop tool-call and tool-result messages; keep visible prose.

## 5. Checklist

- [ ] Every turn has `id`, `session_id`, `actor_id`, `kind`, `start_ts`, `end_ts` and at least one message with `id`, `ts`, `role`, `text`.
- [ ] Turns are ordered by `end_ts`; message ids are unique and stable.
- [ ] Every `actor_id` is in `actors.json`, which also has `human` and `harness`.
- [ ] Tool calls, tool output and hidden reasoning are excluded.
- [ ] `HUMAN_FACING` lists exactly the sessions a person types into.
- [ ] Agent-to-agent messages are `a2a` with the sender as `actor_id`.
- [ ] Secrets and private paths are removed before anything leaves your machine.
- [ ] The smoke test below passes on your output.

## 6. Smoke test

No model calls and no network. It checks the format, builds the v0.3-lean context for every turn and, if `patches.jsonl` is present, validates each patch with the production validator and replays the graph:

```bash
python3 scripts/v02/smoke_adapter.py examples/sample-fleet     # bundled sample: ends with "SMOKE OK"
python3 scripts/v02/smoke_adapter.py data/v02/<your-run>        # your adapter's output
```

On the sample it prints 5 turns, 2 sessions and a final graph of 1 task (`done`), 1 decision and 4 relationships. Then label your run with any OpenAI-compatible model (`local_qwen_day1.py`, see [quickstart.md](quickstart.md#4-run-the-pipelines)) and open the dashboard.
