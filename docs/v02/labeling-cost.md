# Labeling workload and OpenRouter extrapolation

Measured 2026-10-28 from the local annotation worker's final `token_count` telemetry.
This is **labeling-worker usage only**, not the coordinator's dashboard development usage.
No inference API request was made for these estimates; the original run used local CCT/Codex quota.

## Coverage

- Source window: first Gamejam hour, 2026-10-10 14:15:25.380–15:15:25.380 UTC.
- 72 frozen turns, 43 annotated; last accepted turn ended at 14:59:22.758 UTC.
- Approximately 44 minutes of source progression, **not 24 hours**.
- Worker stopped at the +4 percentage-point shared-quota guard beneath a 5-point budget.
- Incremental paid inference API spend: **$0**. Subscription quota was consumed.

## Recorded worker tokens

| Counter | Tokens |
|---|---:|
| Total input | 12,117,203 |
| Cached input (included above) | 11,385,344 |
| Non-cached input | 731,859 |
| Total output | 30,835 |
| Reasoning output (included in output) | 2,709 |

About 94% of input was cached. Totals include the worker's conversation, instructions, tools,
validation/retry loop, and final administrative messages. They are not just the summarizer payload.
Do not add cached input to total input or reasoning output to total output again.

## Direct annotation payload estimate

The 43 persisted annotation inputs, including the rubric and pretty-printed per-turn context,
contain 1,032,093 characters. Their compact JSON patches contain 40,059 characters.
Using **characters / 4** gives approximately **258,023 input + 10,015 output tokens**.
This is an estimate, not provider token telemetry; JSON and different tokenizers can vary materially.
It excludes hidden reasoning, API envelopes, retries, and agent/tool-loop history.
It describes a future one-request-per-turn pipeline, not a second run we actually executed.

## Illustrative OpenRouter cost

Prices checked 2026-10-28 on the canonical model pages:

- [Qwen3 30B A3B Instruct 2507](https://openrouter.ai/qwen/qwen3-30b-a3b-instruct-2507): $0.09/M input, $0.30/M output.
- [GPT-6 Luna](https://openrouter.ai/openai/gpt-6-luna): $0.10/M input, $0.50/M output; listed cache reads $0.01/M.

Search/activity pages showed lower provider-specific Qwen rates; the table deliberately uses the
canonical page's current listed $0.09/$0.30 rates. Provider routing and future prices may differ.

| Scenario | Qwen3 30B Instruct 2507 | GPT-6 Luna |
|---|---:|---:|
| All recorded worker tokens, priced without caching | $1.100 | $1.227 |
| Direct annotation payloads, 43 turns, estimated tokens | $0.0262 | $0.0308 |
| Full first hour, 72 turns, same average payload | $0.0439 | $0.0516 |
| 24 hours at 72 turns/hour, same average payload | $1.054 | $1.238 |

Formula: `(input_tokens * input_price_per_million + output_tokens * output_price_per_million) / 1e6`.
The 24-hour multiplier is `24 * 72 / 43`, because only 43 of the first hour's 72 turns were labeled.
It is a workload-sizing illustration, not a forecast: later agent activity, actor/task-state growth,
longer turns, retry frequency, reasoning, and differing model quality can change the result.
The table excludes platform/payment fees, taxes, tool costs, and training.

If the worker's **same cache hits** were reproduced at GPT Luna's listed cache-read rate, with
non-cached tokens at its standard input rate and no additional cache-write fees, the token subtotal
would be about **$0.202**. That is conditional arithmetic, not a guaranteed OpenRouter bill:
cache behavior and write billing do not transfer automatically from local Codex telemetry.

The observed agent workflow processed roughly 47 times the estimated direct-payload input volume,
mostly cached history. A sequential stateless request runner carrying only selected state would
avoid replaying the entire labeling worker conversation. No paid runner has been launched.

## Corpus-based paid-model sizing (supersedes constant-activity extrapolation)

Read-only reconstruction of all 80 currently ingested Gamejam sessions, including the local
data-host raw-log mirror, yields 10,942 completed turns from 2026-10-10 14:15:25.380 UTC through
2026-10-19 21:15:55.663 UTC (9.29 days). This describes the available corpus, not a guarantee
that every historical Gamejam session has been ingested. The first-hour count reproduces the
frozen prototype's 72 turns. No labels were generated for the later windows.

Cumulative window counts: 1 hour 72; 1 day 1,902; 2 days 2,579; 3 days 6,095;
4 days 9,608; 7 days 10,413; all 10,942. Windows start at the first source timestamp.

Calibration: the 43 saved inputs contain 66,214 characters of current-turn prose and
965,879 characters of rubric/context/formatting. Estimate each window's input as its
measured prose characters plus **22,462 non-source characters per turn**, then divide by four.
Estimate output at **932 characters per turn**, divided by four. This retains measured changes
in activity and prose volume while holding selected-state overhead at the pilot average.
Whole-corpus baseline: **63.315M input + 2.548M output tokens** (estimated, not billed usage).
Larger selected states, tokenization differences, thinking, retries and platform fees can raise it;
cache discounts are deliberately not assumed. These are direct annotation calls, without the
growing Codex worker conversation.

Current paid listing rates and estimates:

| Size | Model | Input / output per 1M | First 1h | First 1d | First 2d | First 3d | First 4d | First 7d | All 9.29d |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 4B | [Gemma 3 4B](https://openrouter.ai/google/gemma-3-4b-it) | $0.04 / $0.05 | $0.018 | $0.46 | $0.63 | $1.48 | $2.33 | $2.53 | $2.66 |
| 7B | [Qwen2.5 7B](https://openrouter.ai/qwen/qwen-2.5-7b-instruct) | $0.10 / $0.20 | $0.046 | $1.20 | $1.62 | $3.81 | $6.00 | $6.51 | $6.84 |
| 8B | [Llama 3.1 8B](https://openrouter.ai/meta-llama/llama-3.1-8b-instruct) | $0.02 / $0.04 | $0.009 | $0.24 | $0.32 | $0.76 | $1.20 | $1.30 | $1.37 |
| 12B | [Gemma 3 12B](https://openrouter.ai/google/gemma-3-12b-it) | $0.05 / $0.15 | $0.024 | $0.62 | $0.84 | $1.98 | $3.11 | $3.38 | $3.55 |
| 14.8B | [Qwen3 14B](https://openrouter.ai/qwen/qwen3-14b) | $0.10 / $0.22 | $0.047 | $1.20 | $1.63 | $3.84 | $6.05 | $6.56 | $6.89 |
| 32.8B | [Qwen3 32B](https://openrouter.ai/qwen/qwen3-32b) | $0.08 / $0.28 | $0.039 | $1.01 | $1.37 | $3.22 | $5.07 | $5.50 | $5.78 |

Gemma 4B's canonical page currently lists $0.04/$0.05; cached pricing subpages showed
$0.05/$0.10. These calculations use the canonical page checked 2026-10-28. Parameter count
does not impose a monotonic API price: hosting/provider economics differ. 7B and 14.8B are
nearby available models for the requested 6B/16B categories, not claims of exact model sizes.

The legacy ledger records 971 unique Gamejam agent-hour summaries, 1,104,608 input tokens,
110,334 output tokens and **$0.143566 recorded cost**, including 32 parse-error rows. It is an
observed historical ledger total, not a controlled equal-quality/equal-coverage comparison.
V0.2 uses roughly 11.3 times as many requests with larger stateful inputs, so a substantial
relative increase is expected even though the estimated absolute total remains a few dollars.

### What was cached

- Provider prompt caching: the local Codex worker reported 11,385,344 cache-read input tokens
  and 731,859 non-cached input tokens. This is reuse of matching request prefixes, not cached
  answers. Exact cached text spans are not exposed in these logs. Repeated instructions/history
  are the likely source of hits; that attribution is an inference from the worker's structure.
- Application state: task graphs and saved inputs are local JSON. Including this state in a new
  prompt still counts as input; disk storage does not make inference free.
- Completed predictions: the append-only ledger avoids generating a completed turn again.
  UI review, graph rendering and this cost analysis read saved data without model inference.

[OpenRouter caching documentation](https://openrouter.ai/docs/guides/best-practices/prompt-caching)
explains cache-read accounting and provider-specific handling. The 94% cache ratio from the
long-running Codex worker should not be transferred to a direct summarizer pipeline.

Private machine-readable counts/calculations are in `data/v02/cost-sizing/`.
