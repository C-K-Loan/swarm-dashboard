# 🔒 Schema v0.2 — BLESSED 2026-10-17

**Status**: locked. Pipeline kicked off.
**Updated**: 2026-10-17
**Design**: Option B — SPLIT rubrics per role (user turns labeled separately from agent turns)

---

## ⬇️ THE FINAL SCHEMA (read this box, then bless or push back)

### A. Per-turn classifier emits 6 fields per message

```yaml
user_intent:   feature-def | feature-update | unblock-context | unblock-infra | status-check | praise | frustration | null
agent_intent:  report-progress | report-blocked | ask-clarification | handoff | celebration | summary | null
agent_role:    List[ dev | tester | debugger | reviewer | planner | coordinator | manager | documenter | ops | analyst | researcher ]
blocker_type:  waiting-human | rate-limit | tool-failure | retry-loop | context-exhausted | scope-ambiguous | null
urgency:       low | mid | high | null
task_phase:    planning | exploring | implementing | testing | debugging | reviewing | documenting | idle | null
```

### B. Deterministic / regex / code-set per turn (NO model call)

```yaml
wedge_signal:       bool                                                  # Reflexion rule: same_action+response ≥ 3 OR actions ≥ 30
completion_signal:  bool                                                  # regex: shipped / all green / committed as / deployed / closed PR
escalation_signal:  bool                                                  # regex: @CKL / @human / need your call / can't proceed / help!
addresses:          human | peer-agent | subagent | self | null           # regex: [from X] → peer; You are <name> → subagent; @-mention → human
fanout_count:       int                                                   # count subagent tool_use blocks
```

### C. Per-bucket summarizer emits 7 fields per (agent, hour) with ≥3 msgs

```yaml
started:        List[str]  (≤5 bullets, 4-12 words each, past tense)      # already shipped v0.1
finished:       List[str]  (≤5)                                           # already shipped v0.1
blocked:        List[str]  (≤3)                                           # already shipped v0.1
key_decisions:  List[str]  (≤3)                                           # already shipped v0.1

fault_author:   user | agent | environment | unclear | null               # NEW v0.2
impact:         List[ unsafe_side_effect | privacy_leak | wrong_final_state | unnecessary_cost | no_impact ]   # NEW v0.2
exit_status:    submitted | exit_cost | exit_context | exit_timeout | exit_format | exit_api | exit_env | exit_command | exit_error | exit_forfeit | null   # NEW v0.2 (hybrid: deterministic where possible, model backfill)
```

### D. Storage-layer metadata (writer sets, not model)

```yaml
annotator_kind:     HUMAN | LLM | CODE
correction:         str | null                           # human override of a wrong auto-label
exit_status_raw:    str                                  # original provider error string beside the normalized enum
```

---

## 🧮 cost impact

| per full-corpus reclassify | v0.1 (shipped) | v0.2 locked | delta |
|---|---|---|---|
| 32K current corpus | $1.43 | $1.73 | +$0.30 |
| 45K corpus (incl live-refresh) | $2.01 | $2.44 | +$0.43 |
| 83K corpus (+ ghost + other-project) | $3.71 | $4.49 | +$0.78 |

**Current Nebius spend**: $2.22 / $3 cap. Hard-stop $2.80. Headroom $0.58.
→ v0.2 reclassify of current 45K comfortably fits (+$0.43 vs headroom $0.58).

---

## 📋 what BLESS triggers (in order)

1. **Lock the 6-field RUBRIC_PROMPT** in `scripts/classify_batch.py`.
2. **Write regex detectors** for `wedge_signal`, `completion_signal`, `escalation_signal`, `addresses`.
3. **Extend `scripts/summarize_buckets.py`** to also emit `fault_author`, `impact`, `exit_status`.
4. **Prepare gold-label bundles** — same prose we've been passing to Qwen (from `data/prose/*.jsonl`), packaged into Claude-context-sized chunks for teacher labeling.
5. **Fire teacher labeling** — Claude Opus 5.5 labels 1000 msgs for the gold eval set (`data/eval/gold_v2.jsonl` FROZEN).
6. **Opus labels 3000 msgs** for the SFT training set (`data/sft/train_v2.jsonl`).
7. **Compare harness** `scripts/compare_models.py` runs 9 models × gold set.
8. **Launch Nebius Managed SFT** on Qwen3-14B with `train_v2.jsonl`.
9. **Deploy LoRA endpoint → re-run compare → dashboard `/eval` page.**

External spend budget: ~$30 (Opus $20 + Nebius SFT $5 + external APIs $5). Fits.
Wall-clock: ~4h parallel (steps 5+6 overlap step 4). Sunday-safe.

---

## 📦 teacher-labeling payload (what we send to Claude for gold labels)

**Same prose payload format we send to Qwen today.** Per-turn classifier labels get one message per call; teacher labels get bundled N-per-call for efficiency.

Current per-turn Qwen payload is defined in `scripts/classify_batch.py`:
```
Role: {ROLE}
Agent fleet: {FLEET}
Project: {PROJECT}
Message:
---
{TEXT}
---
```

For teacher labeling, same per-row payload but bundled 50-per-prompt so one Claude call labels 50 messages:
```
SCHEMA v0.2: [schema definition here]
You are labeling 50 messages for the frozen gold eval set.
Respond with a JSONL array of 50 rows, one per message.

MSG 1
Fleet: gamejam · Project: gamejam · Role: a
---
<prose>
---

MSG 2
Fleet: ghost · Project: other-project · Role: a
---
<prose>
---

...
```

Teacher = Claude Opus 5.5 via your cct branch (per existing memory: use cct not key). Per 50-msg bundle: ~15K input + ~2.5K output tokens → ~$0.11 per bundle · 20 bundles for 1000 gold msgs = **~$2.20 Opus quota** (not Nebius budget).

Bundle preparation script: `scripts/prepare_label_bundles.py` — stratifies across fleets + agent_role classes + time, writes `data/eval/bundles/bundle_NN.txt` for the teacher agent to consume.

---

## 🚀 one-click bless

Say **"bless"** and I'll:
- Spec the schema freeze → `nebius-hack-dev` via cct
- Spec the regex detectors + summarizer extension
- Write `scripts/prepare_label_bundles.py` and generate the 20 bundles
- Fork a Claude teacher agent (`gold-labeler-v2`) and feed it the first bundle
- Run the Playwright pass + dashboard `/eval` scaffolding in parallel

Or push back on any of the 6 classifier fields / 5 derived fields / 7 bucket fields before I lock.

---

## 📚 context (optional reading below)

### why this schema (one-liner per field)

| field | why it earns a slot |
|---|---|
| `user_intent` | kept from v0.1 — maps the human-side ask (features / unblocks / status) |
| `agent_intent` | kept from v0.1 — maps what the agent is doing (progress / blocked / handoff) |
| `agent_role` | **multi-label now** — MAD survey (arXiv 2607.26212) measured role co-occurrence in 151 systems; single-label caused v0.1's 60% ceiling |
| `blocker_type` | kept from v0.1 — classifies WHY blocked (rate-limit / tool-failure / context-exhausted / etc.) |
| `urgency` | kept from v0.1 — time-criticality |
| `task_phase` | NEW — lifecycle stage (planning / implementing / debugging / etc.) enables the phase-heatmap viz |
| `wedge_signal` | NEW — "stuck in a loop" indicator, Reflexion-rule derived (same_action ≥ 3 OR actions ≥ 30) |
| `completion_signal` | NEW — explicit "task done" marker; separate from `exit_status` because single messages can mark completion mid-session |
| `escalation_signal` | NEW — explicitly needs human, distinct from blocker (which can auto-resolve) |
| `addresses` | NEW — audience of this msg (human/peer/sub/self), supports comms-graph edges + spawn-hierarchy |
| `fault_author` (bucket) | NEW — who caused failures in this window: user / agent / environment / unclear |
| `impact` (bucket) | NEW — what the consequence was; `unnecessary_cost` closes the loop with the Nebius $-savings narrative |
| `exit_status` (bucket) | NEW — how the session ended; mostly deterministic from session-end events, model backfills `exit_forfeit` ambiguous cases |

### metrics for comparing models

- Accuracy, Macro F1 per field (continuity)
- PR-AUC for minority classes (replaces Cohen's κ there, per Agent-as-a-Judge paper)
- Cohen's κ for balanced classes
- **Alignment Rate** = % items where model agrees with Opus (headline bar)
- **Judge Shift** per class (signed Δ)
- Pass^k reliability
- Latency P50/P95, error rate, cost / 32K msgs
- Honest-floor callout: human IRR is 10-30%, nothing honestly scores above ~85-90%

### models compared (deck slide 3 + `/eval` page)

9 models, same frozen gold set:
- Nebius Qwen3-30B-A3B (fast)
- Nebius Gemma-3-27B (balanced)
- Nebius Nemotron Nano 30B (precise)
- Nebius Qwen3-14B base (control)
- 🎯 Nebius Qwen3-14B-SFT (ours)
- Haiku 3.5 (ext ref)
- GPT-4o-mini (ext ref)
- Sonnet 3.7 (ext ref)
- Opus 5.5 (reference truth)

Headline we're hunting: `Qwen3-14B-SFT @ $0.65 beats Haiku-3.5 @ $12.87` on `agent_role` + `task_phase` + `wedge_signal`.

### the one judge-killer answer

Anthropic's own multi-agent paper warns against multi-agent-ing coding tasks (dense shared context). A Devpost judge who read that paper will ask about this.

**Answer**: "We observe parallel coding agents that already exist in CKL's workflow — we don't re-architect anything, we measure what's happening. The dashboard is for workflows that are ALREADY parallel, not a recommendation to parallelize new ones."

Keep this in the deck Q&A appendix.

### related files

- `scripts/classify_batch.py` — per-turn RUBRIC_PROMPT lives here (needs v0.2 edit on bless)
- `scripts/summarize_buckets.py` — per-bucket summarizer prompt (needs forensic-trio extension on bless)
- `scripts/wedge_detector.py` · `scripts/signal_detector.py` · `scripts/addresses_detector.py` · `scripts/build_exit_status_index.py` — all to be written on bless
- `scripts/prepare_label_bundles.py` — to be written for teacher-label bundling
- `scripts/compare_models.py` — 9-model comparison harness, to be written
- `research-findings.md` — full 43-source research table (reference, not required reading)
- `schema-v0.2-and-metrics.md` — first-draft proposal (superseded by this doc)
