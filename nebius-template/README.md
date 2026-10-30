# Nebius Reusable Deployment Template — `cct-fleet-compressor`

**For**: Cloud & Serverless track submission
**Pattern match**: *"reusable deployment template using Nebius infrastructure"* (exact rubric wording)

Fork this template, point it at your own agent fleet transcripts, redeploy on Nebius. Same architecture serves any multi-agent setup (CrewAI, LangGraph, AutoGen, CCT, Claude Code, Codex, Cursor).

## What this template deploys

1. **Prose extractor** — Python script that walks any coding-agent transcript directory and emits canonical JSONL (schema v0.1)
2. **Classification pipeline** — concurrent Nebius Token Factory client that labels each prose turn with (user_intent, agent_intent, agent_role, blocker_type, urgency)
3. **Reduction service** — collapses N classified turns into one evidence-backed brief per agent
4. **Serving endpoint** — Nebius Serverless container exposing an OpenAI-compatible `/summarize` route
5. **Dashboard** — Astro static page polling the serving endpoint, rendering agent cards grouped by project/fleet
6. **Fine-tuning hook** — one-shot script to kick a Nebius Managed SFT job on your transcript data

## Nebius products used

| Product | Role |
|---|---|
| Token Factory | 3-tier classifier backend (fast / balanced / precise, aliases cheap|mid|expensive for 1-week grace) |
| Managed SFT | Fine-tune Qwen3-14B on your own fleet traces |
| Serverless | Deploy the summarizer as an API endpoint |
| Deploy Custom LoRA | Serve your fine-tuned checkpoint via pay-per-token |

## 1-command bring-up

```bash
# From a fresh clone:
./scripts/bootstrap.sh \
  --fleet-dir [REDACTED_PATH] \
  --cost-tier fast \
  --dashboard-port 4321
```

What it does:
1. Scans fleet-dir for *.jsonl transcripts
2. Extracts prose to `data/prose/*.jsonl`
3. Classifies via Nebius Token Factory at chosen tier (default: fast, ~$0.001 per 100 messages)
4. Starts dashboard on local port
5. Prints the URL

## Fine-tune for your fleet (optional, post-polish)

```bash
./scripts/finetune_on_my_fleet.sh \
  --training-pairs data/sft-pairs.jsonl \
  --base-model Qwen/Qwen3-14B
```

Submits a Managed SFT job to Nebius AI Cloud, then deploys the resulting LoRA as a Token Factory endpoint. Dashboard automatically picks the custom checkpoint for the "precise" tier.

## Pricing reference (as of 2026-10-16)

| Backend | Qwen3-30B-A3B (fast) | Gemma-3-27B (balanced) | Nemotron Nano 30B A3B (precise, upgrades to SFT'd Qwen3-14B) | Claude Haiku 3.5 (frontier) |
|---|---|---|---|---|
| Cost per 100 agent messages | ~$0.005 | ~$0.015 | ~$0.012 | ~$0.080 |

Delta: ~16x cheaper than frontier at the fast tier. SFT'd checkpoint can match frontier quality at ~7x cheaper.

## Repo layout (what you get)

```
cct-fleet-compressor/
├── scripts/
│   ├── extract_prose.py       # bundle index → canonical JSONL
│   ├── classify_batch.py      # concurrent Token Factory classifier
│   ├── bootstrap.sh           # 1-command bring-up
│   └── finetune_on_my_fleet.sh
├── ui/                        # Astro dashboard
├── other-project/               # ghost/cct-summarizer MCP wrapper
├── data/
│   ├── prose/*.jsonl
│   └── classified/*.jsonl
└── docs/
    ├── schema-v0.1.md
    └── MENTOR-QUESTIONS.md
```

## Changelog

- 0.1.0 (2026-10-16): initial release at Nebius Build Week hackathon
- (planned 0.2.0): Devpost polish week — SFT'd Qwen3-14B shipped, Haiku + 4o-mini baseline comparison added
- (planned 0.3.0): CrewAI + LangGraph transcript parsers
