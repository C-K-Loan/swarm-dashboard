# Classifier comparison (v0.2) — gold = Claude Opus 5.5
Generated: 2026-10-17T23:08:53.237305Z · gold rows: 1000

## Headline
| model | alignment rate | agent_role jaccard | errors | cost/1K msgs |
|---|---:|---:|---:|---:|
| qwen_30b | 5.3% | 0.484 | 0 | $0.0780 |
| gemma_27b | 4.5% | 0.509 | 0 | $0.0780 |
| nemotron_30b | 1.5% | 0.394 | 104 | $0.2810 |
| qwen_235b | 3.6% | 0.535 | 0 | $0.1539 |
| deepseek_flash | 5.1% | 0.435 | 142 | $0.2572 |

## Per-field macro F1
| model | user_intent | agent_intent | blocker_type | urgency | task_phase | addresses |
|---|---:|---:|---:|---:|---:|---:|
| qwen_30b | 0.223 | 0.165 | 0.160 | 0.412 | 0.481 | 0.342 |
| gemma_27b | 0.178 | 0.250 | 0.103 | 0.463 | 0.422 | 0.164 |
| nemotron_30b | 0.198 | 0.297 | 0.145 | 0.312 | 0.507 | 0.176 |
| qwen_235b | 0.363 | 0.241 | 0.082 | 0.394 | 0.442 | 0.135 |
| deepseek_flash | 0.245 | 0.308 | 0.247 | 0.496 | 0.534 | 0.170 |

## Appendix: Anthropic refs (parked, local-only)
| model | alignment rate | agent_role jaccard | cost/1K msgs |
|---|---:|---:|---:|
| haiku | 19.5% | 0.684 | $0.7200 |
| sonnet | 13.8% | 0.687 | $2.7000 |
| fable | 17.7% | 0.726 | $0.9000 |
