# Classification Schema v0.2 + Comparative Metrics Plan

**Status**: PROPOSED (awaiting CKL lock)
**Supersedes**: schema v0.1 (shipped, deck slide 3 numbers from this)
**Owner**: nebius-hack-brainstorm → nebius-hack-dev once locked

---

## 1. Why v0.2

v0.1 shipped 5 fields and 3 Nebius tiers hit ~90% agreement vs Opus on the easy ones. `agent_role` plateaued at **60% across all 3 tiers** — same ceiling as cheap, balanced, and precise. That's the SFT target: if lifting `agent_role` is in-reach with ~3K training pairs, the Devpost story becomes "we fine-tuned a model that beat Haiku-3.5 at $0.65 per 32K msgs."

v0.2 also adds four fields the dashboard needs for its "swarm master" pitch (`task_phase`, `wedge_signal`, `completion_signal`, `escalation_signal`, `addresses`). These give operators actionable signal and give the SFT'd model concepts to learn that no out-of-the-box classifier already has.

---

## 2. Schema v0.2 (DRAFT, not yet locked)

```yaml
# ─── v0.1 fields — kept verbatim for continuity + backward classified data ───
user_intent:   feature-def | feature-update | unblock-context | unblock-infra | status-check | praise | frustration | null
agent_intent:  report-progress | report-blocked | ask-clarification | handoff | celebration | summary | null
blocker_type:  waiting-human | rate-limit | tool-failure | retry-loop | context-exhausted | scope-ambiguous | null
urgency:       low | mid | high | null

# ─── v0.2 EXPANDED (replaces v0.1 enum, breaks backward compat for this field) ───
agent_role:    dev | tester | debugger | reviewer | planner | coordinator | documenter | ops | analyst | null
# v0.1 had: worker | dev | manager | reviewer | planner
# Mapping for existing data: worker→dev, manager→coordinator (default), reviewer/planner unchanged.
# Reasoning: 'manager' was a dumping ground for both planners and coordinators → 60% raters agreement ceiling.

# ─── v0.2 NEW FIELDS ───
task_phase:         planning | exploring | implementing | testing | debugging | reviewing | documenting | idle | null
wedge_signal:       bool   # agent appears stuck in a loop / going in circles / repeated same action
completion_signal:  bool   # declaring a task done ("shipped", "all green", "committed", etc.)
escalation_signal:  bool   # explicitly needs human intervention (not just an auto-recoverable blocker)
addresses:          human | peer-agent | subagent | self | null  # who is this message FOR
```

**Field count**: 10 (was 5). Expected output: ~75 tokens per classification call (was 50). Full re-classify of current ~45K msg corpus: **+$0.70 Nebius**. Trivial.

### 2.1 Enum defs (for the rubric prompt + raters)

#### `agent_role`
- **dev** — writing production code, Edit/Write/MultiEdit the dominant activity
- **tester** — writing tests, running test suites, interpreting failures
- **debugger** — investigating a failure, no new feature intent
- **reviewer** — reviewing another agent's work, posting findings, no own code changes
- **planner** — strategizing, decomposing, writing briefs, no immediate implementation
- **coordinator** — routing work between agents, status aggregation, cross-agent comms
- **documenter** — writing docs, READMEs, briefs — not for immediate implementation
- **ops** — deploys, migrations, infra, CI/CD, incident triage
- **analyst** — data analysis, evals, metrics, no code writing
- **null** — role unclear from this single message

#### `task_phase`
- **planning** — deciding what to build / how to structure
- **exploring** — reading code, grep, browsing, understanding
- **implementing** — writing code toward a defined target
- **testing** — running/writing tests, verifying
- **debugging** — chasing a specific failure
- **reviewing** — assessing existing work
- **documenting** — writing docs / briefs / summaries
- **idle** — no active phase (waiting, pending)
- **null** — unclassifiable

#### `wedge_signal` (bool)
Positive if the message shows signs of being stuck: repeated attempts, loop language ("tried this already", "same error as before"), context exhaustion mentioned, retry loop numbers, or the model declaring "I'm not sure how to proceed."

#### `completion_signal` (bool)
Positive if the message declares a task done: "shipped", "all tests green", "committed as X", "deployed", "closed PR #Y", explicit DONE markers.

#### `escalation_signal` (bool)
Positive if the message explicitly asks for human input or decision ("@CKL", "need your call", "can't proceed without you"). Distinct from `blocker_type`:
- A blocker is something the system might auto-resolve (rate-limit, tool-failure)
- An escalation NEEDS a human to intervene

Both can be true simultaneously.

#### `addresses`
- **human** — message is for CKL / the operator (either in prose or via explicit @-mention)
- **peer-agent** — `[from <other-agent>]` style a2a, or routing a task sideways
- **subagent** — spawning / briefing a child agent
- **self** — thinking out loud, scratchpad, planning notes with no addressee
- **null** — unclassifiable

---

## 3. Comparative Metrics

### 3.1 Metrics per field (replace raw % as primary)

| metric | why |
|---|---|
| **Accuracy** | continuity with v0.1 deck |
| **Macro F1** | handles class imbalance (e.g. `context-exhausted` is rare) |
| **Cohen's κ vs Opus** | strips random-chance baseline; standard in annotator-agreement lit |
| **Confusion matrix** | shows WHERE each model is wrong, enables "SFT specifically fixed X" claims |
| **Per-class precision/recall** | diagnose which classes a model struggles with |

### 3.2 Per-model summary metrics
| metric | why |
|---|---|
| **Cost per 32K msgs** | continuity with deck slide 2 |
| **Latency P50 / P95** | missing from v0.1 deck, judges will ask |
| **Error rate** (API failures, JSON parse fails, reasoning overrun) | Nemotron Nano had 89/200 overruns in v0.1 — needs its own column |
| **Total eval tokens** (in + out) | reproducibility |

### 3.3 Headline numbers for the deck
```
field       | Qwen3-30B fast | Gemma-27B bal | Nemotron Nano | Qwen3-14B base | Qwen3-14B-SFT (OURS) | Haiku-3.5 | GPT-4o-mini | GPT-4o | Sonnet-3.7 | Opus-5.5 ref
agent_role  |     60%        |     60%        |     61%        |      58%        |      82% ⬆          |   75%     |    65%      |  80%   |    85%     |  100%
task_phase  |     n/a        |     n/a        |     n/a        |      55%        |      85% ⬆          |   n/a     |    n/a      |  n/a   |    n/a     |  100%
wedge_signal|     n/a        |     n/a        |     n/a        |      65%        |      88% ⬆          |   n/a     |    n/a      |  n/a   |    n/a     |  100%
```
(Numbers above are PROJECTED, not measured — real values after eval run.)

The row that pops: `Qwen3-14B-SFT · $0.65 · agent_role 82%` beats Haiku-3.5 at $12.87 AND Nemotron 30B. Devpost headline.

---

## 4. Gold Eval Set

**NEW, do not reuse 200-msg v0.1 holdout.**

- Size: **1000 msgs**
- Stratification:
  - Equal buckets per `agent_role` class (100 msgs each × 10 classes)
  - Spread across all current fleets (gamejam / cct / ghost / other-project / nebius-hack)
  - Spread across time (every month of history represented)
- Labels: Opus 5.5 for all 10 fields (reference set, not ground truth)
- Cost: ~$5 Opus quota
- Location: `data/eval/gold_v2.jsonl` — **FROZEN** once labeled

Secondary adversarial set:
- 200 **hard** msgs — ones where v0.1 Nebius tiers DISAGREED with each other → high-signal for inter-model lift measurement
- Opus labels these too; same freeze rule

---

## 5. SFT Training Set

- Target: **3000 msgs** balanced across fields + classes
- Opus-labeled against v0.2 schema (overlapping with current `data/sft/gamejam_train.jsonl` where re-labelable)
- Nebius Managed SFT: Qwen3-14B base, LoRA, 3 epochs, batch 8, lr 1e-5 (defaults)
- Expected cost: $3-5 Nebius (per earlier estimate in `17-inference-overview.md`)
- Location: `data/sft/train_v2.jsonl`
- Eval cycle: train → deploy custom LoRA → run `scripts/compare_models.py` on gold set → publish lift

---

## 6. Execution Order (to land before Sunday Berlin)

1. ✅ Draft schema v0.2 (this doc)
2. ⏳ CKL review + lock
3. Opus labels gold eval set (1000 msgs, parallel-safe)
4. Opus relabels SFT training set (3000 msgs, parallel-safe)
5. Baseline all 8 non-SFT models on gold → `data/eval/baseline.json`
6. Launch Nebius Managed SFT on `train_v2.jsonl`
7. Deploy LoRA endpoint; eval on gold → `data/eval/sft.json`
8. Dashboard `/eval` page with headline bars + cost-vs-accuracy scatter + confusion matrices
9. Regenerate deck slide 3+ from JSON (no manual numbers)

External spend: ~$20 Opus + $5 Nebius SFT + ~$5 for GPT-4o/mini/Sonnet calls on gold set. **~$30 total new spend.**

Wall-clock: ~4h if Opus labeling runs in parallel with Nebius SFT prep.

---

## 7. Open Questions

- (A) Keep `null` as an option on every field, or force-pick for every field? Deciding: `null` reduces false positives but inflates "unclassifiable" rate.
- (B) Multi-label on some fields (e.g. a message can simultaneously be `debugging` AND `escalation`)? Currently all fields are mutually-exclusive single-label. `wedge_signal`/`completion_signal`/`escalation_signal` are already bool-independent.
- (C) Chain-of-thought scratchpad field (`reasoning: str`, discarded post-parse)? Known to lift structured-JSON accuracy ~5-10% at ~$0.03 extra cost per full fleet pass. Decision: **recommend YES**.
- (D) Thread-context window — classify a msg WITH its previous 2 msgs? Doubles input tokens. Probably lift +5% on all fields. Decision: TBD, run a 100-msg A/B first.
- (E) Few-shot anchoring in the rubric (1 example per enum value)? +~$0.08 per full fleet pass. Decision: **recommend YES**.

---

## 8. Related Files

- `[REDACTED_PATH]` — current prompts + v0.1 pricing
- `scripts/classify_batch.py` — RUBRIC_PROMPT lives here
- `scripts/summarize_buckets.py` — bucket summary prompt
- `data/holdout/claude_reference.jsonl` — v0.1 Opus holdout (200 msgs, deprecated once gold_v2 lands)
- `data/sft/gamejam_train.jsonl` — v0.1 training set (2741 pairs, will be re-labeled against v0.2)
- `docs/deck-draft.md` — slide 3 numbers (will regenerate from v0.2 eval)
