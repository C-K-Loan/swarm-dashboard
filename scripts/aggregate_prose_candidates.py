#!/usr/bin/env python3
"""Aggregate prose summarizer candidates into a single dashboard JSON.

Reads data/eval/prose_candidates/*.jsonl (one JSONL per model slug) and
rolls up:
  per model:
    rows_scored, errors, avg_latency_s, total_cost_usd, cost_per_1k_items_usd,
    bullets_per_section_avg: {started, finished, blocked, key_decisions}
  per bundle × model:
    error_count, cost_sum

Writes data/eval/prose_compare.json for /eval page consumption.

Judge metrics (Opus faithfulness + ROUGE-L + BERTScore) are TBD — this script
reserves slots for them so the eval page can slot them in when C2 lands.
"""
import json
import sys
from pathlib import Path

ROOT = Path(".")
CAND_DIR = ROOT / "data/eval/prose_candidates"
OUT = ROOT / "data/eval/prose_compare.json"


def load_jsonl(path):
    with path.open() as f:
        return [json.loads(l) for l in f if l.strip()]


def bullets_avg(rows, key):
    counts = []
    for r in rows:
        s = r.get("summary") or {}
        v = s.get(key, [])
        counts.append(len(v) if isinstance(v, list) else 0)
    return round(sum(counts) / len(counts), 2) if counts else 0.0


def main():
    results = {}
    for p in sorted(CAND_DIR.glob("*.jsonl")):
        slug = p.stem
        rows = load_jsonl(p)
        errs = sum(1 for r in rows if r.get("_api_error") or r.get("_parse_error"))
        latency = [r.get("latency_s", 0) for r in rows if r.get("latency_s")]
        avg_lat = round(sum(latency) / len(latency), 2) if latency else 0
        total_cost = sum(r.get("usage", {}).get("cost_usd", 0) or 0 for r in rows)
        per_1k = (total_cost / len(rows) * 1000) if rows else 0
        results[slug] = {
            "slug": slug,
            "model": rows[0]["model"] if rows else None,
            "n_rows": len(rows),
            "errors": errs,
            "avg_latency_s": avg_lat,
            "total_cost_usd": round(total_cost, 6),
            "cost_per_1k_items_usd": round(per_1k, 6),
            "bullets_per_section_avg": {
                k: bullets_avg(rows, k)
                for k in ("started", "finished", "blocked", "key_decisions")
            },
            # Judge metrics — filled by C2 when it runs
            "judge": None,  # {"completeness": {"mean": ..., "n": ...}, "faithfulness": ..., "concision": ..., "rouge_l": ..., "bert_f1": ...}
        }
        print(f"[ok] {slug}: {len(rows)} rows, {errs} errs, ${total_cost:.4f}, avg {avg_lat}s", file=sys.stderr)

    out = {
        "schema": "v0.2-prose",
        "n_items_per_model": max((r["n_rows"] for r in results.values()), default=0),
        "candidates": results,
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"[write] {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
