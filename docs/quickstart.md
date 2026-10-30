# Quickstart

Everything here runs locally. Nothing in this repository uploads, deploys or calls a paid API unless you run a script that says so in its header and pass it a key.

## 1. Requirements

| Tool | Version used | Needed for |
|---|---|---|
| Node.js | 20+ | dashboard (`ui/`) |
| Python | 3.10+ | pipelines (`scripts/`) |
| `jsonschema` (pip) | any recent | V0.2 validator |
| `huggingface_hub[cli]` (pip) | any recent | downloading the dataset and adapters |
| `transformers` (pip) | any recent | token counting in the SFT exporter only |
| `modal` (pip) | 1.x | only if you serve adapters on Modal |

## 2. Get the data

The dashboard and the pipelines read a `data/` directory that is not part of this repository. The public copy is the Hugging Face dataset `wow-cod-thps4-gamejam` (card: `README.md` in the dataset). Download it and lay it out:

```bash
pip install -U "huggingface_hub[cli]"
hf download CKL/wow-cod-thps4-gamejam --repo-type dataset --local-dir ../wow-cod-thps4-gamejam
scripts/setup_public_data.sh ../wow-cod-thps4-gamejam
```

`setup_public_data.sh` copies each dataset folder to the path the code reads (for example `labels/<run>/` -> `data/v02/<run>/`, `prose-v01b/` -> `data/v01b/`, `spend/` -> the spend ledgers in `data/v02/`) and rebuilds every run's `turns.json` from the shared `source/turns.json` (`scripts/v02/restore_run_turns.py`; each run labeled a prefix of the source turns, its `manifest.json` gives the count).

What the release does not contain, and what that means:

- Live fleet indexes (bus, spawn tree, file diffs, media). The `timeline` page shows those panels empty.
- Stage 1 was evaluated on a private 1,000-message set labeled by Claude Opus (not released); a 30-message gamejam subset is included for reproduction. (`stage1-gamejam/`; see [eval.md](eval.md#stage-1).)
- Game media. The dashboard links to clips by path; the files are not distributed.

## 3. Run the dashboard

```bash
cd ui
npm ci
npm run dev                 # http://127.0.0.1:4322
```

Environment variables (all optional):

| Variable | Default | Effect |
|---|---|---|
| `HOST` | `127.0.0.1` | dev server bind address |
| `ALLOWED_HOSTS` | empty | comma list of extra host names Vite accepts |
| `CCT_V02_DATASET` | per page | which `data/v02/<run>` the V0.2 pages open |

Pages: `timeline` (agents x time, state graph, A2A comms, transcript), `training`, `eval` (Summary, Prose v1, Prose v2, v0.3 lean tabs), `review`, `about`, `help`.

### Static demo build (no backend)

```bash
FREEZE_BASE=/projects/swarm-board/ scripts/freeze/build.sh dist-static
mkdir -p /tmp/sb/projects && ln -sfn "$PWD/dist-static/site" /tmp/sb/projects/swarm-board
python3 -m http.server 8791 -d /tmp/sb      # open http://localhost:8791/projects/swarm-board/
```

`scripts/freeze/slice.json` picks the fleet, the turn windows, the runs and the pages. `freeze_data.py` copies only that slice; every API call the pages make is answered from pre-built JSON (`ui/src/lib/frozen-fetch.js`).

## 4. Run the pipelines

Each stage is described in [data-formats.md](data-formats.md). The commands below are the ones that produced the released data.

### Stage 1 and V0.1 hourly bullets

```bash
python3 scripts/extract_prose.py                      # transcripts -> data/prose/<project>.jsonl
python3 scripts/summarize_buckets.py --project gamejam --budget-cents 40
python3 scripts/build_bucket_summary_index.py         # -> ui/public/data/bucket_summaries.json
```

`summarize_buckets.py` calls Qwen3-30B-A3B-Instruct-2507 on Nebius Token Factory (`NEBIUS_TOKEN_FACTORY_KEY`, see `.env.example`) and stops at the budget cap.

### Stage 2: V0.1.B hierarchical merge

```bash
python3 scripts/v01b/hier_merge.py --base-url "$BASE_URL" --key-env API_KEY_ENV_NAME \
  --model Qwen/Qwen3-235B-A22B-Instruct-2507 --name gamejam-day1-q235b
python3 scripts/v01b/judge_merge.py --name gamejam-day1-q235b --max-usd 1.5
```

Any OpenAI-compatible endpoint works for the merger. The released trees used Qwen3-235B and Qwen3-8B (Token Factory, Modal), Llama-3.1-8B (OpenRouter) and Claude Haiku 4.5 (reference).

### State graph: v0.3-lean labeling

```bash
# teacher (Claude subscription CLI, no API key):
python3 scripts/v02/claude_haiku_day1.py --help
# any OpenAI-compatible model, causal rollout over Day 1:
python3 scripts/v02/local_qwen_day1.py --run --renderer v0.3-lean --structured --quote-spans --no-thinking \
  --source day1 --dataset my-run --model Qwen/Qwen3-30B-A3B-Instruct-2507 \
  --base-url https://api.tokenfactory.nebius.com/v1 --api-key-env NEBIUS_TOKEN_FACTORY_KEY \
  --budget-usd 3 --spend-ledger data/v02/_spend.json --price-in <USD/token> --price-out <USD/token>
```

`--budget-usd` is a hard cap on a shared spend ledger, checked before every call against the worst case.

### Training and serving

See [training-nebius-token-factory.md](training-nebius-token-factory.md).

## 5. Tests

```bash
python3 -m pytest -q scripts ui/tests       # 195 passed, 1 skipped on the release tree
python3 scripts/v02/smoke_adapter.py examples/sample-fleet   # offline adapter smoke test
cd ui && npm run test:unit                  # node --test: 36 tests, 35 pass, 1 skipped
cd ui && npm run test:e2e                   # Playwright; needs the dev server and data
```
