# Documentation map

## Start here

| Doc | Contents |
|---|---|
| [quickstart.md](quickstart.md) | install, data layout, run the dashboard and every pipeline, tests |
| [data-formats.md](data-formats.md) | every file format and schema |
| [eval.md](eval.md) | every Summary-tab number: definition, source, recompute command, caveats |
| [adapt-to-your-harness.md](adapt-to-your-harness.md) | use your own agents' logs: input format, adapter, config, recipes, smoke test |
| [training-nebius-token-factory.md](training-nebius-token-factory.md) | LoRA export, upload, job parameters, timing, serving, the cost lesson |
| [HANDOFF.md](HANDOFF.md) | release checklist and the (not yet executed) publish commands |

## Design notes and history

These were written while building and are kept as a record. Where they disagree with the four docs above, the four docs above are current.

| Doc | What it is |
|---|---|
| [v02/README.md](v02/README.md) | V0.2 state-graph prototype: contract, review loop, local commands, run history |
| [v02/selection-contract.md](v02/selection-contract.md) | how a timeline selection maps to a replayed graph state |
| [v02/training-eval-plan.md](v02/training-eval-plan.md) | the training and eval plan, with open questions as they stood |
| [v02/labeling-cost.md](v02/labeling-cost.md) | labeling workload and cost extrapolation |
| [v02/nemotron-free-comparison.md](v02/nemotron-free-comparison.md) | Nemotron comparison runs |
| [eval/schema-v0.2-and-metrics.md](eval/schema-v0.2-and-metrics.md), [eval/schema-v0.2-synthesis.md](eval/schema-v0.2-synthesis.md), [eval/research-findings.md](eval/research-findings.md) | the earlier per-message classifier schema (Stage 1 v0.2), not the state graph |
| [eval/compare_report.md](eval/compare_report.md) | Stage 1 classifier comparison vs Claude Opus labels |
| `media/` | README GIFs: explainers and dashboard captures |
| `charts/`, `architecture.svg` | legacy V0.1 hackathon figures (pseudo-label era; numbers superseded by eval.md) |
