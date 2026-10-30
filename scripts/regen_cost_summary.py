#!/usr/bin/env python3
"""Regenerate data/cost_summary.json from live classified data.

Baseline ratios are fixed (pinned from original v06 holdout comparison):
Haiku 9.0x, 4o-mini 1.6x, 4o 26.6x, Sonnet 33.9x the Nebius Qwen3-30B-A3B spend.
"""
import json, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cost_summary.json"
BASELINES = {
    "claude-haiku-3.5": 9.0,
    "gpt-4o-mini": 1.6,
    "gpt-4o": 26.6,
    "claude-sonnet-3.7": 33.9,
}

def main():
    cost = tin = tout = n = 0
    for f in glob.glob(str(ROOT / "data" / "classified" / "*.jsonl")):
        for line in open(f):
            try: o = json.loads(line)
            except Exception: continue
            u = o.get("usage") or {}
            cost += u.get("cost_usd", 0) or 0
            tin += u.get("in", 0) or 0
            tout += u.get("out", 0) or 0
            n += 1
    # v12-S: pre-computed bucket summarization is a separate Nebius workload; count it
    # in `extras.summarization` so the ticker shows a true total but baseline ratios
    # (per-message classification) remain apples-to-apples.
    sum_cost = sum_in = sum_out = sum_n = 0
    sum_path = ROOT / "data" / "bucket_summaries.jsonl"
    if sum_path.exists():
        for line in open(sum_path):
            try: o = json.loads(line)
            except Exception: continue
            u = o.get("usage") or {}
            sum_cost += u.get("cost_usd", 0) or 0
            sum_in += u.get("in", 0) or 0
            sum_out += u.get("out", 0) or 0
            sum_n += 1
    total_cost = cost + sum_cost
    summary = {
        "ours": {"cost_usd": round(cost, 4), "model": "Qwen/Qwen3-30B-A3B-Instruct-2507", "tier": "fast"},
        "msgs": n,
        "tokens_in": tin,
        "tokens_out": tout,
        "extras": {
            "summarization": {"cost_usd": round(sum_cost, 4), "buckets": sum_n, "tokens_in": sum_in, "tokens_out": sum_out, "model": "Qwen/Qwen3-30B-A3B-Instruct-2507"},
        },
        "nebius_total_cost_usd": round(total_cost, 4),
        "baselines": {k: {"cost_usd": round(cost * r, 4), "ratio": r} for k, r in BASELINES.items()},
    }
    OUT.write_text(json.dumps(summary, indent=2))
    print(f"[done] {OUT}: n={n} ours=${cost:.4f} baselines={ {k:v['cost_usd'] for k,v in summary['baselines'].items()} }")

if __name__ == "__main__":
    main()
