#!/usr/bin/env python3
"""Prepare paired (gold, candidate) prose summaries for LLM-judge scoring.

For each candidate slug, writes data/eval/prose_judge/<slug>_pairs.jsonl where
each row is a judge-ready input:
  {sid, bucket_start, project, msg_count, gold_summary, cand_summary}

A judge subagent reads this and emits <slug>_llm.jsonl with per-pair scores:
  {sid, bucket_start, completeness: 1-5, faithfulness: 1-5, concision: 1-5, notes}
"""
import json
import sys
from pathlib import Path

ROOT = Path(".")
GOLD = ROOT / "data/eval/prose_gold.jsonl"
CAND_DIR = ROOT / "data/eval/prose_candidates"
OUT_DIR = ROOT / "data/eval/prose_judge"


def main():
    gold_map = {}
    for l in GOLD.open():
        r = json.loads(l)
        gold_map[(r["sid"], r["bucket_start"])] = r

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug in ("gemma_27b", "qwen_30b"):
        pairs = []
        for l in (CAND_DIR / f"{slug}.jsonl").open():
            r = json.loads(l)
            if r.get("_parse_error") or r.get("_api_error"):
                continue
            key = (r["sid"], r["bucket_start"])
            if key not in gold_map:
                continue
            g = gold_map[key]
            pairs.append({
                "sid": r["sid"],
                "bucket_start": r["bucket_start"],
                "project": r.get("project"),
                "msg_count": r.get("msg_count"),
                "gold_summary": g["summary"],
                "cand_summary": r["summary"],
            })
        out_path = OUT_DIR / f"{slug}_pairs.jsonl"
        with out_path.open("w") as f:
            for p in pairs:
                f.write(json.dumps(p) + "\n")
        print(f"[{slug}] {len(pairs)} judge pairs → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
