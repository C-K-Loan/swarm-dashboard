# CCT Swarm Dashboard

Observability for a fleet of concurrent AI coding agents. The dashboard turns raw agent transcripts into a timeline (agents x time), an agent-to-agent message graph and readable summaries, and it ships the pipelines and evals behind those summaries.

The data is one developer's agent fleet (Claude Code and Codex sessions, coordinated by a small harness called `cct`) building a game during a weekend jam: 76 agent sessions, 10,935 prose turns.

## What it summarizes, and how

**Prose pipeline, two stages**

1. **Stage 1 (V0.1):** per message, a small model writes 5 labels (user intent, agent intent, agent role, blocker type, urgency); per agent and hour it writes four bullet lists: started, finished, blocked, key decisions. Production model: Qwen3-30B-A3B-Instruct-2507 on Nebius Token Factory.
2. **Stage 2 (V0.1.B):** the hourly bullets are merged up a two-axis tree: per agent over 1 h, 4 h and the day, and across all agents per time slice. Every merged bullet cites the hourly bullets it came from. Production merger: Qwen3-235B.

**State graph (v0.3-lean)**

Per turn, a model reads the turn plus a compact view of the graph so far and writes a JSON patch: tasks, decisions, relationships, actor role and state, each change backed by an exact quote. A strict validator applies it. Teacher labels (Claude Haiku 4.5, NVIDIA Nemotron 3 Ultra) were distilled into a LoRA on Qwen3-30B-A3B. The LoRA was trained on Nebius Token Factory and served on Modal (vLLM, H100), because Token Factory cannot serve Qwen3-30B-A3B fine-tunes.

## Results

As shown on the dashboard's **eval -> Summary** tab. Every reference is a model; there is no human ground truth.

| Stage | Our model | Agreement with the reference | Cost vs the reference |
|---|---|---|---|
| Stage 1 · Prose v1 | Qwen3-30B-A3B | **55.6%** per-field agreement with Claude Opus 5.5 labels (majority baseline 59.1%) | no cost on record |
| Stage 2 · Prose v2 | Qwen3-235B | **0.94** F1 vs Claude Haiku 4.5 merges (lenient; strict 0.89) | **~90% cheaper** |
| State graph · v0.3 lean | Qwen3-30B-A3B + LoRA v2 | **58.7%** touched-task F1 vs Claude Haiku 4.5 labels | **~95% cheaper** (~91% priced on the same prompt) |
| Overall (Stage 2 + state graph) | | | **~92% cheaper** |

State graph, 131 held-out turns, teacher-forced:

| | Base | LoRA v1 | LoRA v2 |
|---|---|---|---|
| Valid output | 77.1% | 88.5% | 93.9% |
| Touched-task F1 | 37.7% | 57.2% | 58.7% |
| Relationships / turn (Haiku: 0.47) | 0.00 | 0.07 | 0.16 |

Stage 2 mergers, mean F1 over four tree levels: Qwen3-235B 0.94, Qwen3-8B 0.76, Llama-3.1-8B 0.59.

**Caveats, plainly:**

- Stage 1 does **not** beat always-predicting-the-majority-label on accuracy (55.6% vs 59.1%); it does on macro-F1 (0.30 vs 0.12). Frontier Claude models reach 72 to 74% on the same set. Stage 1 was evaluated on a private 1,000-message set labeled by Claude Opus (not released); a 30-message gamejam subset is included for reproduction.
- "Cheaper" uses list prices times token counts. Real bills for Token Factory training, Modal GPU-hours and subscriptions are not included.
- The LoRA emits a third of the reference's relationships, and its validation turns come from the same day as its training turns (later in time, with a gap).
- All state-graph numbers above recompute exactly from the public dataset; see [docs/eval.md](docs/eval.md).

## NVIDIA Nemotron and Nebius

**NVIDIA Nemotron 3 Ultra (550B-A55B, open model)**
- Free second teacher for the project state graph: its labels are part of the LoRA training data, next to Claude Haiku 4.5.
- Strongest open model on held-out state-graph turns: 64.6% task agreement with Claude Haiku, 99.2% valid structured output, and the same relationship rate as Haiku (0.47 per turn).
- Stage 2 summary merger: 0.93 F1 against Claude Haiku's merges, tying Qwen3-235B.
- Stage 1 intent tags: best agent-intent agreement of every model tested (0.81), Claude included.
- Run via OpenRouter's free tier and NVIDIA's build.nvidia.com API (Nemotron is not available for fine-tuning on Token Factory).

**Nebius Token Factory: where it accelerated the work**
- Serverless open-model inference with no infrastructure: Qwen3-30B-A3B labeled all 1,902 day-one turns at about 1,950 turns per hour for USD 0.76; Qwen3-235B merged the summaries of the whole 9-day jam in 7 minutes for USD 0.34; Qwen3-30B-A3B also writes the Stage 1 hourly bullets.
- LoRA fine-tuning: two Qwen3-30B-A3B LoRA jobs (16 and 22 minutes) on teacher-labeled data, no GPU setup. Recipe: [docs/training-nebius-token-factory.md](docs/training-nebius-token-factory.md).
- The fine-tuned 30B then matches an untuned Qwen3-235B on held-out turns at about 19x lower inference cost than Claude Haiku.

## Quick start

```bash
cd ui && npm ci && npm run dev              # http://127.0.0.1:4322
python3 -m pytest -q scripts ui/tests       # pipeline tests
```

The dashboard needs a `data/` directory: get the `cct-swarm-gamejam` dataset and lay it out as described in [docs/quickstart.md](docs/quickstart.md). A static build with no backend: `scripts/freeze/build.sh`.

## Documentation

| Doc | Contents |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | install, data layout, run the dashboard and every pipeline, tests |
| [docs/data-formats.md](docs/data-formats.md) | schemas: source turns, V0.1 labels and bullets, V0.1.B merge tree, v0.3-lean patch and context, SFT rows |
| [docs/eval.md](docs/eval.md) | every Summary-tab number: definition, source file, recompute command |
| [docs/adapt-to-your-harness.md](docs/adapt-to-your-harness.md) | input format, adapter code, config knobs, Claude Code / Codex / chat-log / multi-agent-framework recipes, smoke test |
| [docs/training-nebius-token-factory.md](docs/training-nebius-token-factory.md) | LoRA export, upload, job parameters, timing, serving on vLLM, the cost lesson |
| [docs/index.md](docs/index.md) | map of the remaining design notes |

## Repository layout

| Path | What it is |
|---|---|
| `ui/` | Astro dashboard: timeline, state graph, A2A comms, transcript, training and eval pages |
| `scripts/` | V0.1 pipeline: prose extraction, hourly summaries, Stage 1 classification, fleet indexes |
| `scripts/v01b/` | V0.1.B hierarchical merge and its judges |
| `scripts/v02/` | state-graph pipeline: lean context, teacher and student runners, validator, SFT export, eval |
| `scripts/freeze/` | static demo build |
| `schemas/`, `prompts/` | turn-patch JSON schema and the labeling rubric |

Some V0.1 scripts ingest a live agent fleet from local machine paths (session logs, a message bus). They are kept for reference; their private paths are redacted and they will not run unchanged.

## Data and privacy

The transcripts come from the author's own agents. Before release, secrets, IP addresses, host names, emails, account ids and every path outside the project were replaced with typed placeholders such as `[REDACTED_PATH]`; names of the author's other projects became generic roles; sessions not about the game jam were removed. Game assets are not distributed.

## Trademarks and game content

This was a non-commercial fan game jam. World of Warcraft and Call of Duty are trademarks of their owners (Blizzard Entertainment, Activision); this project is not affiliated with or endorsed by them. No game assets, media or code from those games are distributed here. Where the transcripts mention "reverse engineering", they refer to the agents' own experiments during the jam; no extracted assets, data or code from those games are included in this release.

## License

Code: [Apache-2.0](LICENSE). The dataset and the LoRA adapters are released separately under CC BY 4.0. Some of that data, and the adapters' training data, are Claude and OpenAI model outputs; see the dataset and model cards for the terms caveat.

Built with CKL's private multi-agent harness CCT (to be released in the future); the transcripts in this dataset were produced by agents run under CCT.

Built for the Nebius x NVIDIA AI Builders Hackathon.
