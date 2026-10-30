# Nemotron free comparisons — original stopped; reasoning experiment awaiting provider

The original `nemotron-day1` run remains preserved at its quality stop. A separately authorized reasoning-enabled experiment, `nemotron-reasoning-day1`, starts from an empty graph over the identical frozen Day 1 source. Repeated calls returned upstream Nvidia 502 errors, with no model candidate or usage receipt; no reasoning prediction has been accepted yet. It is **waiting for the provider**, with only the already configured bounded cooldown/retries left active. No further configuration variants are being attempted. Its independent supervisor/status directory is `data/v02/nemotron-reasoning-background/`; do not infer its live state from the original run's status below.

**Current state, 2026-10-28 21:17 UTC:** seven turns accepted. Turn 8 exhausted its five total inference attempts and still contained five non-verbatim evidence quotations. The supervisor exited; no comparison worker is currently running. Strict evidence/schema validation remains unchanged, no turn was skipped, and restarting cannot reset the attempt count. Receipts total **136,913 input + 18,636 output tokens, $0** (excluding the separate 29-token synthetic probe). Details: `data/v02/nemotron-day1/model-quality-blocker.json`. Whole-timeline continuation remains prepared but has not begun.

Verified 2026-10-28 against the live OpenRouter catalog and endpoints API.

- Model: `nvidia/nemotron-3-ultra-550b-a55b:free` — 550B total parameters, 55B active, 1M context. This is the largest Nemotron free variant currently listed.
- Provider: Nvidia only. Catalog input/output prices and every returned endpoint pricing field are zero.
- A synthetic probe completed: 23 input + 6 output tokens, 0 reasoning tokens, `usage.cost = 0`, all upstream cost fields zero. After source authorization, real source turn `turn-0001` passed validation: **5,489 input + 556 output tokens, zero reasoning, $0**.
- Existing account quota: 1,000 free requests/day. The selected existing key has **$0 remaining spend allowance**. No key was created or topped up.
- Day 1 has 1,902 turns. At least two quota windows are needed, even without validation retries. Failed requests and repairs also consume requests. The complete frozen corpus has 10,942 turns across 223.009 hours and requires at least 11 quota windows including Day 1.

## Source handling explicitly authorized

The [model page](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free) says NVIDIA logs free-endpoint submissions for security and product improvement and asks users not to submit confidential or personal information. The endpoint also lacks EU in-region routing. This is an endpoint-specific restriction, discovered after the request to compare the free model.

CKL explicitly replied: **“Full source; I confirm it is suitable for that endpoint.”** The exact reply, timestamp and scope are stored in `data/v02/nemotron-day1/source-authorization.json`. Corpus inference began only after that reply. The runner now rejects `--run` without `--allow-provider-retention` before issuing any request. Default prepare remains read-only and does not submit corpus data. No sanitization or source-text edits were applied.

Counts-only heuristic scan of the 1,902 source turns:

| Category | Matches | Turns |
|---|---:|---:|
| Credential patterns | 0 | 0 |
| Credential assignments | 0 | 0 |
| Email-like strings | 4 | 4 |
| International phone-like strings | 0 | 0 |
| Private URL patterns | 41 | 38 |

42 turns triggered at least one category. These heuristics can miss sensitive material or flag harmless examples; they are not a privacy certification. No matched values were printed, no source text was changed, and no task IDs were rewritten.

## Isolation and causal comparison

`data/v02/nemotron-day1/` holds copied/frozen `turns.json`, `actors.json`, schema and extraction prompt; source and pipeline SHA-256 hashes; a separate prediction ledger; and the usual `review.json` containing exact per-turn LLM input and resulting accumulated state. Its initial task graph is empty: no Luna labels are seeded into Nemotron's rollout.

Source SHA-256: `bf9662c464296908039bc7744f0f613b9d7e338240dbc5ab74dd826e4b889894`.

The existing `pipeline.context`, `pipeline.check`, `pipeline.apply`, and `pipeline.publish` define context, validation, accumulated state, and review artifacts. Because the existing `pipeline.accept` hardcodes the Luna teacher name, the isolated runner appends validated rows itself with the correct Nemotron model and inference receipt. It holds writer/runner locks, fsyncs ledger additions, verifies chronological prefixes on resume, and never rewrites accepted labels. Both graph contexts and per-turn changes can diverge between models; comparisons should align by source turn ID rather than equating model-created task IDs.

Only the current causal context is sent. It includes the same project brief, relevant accumulated tasks/actors, recent prior patches and current prose as the local pipeline. There is no wholesale corpus upload, agent tooling, skills, shell prompt, or coding-agent wrapper. Schema and prompt are included explicitly. These predictions remain **unreviewed machine drafts**, not ground truth.

The runner never switches `data/v02/active.json`, writes to `day1`/`prototype`, calls Codex, or consults the local Codex 5% quota. It stops if source, prompt, schema, or pipeline hashes drift; a deliberately revised comparison needs a new dataset name.

## Zero-dollar guard and rate limits

Before every inference, including a validation repair, the runner rechecks:

1. The exact `:free` model is still listed and all catalog/endpoint price fields are numeric zero.
2. The pinned Nvidia endpoint still exists and the account has free requests left.
3. The request pins one model and provider, disables fallback, and sets `provider.max_price` for prompt/completion/request/image to zero. No plugins, tools, web search, or paid alternatives are enabled.
4. The response names the same model/provider and explicitly reports zero cost, including any nested upstream costs. Missing or nonzero receipts stop processing before another request.

Requests are sequential, at least 3.2 seconds apart. The default invocation caps at 990 requests; `--max-requests` accepts 1–1000. Each turn permits **one initial candidate plus at most four correction candidates**, counting previous candidate attempts across process restarts. Confirmed HTTP/JSON 429/502/503/504 failures without a candidate are tracked separately; ambiguous interrupted requests count conservatively. Every request and raw error remains preserved. The original non-reasoning turn 8 still has five failed candidates and remains stopped. The strict validator runs after every candidate; no invalid turn is skipped. Other HTTP errors, missing zero-price routes and exhaustion of the repair allowance stop the child runner with a recorded reason. There is no automatic account/key rotation, top-up, paid fallback, or uncontrolled retry loop. `free-runner-status.json` records a suggested next UTC reset for exhausted daily quota.

`openrouter_free_background.py` supervises Day 1 and then the full timeline. It resumes exhausted daily quota at the next UTC reset. Transient 429 has at most five consecutive cooldown retries with bounded backoff. Upstream 502/503/504 uses an independent **persistent one-hour outage window**, **5–10-minute cooldowns**, and a secondary cap of 12 failures. `provider-retry-budget.json` preserves the deadline/counter across supervisor restarts; history is append-only. Successful accepted-turn progress marks an outage recovered. It never retries pricing, privacy, drift, exhausted schema/evidence repair budgets or other HTTP failures. Upstream error responses are preserved locally, including errors returned inside HTTP200 JSON. A 30-day supervisor lifetime caps unattended operation. STOP is checked throughout cooldown and relayed to a running child; an in-flight turn is allowed to finish so its cost receipt and accepted prediction are preserved. Logs and status live under the selected supervisor directory.

CKL explicitly requested continued cooking during a one-hour AFK period. The revised reasoning supervisor was launched at **2026-10-28 21:38:25 UTC**, with its current outage deadline **22:38:25 UTC**. Five earlier confirmed provider failures were preserved and excluded from the candidate budget; no original validation failure was reset. Policy provenance is in `outage-policy-v1.json`. If the provider recovers, accepted outputs continue through the normal strict pipeline; if it stays unavailable through the deadline, the supervisor stops candidly.

## Full timeline and explicit identity anchoring

Raw `data/v02/gamejam-full-source/` is preserved unchanged: **10,942 turns / 80 source sessions / 161 actor records**. An exact Day 1 prefix check initially found 365 different `actor_id` values, caused by the expanded name-to-session catalog; every other field, including source prose, turn ID and chronology, matched. No raw source was silently substituted.

Root explicitly directed baseline identity anchoring. Separate `data/v02/gamejam-full-anchored/` pins the first 1,902 actor IDs to the frozen Day 1 namespace. For later A2A input attribution only, five turns use their uniquely matching frozen Day 1 name/ID. No Day 1 name was ambiguous, all 161 actor records remain, and no catalog entities were merged or deleted. No post-prefix agent-authored turn was remapped. `identity-normalization.json` records each original ID, chosen ID, rule and source checksums. The raw full source and Day 1 source remain unchanged.

The anchored source now has an **exact 1,902-turn prefix**, with all Day 1 actor records preserved. Before transitioning, the supervisor checks equality again, prepares isolated `nemotron-full`, and seeds its ledger with the completed Nemotron Day 1 rows byte-for-byte. The seed provenance identifies the original inference receipt directory; no accepted turn is regenerated. Additional full-source actors become available for continuation. Full-source hash: `01c805ae2f20fe3deb89a61ed00c3ece528b9492e1f6a229ddcffd4461eba3d7`.

`openrouter-jobs/` preserves exact requests and responses locally. `free-usage.jsonl` records successful generation receipts, including invalid candidates, so costs/tokens are not understated by counting only accepted labels. The synthetic probe receipt and counts-only scan are stored separately in the dataset.

Turn 7 exposed a repair-prompt weakness: the first candidate exceeded the quote-length limit, and its repair contained a non-verbatim quote. Both were rejected and preserved. Repair requests now include the actual rejected JSON and concise validation constraints. One explicit, documented retry after that runner fix passed strict validation at $0; no source quote was manually rewritten and the schema was not relaxed. The background supervisor resumed from seven accepted turns.

Turn 8 additionally exposed historical-evidence leakage: candidates cited project-brief/previous-turn message IDs, flattened source newlines, and used an unknown blocker actor. Prompt contract `current-turn-evidence-v2` makes the current-message whitelist explicit, forbids historical evidence, and reports all observed evidence/schema issues together rather than only the first. Root authorized the bounded four-correction policy after diagnosis. `repair-policy-v2.json` and each new inference receipt record the policy version, maximum attempts and runner hash. This changes extraction/repair guidance, not the schema, source text, deterministic state pipeline or evidence acceptance rules.

The stronger diagnostics corrected the unknown actor ID, but attempts 4 and 5 repeated five invalid evidence quotations. This is a model-quality limitation for the current non-reasoning extraction configuration, rather than a price/routing/credentials blocker. A future reasoning-enabled experiment should use separate configuration/provenance and an explicitly bounded new attempt budget; the existing failed ledger must not be silently reset or passed off as a successful full run.

## Separately tracked reasoning experiment

Root authorized one independent reasoning-enabled experiment after the original failure. The model and provider remain exactly the same free variants; schema, sources, evidence validator and accumulated-state pipeline are unchanged. No predictions are copied from the original run. Configuration is frozen in `inference-config.json` and included by SHA-256 in every receipt:

```json
{"reasoning":{"enabled":true,"max_tokens":4096,"exclude":true},"max_tokens":16384,"max_inference_attempts_per_turn":5}
```

The live catalog advertises `supports_max_tokens:true` and the Nvidia endpoint lists `reasoning` among supported parameters. These capabilities are rechecked before every reasoning request. The [official reasoning documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) explains that reasoning consumes output tokens and that excluding reasoning text does not remove those tokens from usage. The larger completion ceiling leaves room for both reasoning and visible JSON; usage receipts retain `completion_tokens_details.reasoning_tokens`. All zero-price checks still cover the entire output charge, and every response must explicitly report $0.

Changing the reasoning configuration in an existing dataset is rejected. The original five-attempt stop is not reset. If this separate experiment also reaches its bounded validation stop, report the failure instead of cycling through more variants. On success, its supervisor continues Day 1 and then seeds `nemotron-reasoning-full` with that run's own exact accepted prefix.

## Commands

Prepare and verify current free availability without submitting source:

```bash
python3 scripts/v02/openrouter_free.py
python3 scripts/v02/openrouter_free_scan.py data/v02/day1/turns.json
python3 scripts/v02/openrouter_free_test.py
```

The real one-turn pilot passed. To run/resume the authorized source manually:

```bash
python3 scripts/v02/openrouter_free.py --run --max-requests 1 --allow-provider-retention

# Offline supervisor plan (no network or inference):
python3 scripts/v02/openrouter_free_background.py

# Day 1 followed by verified full-source continuation:
python3 scripts/v02/openrouter_free_background.py --run --allow-provider-retention

# Independent reasoning experiment; uses separate ledger, status and locks:
python3 scripts/v02/openrouter_free_background.py --run --allow-provider-retention \
  --reasoning enabled --day-dataset nemotron-reasoning-day1 \
  --full-dataset nemotron-reasoning-full --full-source gamejam-full-anchored \
  --supervisor-name nemotron-reasoning-background
```

For a future sanitized variant, use a separate source dataset and comparison name. Never edit a frozen original dataset in place. The runner resumes accepted prefixes with the same arguments and a larger request cap. Creating `data/v02/<comparison-name>/STOP` or `data/v02/nemotron-background/STOP` requests a stop before the next turn. Remove a STOP file only when explicitly resuming the same authorized work. The supervisor also accepts SIGTERM for a graceful stop.

Twenty-one offline tests pass, including zero-dollar guards (also inside provider-error envelopes), frozen reasoning configuration and advertised capability enforcement, transient provider-error classification, separation of outages from candidate attempts, persistent one-hour outage expiry, complete repair diagnostics, an attempt cap that survives restarts, and a two-turn causal rollout/resume that does not regenerate accepted labels. Four additional offline supervisor scenarios passed: terminal failure is not retried, daily quota waits and resumes, transient 429 stops after five retries, and invocation request caps continue without resetting accepted labels.

## Official references

- [Model and endpoint terms](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free)
- [Live model catalog](https://openrouter.ai/api/v1/models)
- [Endpoint pricing](https://openrouter.ai/api/v1/models/nvidia/nemotron-3-ultra-550b-a55b:free/endpoints)
- [Provider routing and maximum prices](https://openrouter.ai/docs/guides/routing/provider-selection)
- [Free-model request limits](https://openrouter.ai/docs/api-reference/limits)
