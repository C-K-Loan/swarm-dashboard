# v0.2 Schema Research Findings

**Compiled**: 2026-10-17 for the Nebius x NVIDIA hackathon dashboard. Reviews ~50 external sources (Anthropic, OpenAI, DeepMind, academic benchmarks, LLM-observability vendors, multi-agent frameworks) for label taxonomies, metrics, and gotchas usable in v0.2.

Confidence tags per claim: **HIGH** = read directly from source code or primary doc; **MED** = pulled from paper or secondary doc; **LOW** = inferred / not re-verified this round.

---

## Section A: Canonical label taxonomies table

One row per source. The "maps to" column is the closest v0.2 field; "(NEW)" means the source argues for a field we don't yet have.

| # | Source | Label / rubric name | Enum values (verbatim) | Maps to our field | URL | Conf |
|---|---|---|---|---|---|---|
| A1 | Anthropic multi-agent research system | Orchestrator / subagent taxonomy | `LeadResearcher`, `Subagent`, `CitationAgent`, `ToolTestingAgent` | `agent_role` | https://www.anthropic.com/engineering/multi-agent-research-system | HIGH |
| A2 | Same (failure catalog) | Degenerate-trajectory modes | `over_spawning`, `endless_search`, `duplicated_work`, `verbose_query`, `wrong_tool`, `source_bias`, `hallucination`, `context_overflow`, `synchronous_bottleneck`, `compounding_error`, `deployment_disruption` | `wedge_signal` | same | HIGH |
| A3 | Same (query tiering) | Query complexity | `fact_finding`, `comparison`, `complex_research` | (NEW) `query_complexity` | same | HIGH |
| A4 | Anthropic "Building effective agents" | Workflow patterns | `prompt_chaining`, `routing`, `parallelization`, `orchestrator_workers`, `evaluator_optimizer`, `autonomous_agent` | `agent_role` secondary / (NEW) `pattern` | https://www.anthropic.com/engineering/building-effective-agents | HIGH |
| A5 | Claude Code hooks reference | Lifecycle events (33) | `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`, `TeammateIdle`, `Stop`, `StopFailure`, `PermissionRequest`, `PermissionDenied`, `PreCompact`, `WorktreeCreate`, ... | `task_phase`, `completion_signal`, `escalation_signal` | https://code.claude.com/docs/en/hooks | HIGH |
| A6 | Claude Code `StopFailure` matcher | Terminal API fault | `rate_limit`, `overloaded`, `billing_error`, `server_error`, `unknown` | `blocker_type` | same | HIGH |
| A7 | Claude Code `Notification` matcher | User-prompt events | `permission_prompt`, `idle_prompt`, `auth_success`, `elicitation_dialog` | `escalation_signal`, `wedge_signal` | same | HIGH |
| A8 | Agent SDK result subtypes | Termination | `success`, `error_max_budget_usd`, `concurrent_subagent_limit`, `error_max_turns`, `rate_limit`, ... | `completion_signal` | https://code.claude.com/docs/en/agent-sdk/subagents | HIGH |
| A9 | SHADE-Arena | Sabotage judge | `suspicion ∈ [0,100]`; main_task × side_task × ¬detected | (NEW) `impact_class` (safety variant) | https://arxiv.org/abs/2506.15740 | MED |
| A10 | Agentic Misalignment Summer 2026 | Judge label | `COMPLIANT`, `NON_COMPLIANT`, `DECLINE_TO_LABEL` + action class `covert`/`disclosed`/`refusal` | `agent_intent`, `escalation_signal` | https://alignment.anthropic.com/2026/agentic-misalignment-summer-2026/ | HIGH |
| B1 | openai/swarm `types.py` | Handoff | `Response.agent: Agent`; a tool returning `Result(agent=X)` reassigns | `agent_role` + `addresses` | https://github.com/openai/swarm/blob/main/swarm/types.py | HIGH |
| B2 | OpenAI CUA / Operator | Action verb | `click`, `double_click`, `type`, `keypress`, `scroll`, `wait`, `screenshot`, `drag` | `agent_intent` (verb) / `task_phase` | https://learn.microsoft.com/en-my/Azure/foundry-classic/openai/how-to/computer-use | MED |
| B3 | OpenAI Codex CLI JSONL | `item.type` | `message`, `reasoning`, `function_call`, `function_call_output`, `command_execution`, `apply_patch` | `task_phase` | https://github.com/openai/codex | MED |
| B4 | SWE-bench harness | `ResolvedStatus` | `RESOLVED_NO`, `RESOLVED_PARTIAL`, `RESOLVED_FULL` | `completion_signal` (3-tier) | https://github.com/princeton-nlp/SWE-bench/blob/main/swebench/harness/constants/__init__.py | HIGH |
| B5 | Same | `TestStatus` + `EvalType` | `FAILED`, `PASSED`, `SKIPPED`, `ERROR`, `XFAIL`; `FAIL_TO_PASS`, `PASS_TO_PASS`, `FAIL_TO_FAIL`, `PASS_TO_FAIL` | `completion_signal` detail | same | HIGH |
| B6 | SWE-bench `infra_failure.py` | Infra tier | `TIER_ENVIRONMENT`, `TIER_AMBIGUOUS` with reasons `browser_launch_failed`, `display_unavailable`, `out_of_memory`, `container_unavailable`, `network_unreachable`, `missing_module`, `no_tests_collected`, `tests_timed_out` | `blocker_type` + `escalation_signal` | https://github.com/princeton-nlp/SWE-bench/blob/main/swebench/harness/infra_failure.py | HIGH |
| B7 | SWE-agent `HistoryItem.message_type` | Per-turn role | `thought`, `action`, `observation` | `task_phase` (micro) | https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/types.py | HIGH |
| B8 | SWE-agent `exit_status` | Terminal reason (11) | `submitted`, `submitted (<reason>)`, `exit_command`, `exit_forfeit`, `exit_total_execution_time`, `exit_command_timeout`, `exit_context`, `exit_cost`, `exit_api`, `exit_environment_error`, `exit_error`, `exit_format` | `completion_signal` + `wedge_signal` | https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/agent/agents.py | HIGH |
| B9 | TAU-bench `FaultAuthor` / `FaultType` | Fault attribution | author ∈ `{user, agent, environment}` × type ∈ `{called_wrong_tool, used_wrong_tool_argument, goal_partially_completed, other}` | (NEW) `fault_author` + `blocker_type` | https://github.com/sierra-research/tau-bench/blob/main/auto_error_identification.py | HIGH |
| B10 | TAU-bench `GradingStrategy` | Judge mode | `actions`, `outputs` | (NEW) `grading_strategy` | same | HIGH |
| B11 | AgentBench finish reasons | Trajectory end | `Context Limit Exceeded`, `Invalid Format`, `Invalid Action`, `Task Limit Exceeded`, `Completed` | `wedge_signal` + `completion_signal` | https://arxiv.org/html/2308.03688v3 | MED |
| B12 | GAIA | Task level | `Level ∈ {1,2,3}` (step/tool count bands) | (NEW) `difficulty_level` | https://arxiv.org/pdf/2311.12983 | MED |
| B13 | MLE-bench medal tier | Grade | `gold_medal`, `silver_medal`, `bronze_medal`, `above_median`, `valid_submission`, `submission_exists` | `completion_signal` | https://github.com/openai/mle-bench/blob/main/mlebench/grade.py | HIGH |
| B14 | OpenHands `EvalOutput` | Harness record | `instance_id`, `test_result`, `history`, `metrics`, `error`; `exit_status: "submit"` or error string | `completion_signal` + `agent_role`(`agent_class`) | https://github.com/OpenHands/benchmarks/blob/main/legacy/utils/shared.py | HIGH |
| B15 | ToolBench `ToolEval` | Trajectory judge | `Pass`, `Fail`, `Unsure` | `completion_signal` (3-way) | https://arxiv.org/pdf/2307.16789 | MED |
| C1 | OpenTelemetry GenAI | `gen_ai.operation.name` | `create_agent`, `invoke_agent`, `execute_tool`, `chat`, `text_completion`, `embeddings` | `task_phase` (coarse) + `agent_role` | https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-agent-spans.md | HIGH |
| C2 | OTel GenAI | `finish_reasons` | provider-specific array (NOT normalized) | `completion_signal` (raw) | same | HIGH |
| C3 | OpenInference / Arize Phoenix | `openinference.span.kind` | `LLM`, `EMBEDDING`, `CHAIN`, `RETRIEVER`, `RERANKER`, `TOOL`, `AGENT`, `GUARDRAIL`, `EVALUATOR`, `PROMPT`, `DECISION` | `task_phase` + `agent_role` | https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md | HIGH |
| C4 | OpenInference | `annotation.annotator_kind` | `HUMAN`, `LLM`, `CODE` | (NEW) `annotator_kind` or mirror of `addresses` | same | HIGH |
| C5 | OpenInference | `graph.node.{id,name,parent_id}` | free-form; **only semconv with explicit parent edge** | `addresses` | same | HIGH |
| C6 | Langfuse scores | `dataType` | `NUMERIC`, `CATEGORICAL`, `BOOLEAN` | signal-generic | https://langfuse.com/docs/scores/data-model | HIGH |
| C7 | LangSmith feedback | Feedback shape | `key`, `score`, `value`, `correction`, `comment`, `feedback_source` | (NEW) `correction` slot | https://langsmith-sdk.readthedocs.io/en/stable/schemas/langsmith.schemas.Feedback.html | HIGH |
| C8 | LangSmith run_type | Trace node kind | `llm`, `chain`, `tool`, `retriever`, `parser`, `embedding` | `task_phase` | same | HIGH |
| C9 | Datadog LLM Obs | `capture_intent` | model-generated reason-for-tool-call (free string) | **direct hit on `agent_intent`** | https://docs.datadoghq.com/llm_observability/setup/sdk/ | HIGH |
| C10 | Datadog span kind | — | `llm`, `workflow`, `agent`, `tool`, `task`, `embedding`, `retrieval` | `task_phase` + `agent_role` | same | HIGH |
| C11 | W&B Weave `AnnotationSpec` | Reconfigurable schema | `name`, `description`, `field_schema{type, enum, min, max}`; feedback becomes `wandb.annotation.<name>` | schema-evolution pattern | https://docs.coreweave.com/weave/guides/tracking/feedback | HIGH |
| C12 | CrewAI `Agent` | Role/goal split | `role`, `goal`, `backstory`, `allow_delegation:bool` | `agent_role` + `agent_intent` + `escalation_signal` | https://docs.crewai.com/concepts/agents | HIGH |
| C13 | AutoGen AgentChat termination | 11-way enum | `MaxMessage`, `TextMention`, `StopMessage`, `TokenUsage`, `Handoff`, `SourceMatch`, `External`, `Timeout`, `TextMessage`, `FunctionCall`, `Functional` | **3-way split**: completion/wedge/escalation | https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html | HIGH |
| C14 | AutoGen message type | Addresses | `TextMessage`, `StopMessage`, `ToolCallSummaryMessage`, `HandoffMessage` | `addresses` + `escalation_signal` | same | HIGH |
| C15 | LangGraph `Command` + `interrupt` | HITL control | `resume`, `goto`, `update`, `graph`; HITL patterns `approve/reject`, `review_and_edit`, `tool_review`, `validate_input`, `multi_interrupt` | `escalation_signal` taxonomy | https://docs.langchain.com/oss/langgraph/interrupts | HIGH |
| C16 | Vercel AI SDK | `finishReason` | `stop`, `length`, `content-filter`, `tool-calls`, `error`, `other` | `completion_signal` | https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text | HIGH |
| D1 | ChatDev | Role + phase | roles `CEO,CPO,CTO,Programmer,Reviewer,Tester,Designer`; phases `Designing, Coding, Testing, Documenting`; completion `<END>` token | `agent_role` + `task_phase` + `completion_signal` | https://arxiv.org/abs/2307.07924 | HIGH |
| D2 | MetaGPT | SOP roles | `Product Manager`, `Architect`, `Project Manager`, `Engineer`, `QA Engineer` | `agent_role` | https://arxiv.org/abs/2308.00352 | HIGH |
| D3 | Reflexion | Wedge heuristic | fire self-reflection when `same_action_response_repeat ≥ 3` OR `actions_taken ≥ 30` | **direct hit on `wedge_signal` formula** | https://arxiv.org/abs/2303.11366 | HIGH |
| D4 | Voyager | Curriculum phases | `curriculum_propose`, `skill_execute`, `skill_verify`; artifact admitted iff self-verified | `task_phase` + novel completion | https://arxiv.org/abs/2305.16291 | HIGH |
| D5 | Agent-as-a-Judge (DevAI) | Metrics | `Requirements Met (I)`, `Requirements Met (D)`, `Task Solve Rate`, `Alignment Rate`, `Judge Shift` | metrics (see Section C) | https://arxiv.org/abs/2410.10934 | HIGH |
| D6 | TheAgentCompany | Task category + checkpoint | category ∈ `{SWE,PM,DS,Admin,HR,Finance,Other}`; checkpoints `Action Completion`, `Data Accuracy`, `Collaboration`; failure patterns `early_give_up`, `looping/wandering`, `stuck_on_popup`, `fake_shortcut_creation` | `wedge_signal` + `addresses` | https://arxiv.org/abs/2412.14161 | HIGH |
| D7 | AgentCoder / MapCoder | Role sub-enums | `Programmer, Test Designer, Test Executor`; `Retriever, Planner, Coder, Debugger` | `agent_role` | https://arxiv.org/abs/2312.13010 / https://arxiv.org/abs/2405.11403 | HIGH |
| D8 | Multi-Agent Debate survey | Role shares in 151 systems | `Debater` 100%, `Judge` 38%, `Summarizer` 22%, `Leader` 17%, `Verifier` 6%, `Editor` 3%; **roles co-occur** | `agent_role` must be **multi-label** | https://arxiv.org/abs/2607.26212 | HIGH |
| D9 | AgentAtlas | Fault + impact | 9-value `failure_mode`, 6-value `control_decision` (`Act,Ask,Refuse,Stop,Confirm,Recover`), 5-value `impact` (`unsafe_side_effect, privacy_leak, wrong_final_state, unnecessary_cost, no_impact`) | `blocker_type` + `agent_intent` + (NEW) `impact_class` | https://arxiv.org/abs/2605.20530 | HIGH |

---

## Section B: Proposed NEW fields (ranked by impact × SFT-lift likelihood)

Scoring: *impact* = how much this changes the dashboard story. *SFT-lift* = likelihood that a 14B student model can learn it from Opus labels in 3K pairs. Rank mixes both; "add" means recommended for v0.2 lock; "defer" means collect but don't display.

| Rank | New field | Enum | Impact | SFT-lift | Decision | Primary evidence |
|---|---|---|---|---|---|---|
| 1 | **`fault_author`** | `user, agent, environment, unclear` | HIGH — splits "the agent screwed up" from "the system screwed up"; drives escalation routing | HIGH — the signal is almost always surface-level | **ADD** | TAU-bench B9, SWE-bench B6, AgentAtlas D9 |
| 2 | **`impact_class`** | `unsafe_side_effect, privacy_leak, wrong_final_state, unnecessary_cost, no_impact, null` | HIGH — a dashboard that distinguishes cost burn from safety hit sells; `unnecessary_cost` ties directly to Nebius $-metric | MED — needs context, judge may be noisy | **ADD** | AgentAtlas D9, SHADE-Arena A9 |
| 3 | **`exit_status`** (separate from `completion_signal`) | SWE-agent 11-value enum normalized to `{submitted, exit_cost, exit_context, exit_timeout, exit_format, exit_api, exit_env, exit_command, exit_error, exit_forfeit}` | HIGH — most mature external taxonomy; directly addresses "why did this run stop" | HIGH — token-level surface features | **ADD** | SWE-agent B8, AutoGen C13, AgentBench B11 |
| 4 | **`difficulty_level`** | `1, 2, 3` (step/tool count bands) | MED — operator wants to filter easy vs. hard runs; parallels GAIA | MED — mostly inferable from message length + tool count | **ADD** | GAIA B12 |
| 5 | **`pattern`** (workflow pattern) | `prompt_chaining, routing, parallelization, orchestrator_workers, evaluator_optimizer, autonomous_agent` | MED — orthogonal to `agent_role`; demos Anthropic literacy | LOW — requires multi-message context | **DEFER** (collect at session-level, not per-msg) | Building Effective Agents A4 |
| 6 | **`fanout_count`** (integer) | 0..N subagents spawned by this turn | MED — single clearest explanatory signal in Anthropic's multi-agent post (R² 0.80 → 0.95 when added) | HIGH — derivable from tool_use blocks, no judge needed | **ADD as derived** (don't ask the classifier) | Anthropic multi-agent A1 |
| 7 | **`annotator_kind`** | `HUMAN, LLM, CODE` | MED — distinguishes human labels from model labels in the DB; cleaner than embedding in `addresses` | HIGH — metadata, not a judge call | **ADD at storage layer** | OpenInference C4 |
| 8 | **Multi-label `agent_role`** | existing enum but `List[Role]` | MED — MAD survey shows judge+summarizer and other combos are 20-40% of the field | MED-HIGH — relaxes the 60% ceiling v0.1 hit on single-label | **ADD** (requires rubric change; see gotchas) | MAD survey D8, CrewAI C12 |
| 9 | **`grading_strategy`** | `actions, outputs, hybrid` | LOW — tells judges whether to score the trajectory or the final string | HIGH — rubric-level, not per-message | **DEFER** to per-task config, not per-message | TAU-bench B10 |
| 10 | **`correction`** | free-text; a human overriding the auto-label | LOW story but HIGH data value — LangSmith's reserved slot for exactly this | HIGH — not a model output at all | **ADD at storage layer** | LangSmith C7 |

---

## Section C: Evaluation metrics beyond v0.1

v0.1 reports accuracy and macro-F1 vs. Opus. These are necessary but not sufficient. Add the following.

| Metric | Formula | When to use | Source |
|---|---|---|---|
| **Cohen's κ** | κ = (p_o − p_e) / (1 − p_e); p_o observed agreement, p_e chance agreement | Any field where class imbalance makes raw accuracy misleading (`blocker_type`, `wedge_signal`) | standard; already in v0.2 draft |
| **Alignment Rate** | % of items where judge label equals majority of human raters | Headline "did the SFT'd student agree with Opus/humans"; direct Devpost headline | Agent-as-Judge D5 (90.44% on OpenHands) |
| **Judge Shift** | signed Δ = model_metric − human_consensus_metric, per class | Catches systematic drift (e.g. SFT model always says `wedge=false` where humans say true) | Agent-as-Judge D5 |
| **Precision-Recall AUC** per class | integral of P-R curve | **Replace Cohen's κ** for minority classes like `context-exhausted`, `escalation_signal=true`; AaJ explicitly chose this over kappa because positive-class is minority | Agent-as-Judge D5 |
| **Pass^k reliability** | pass^k = (1/n) · Σᵢ C(cᵢ,k) / C(n,k) where cᵢ = correct trials for example i | For fields the model should get right on **every** retry; distinguishes "lucky once" from "reliable" | TAU-bench B9 |
| **Requirements Met (D)** | dependency-aware; a requirement is only credited if its upstream deps are met | For `task_phase` ordering: a message labeled `testing` should not predate a `implementing` message in the same thread | Agent-as-Judge D5 |
| **Finish-reason distribution** | histogram of `exit_status` over the fleet | Operator-level KPI — same shape as AgentBench Table 4 CLE/IF/IA/TLE/Completed | AgentBench B11, SWE-agent B8 |
| **Fanout R²** | regression of outcome quality on {tokens, tool_calls, model_choice, fanout_count} | Validates whether `fanout_count` buys real signal (0.80 → 0.95 in Anthropic's experiment) | Multi-agent A1 |
| **Latency P50 / P95** | distribution | Already in v0.2 draft; keep | Standard |
| **Error rate** (API fail / JSON parse / overrun) | fraction of calls that didn't produce parseable output | Already in v0.2 draft; add overrun separately per Nemotron Nano | Standard |
| **Inter-rater floor** | expected 10–30% disagreement among humans | Set a ceiling: no judge is expected to score above `1 − floor` without overfitting | Agent-as-Judge D5 |

**Dropped on reflection**: a single multi-dimensional judge score. LLM-as-judge surveys consistently find that asking one model call to grade N dimensions in one pass produces coarse, correlated scores. Score each field in its own call (we already do).

---

## Section D: Gotchas / failure modes to avoid

Ten concrete issues the external literature documents, each worth encoding as a validation rule or caveat.

1. **Empty status map ≠ clean run.** SWE-bench grading.py explicitly guards against the case where a test runner never executed and the empty result dict scores as "all passed." Our rubric should require positive evidence of execution before a `completion_signal=true` label is accepted. (Source: B4/B5, HIGH)
2. **Skipped tests are not success.** F2P with status `SKIPPED` must count as unresolved. Dashboard filters that collapse `{PASSED, XFAIL, SKIPPED}` into one bucket hide half the failures. (B4, HIGH)
3. **`submitted (<reason>)` is composite.** SWE-agent trajectories can be both "submitted" and have a secondary failure reason (e.g. `submitted (exit_cost)`). Collapsing to a boolean `done` loses the wedge shape. Our `completion_signal` + `exit_status` split captures this; preserve both. (B8, HIGH)
4. **`exit_status` field name is overloaded.** SWE-agent has 11 values; OpenHands has `"submit"` or an error string; our v0.2 proposal is yet another vocabulary. If we adopt the name, name our values differently (e.g. `end_reason`) or document the mapping. (B8, B14, HIGH)
5. **Advisory classifiers must not change the denominator.** SWE-bench's `infra_failure.py` labels env failures but keeps `unresolved_ids` unchanged — so leaderboard numbers remain comparable. If we add an `escalation_signal` auto-label, it must not silently exclude escalated items from the base accuracy denominator. (B6, HIGH)
6. **Role labels drift inside atomic chats** (ChatDev). Multiple semantic roles collapse to `instructor`/`assistant` during a sub-conversation. If we store `agent_role` at write-time it is stable; if we re-read mid-thread we get a different label. Our pipeline already stores at write-time; keep it that way. (D1, HIGH)
7. **Memory windows cause label decay** (Reflexion Ω ∈ {1,2,3}; Anthropic 200K truncation). Any field that aggregates across history must rehydrate from an immutable store, not from the agent's own working memory. (D3, A1, HIGH)
8. **Auto-labelers must be stateless w.r.t. their own prior labels.** Agent-as-Judge removed its `memory` sub-module because early errors propagated. If our classifier reads back its earlier labels as context, errors compound. Keep each classify call independent. (D5, HIGH)
9. **Judge calls are stochastic without caching.** ToolBench PR scores shifted by several points across GPT-3.5-judge runs; StableToolBench fixed this by pinning the judge model and caching API responses. Our Opus-5.5 reference labels must be seeded + cached, else SFT lift is unmeasurable. (B15, MED)
10. **Finish-reason enums are provider-shaped.** OTel GenAI explicitly keeps `finish_reasons` as raw provider arrays. If we normalize across OpenAI / Anthropic / Nebius into a single enum, we fight every SDK. Store both: `exit_status_raw` (string) and `exit_status_normalized` (our enum). (C2, HIGH)
11. **Role sets co-occur, not partition** (MAD survey). 38% of debate-system judges also summarize; 17% also lead. A single-label `agent_role` enforces a false partition and is the plausible cause of v0.1's 60% ceiling. Switch to `List[Role]` and change the rubric to allow multi-pick. (D8, HIGH)
12. **Human inter-rater disagreement is 10-30%.** Agent-as-Judge measured this on DevAI. Our 82% projected `agent_role` number should be read against that floor — not against 100%. (D5, HIGH)
13. **Prompt-length wedges** (Reflexion heuristic). `same_action + same_response ≥ 3 cycles` OR `actions ≥ 30` is a formal `wedge_signal=true` rule. Encode this as a deterministic pre-check feeding the classifier; don't ask the model to spot its own loops. (D3, HIGH)
14. **Role-pattern inconsistencies in secondary sources.** A 2509.19918 survey misattributes a hierarchy to AgentCoder that isn't in the original. If we cite paper taxonomies in the deck, cite the paper, not the summary. (D7, HIGH)
15. **AutoGen termination enum conflates three concepts** (completion, wedge, escalation). Microsoft tolerates this because the stop-reason is a routing decision inside a graph. Our schema needs them split; use AutoGen as a source of enum values, not shape. (C13, HIGH)
16. **`agent_id` + `parent_tool_use_id` are the only reliable addressing primitives in Claude Code.** They require Agent SDK TS v0.3.292+. If our dashboard reads older sessions, `addresses` has to fall back to prose heuristics. (A5, HIGH)
17. **The multi-agent paper warns against multi-agent-ing coding tasks.** Anthropic says tasks with dense shared context fit poorly. A fleet-of-coding-agents dashboard is exactly that counterexample. Have an answer ready. (A1, HIGH)

---

## Section E: What a Devpost judge who has seen 50 agent-eval projects will think

A judge who has reviewed fifty of these will spot three unoriginal choices in v0.2 and three novel ones.

**Unoriginal.** `user_intent`/`agent_intent`/`agent_role` is the template every CrewAI/AutoGen clone ships; CrewAI has required `role` + `goal` since 2023, MetaGPT shipped the SOP-roles taxonomy in 2308.00352, and ChatDev's `CEO/CTO/Programmer/Reviewer/Tester` is in every multi-agent paper slide deck. A single-label role enum is also the exact design the MAD survey (2607.26212) documents as failing — 38% of judges also summarize — which is a plausible cause of the v0.1 60% ceiling and will look naive to a judge who read that survey. Accuracy vs. a reference model is the same metric every LLM-as-judge paper reports; without Cohen's κ or PR-AUC it reads as a first draft.

**Novel.** The combination of `wedge_signal` + `completion_signal` + `escalation_signal` as **independent booleans** on the same message is unusual — AutoGen conflates them into one 11-value termination enum, TAU-bench splits only completion vs. fault, and no observability vendor (Langfuse, LangSmith, Phoenix, Datadog) ships this three-way split out of the box. The `addresses` field (who the message is *for*) has no equivalent in any surveyed source — the closest is OpenInference's `graph.node.parent_id`, which is structural, not semantic. And the **Qwen3-14B-SFT at $0.65 beats Haiku-3.5 at $12.87** cost-accuracy frontier is a dashboard-ready headline that the agent-eval literature (which stops at success rates) doesn't produce.

**What to add before Sunday to tilt judgment.** Three cheap moves: (1) make `agent_role` multi-label, cite MAD survey, and show a confusion-matrix lift from v0.1's single-label 60% ceiling; (2) add `fault_author` (user/agent/environment) — three values, free from TAU-bench, instantly separates "the model screwed up" from "Nebius had a bad minute" on the dashboard; (3) add `impact_class` from AgentAtlas, including `unnecessary_cost` which closes the loop with the Nebius $-savings story. These three changes convert the schema from "another CrewAI-ish role sheet" into "we read the agent-eval literature and the LLM-observability stack and chose the fields neither alone would surface."

---

## Appendix: Source URLs (deduplicated)

Anthropic / Claude Code:
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://www.anthropic.com/engineering/building-effective-agents
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/agent-sdk/subagents
- https://arxiv.org/abs/2506.15740 (SHADE-Arena)
- https://alignment.anthropic.com/2026/agentic-misalignment-summer-2026/
- https://arxiv.org/abs/2410.09024 (AgentHarm, Gray Swan/UK AISI)

OpenAI + benchmarks:
- https://github.com/openai/swarm/blob/main/swarm/types.py
- https://github.com/openai/codex
- https://github.com/princeton-nlp/SWE-bench
- https://github.com/SWE-agent/SWE-agent
- https://github.com/sierra-research/tau-bench
- https://arxiv.org/html/2308.03688v3 (AgentBench)
- https://arxiv.org/pdf/2311.12983 (GAIA)
- https://github.com/openai/mle-bench
- https://github.com/OpenHands/benchmarks
- https://arxiv.org/pdf/2309.10691 (MINT)
- https://arxiv.org/pdf/2307.16789 (ToolBench)
- https://arxiv.org/pdf/2403.07714 (StableToolBench)

Observability + frameworks:
- https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-agent-spans.md
- https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md
- https://langfuse.com/docs/scores/data-model
- https://langsmith-sdk.readthedocs.io/en/stable/schemas/langsmith.schemas.Feedback.html
- https://docs.helicone.ai/features/advanced-usage/custom-properties
- https://docs.datadoghq.com/llm_observability/setup/sdk/
- https://docs.coreweave.com/weave/guides/tracking/feedback
- https://docs.crewai.com/concepts/agents
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html
- https://docs.langchain.com/oss/langgraph/interrupts
- https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text
- https://mastra.ai/docs/agents/overview

Papers (DeepMind + multi-agent):
- https://arxiv.org/abs/2307.07924 (ChatDev)
- https://arxiv.org/abs/2308.00352 (MetaGPT)
- https://arxiv.org/abs/2303.11366 (Reflexion)
- https://arxiv.org/abs/2305.16291 (Voyager)
- https://arxiv.org/abs/2410.10934 (Agent-as-a-Judge)
- https://arxiv.org/abs/2412.14161 (TheAgentCompany)
- https://arxiv.org/abs/2312.13010 (AgentCoder)
- https://arxiv.org/abs/2405.11403 (MapCoder)
- https://arxiv.org/abs/2607.26212 (MAD survey)
- https://arxiv.org/abs/2605.20530 (AgentAtlas)
- https://arxiv.org/abs/2604.02022 (ATBench)
- https://arxiv.org/abs/2410.02506 (AgentPrune)
- https://llm-as-a-judge.github.io/
