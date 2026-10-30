#!/usr/bin/env python3
"""Judge prose candidates against prose_gold.jsonl.

Computes LOCAL automatic metrics for each (sid, bucket_start)-aligned pair:
  - ROUGE-L F1 on the concatenated bullets of each section (started/finished/
    blocked/key_decisions), averaged across the 4 sections.
  - BERTScore F1 on the same concatenated bullets, averaged across the 4 sections.
  - Bullet-count delta per section vs gold.

LLM-judge dims (completeness / faithfulness / concision) are intentionally NOT
computed here — those come from a separate judge pass that writes
data/eval/prose_judge/<slug>_llm.jsonl which this script merges on the next run.

Writes:
  data/eval/prose_judge/<slug>.jsonl       — per-pair row with all metrics
  data/eval/prose_judge/<slug>_agg.json    — aggregate: n_pairs, mean rouge_l,
                                              mean bert_f1, mean bullet counts,
                                              mean LLM dims (if _llm.jsonl present)
  data/eval/prose_judge_summary.json       — slugs → {metrics for /eval}
"""
import argparse
import json
import os
import sys
from pathlib import Path
from statistics import mean

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")

from rouge_score import rouge_scorer
import bert_score

ROOT = Path(".")
GOLD = ROOT / "data/eval/prose_gold.jsonl"
CAND_DIR = ROOT / "data/eval/prose_candidates"
OUT_DIR = ROOT / "data/eval/prose_judge"

SECTIONS = ("started", "finished", "blocked", "key_decisions")


def flatten_section(summary: dict, section: str) -> str:
    v = (summary or {}).get(section) or []
    if not isinstance(v, list):
        return ""
    return " ".join(str(b) for b in v if b)


def load_jsonl(path: Path):
    return [json.loads(l) for l in path.open() if l.strip()]


def rouge_l_f1(ref: str, hyp: str, scorer) -> float:
    if not ref.strip() and not hyp.strip():
        return 1.0  # both empty → trivially aligned
    if not ref.strip() or not hyp.strip():
        return 0.0
    return scorer.score(ref, hyp)["rougeL"].fmeasure


def judge_one(slug: str) -> dict:
    gold_rows = load_jsonl(GOLD)
    gold_map = {(r["sid"], r["bucket_start"]): r for r in gold_rows}
    cand_rows = load_jsonl(CAND_DIR / f"{slug}.jsonl")
    cand_rows = [r for r in cand_rows if not r.get("_parse_error") and not r.get("_api_error")]

    pairs = [(gold_map[(r["sid"], r["bucket_start"])], r)
             for r in cand_rows if (r["sid"], r["bucket_start"]) in gold_map]
    print(f"[{slug}] pairs aligned: {len(pairs)} (gold={len(gold_rows)}, cand_clean={len(cand_rows)})", file=sys.stderr)
    if not pairs:
        return {"slug": slug, "n_pairs": 0, "error": "no aligned pairs"}

    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)

    # Collect section-level refs/hyps for BERTScore batch call
    all_refs = []
    all_hyps = []
    row_section_offsets = []  # [(start_idx, 4)] per pair
    for g, c in pairs:
        start = len(all_refs)
        for sec in SECTIONS:
            all_refs.append(flatten_section(g["summary"], sec) or "(empty)")
            all_hyps.append(flatten_section(c["summary"], sec) or "(empty)")
        row_section_offsets.append(start)
    print(f"[{slug}] BERTScore batch: {len(all_refs)} items …", file=sys.stderr)
    _, _, bert_f1 = bert_score.score(all_hyps, all_refs, lang="en", verbose=False, batch_size=64, model_type="microsoft/deberta-xlarge-mnli", rescale_with_baseline=True)
    bert_f1 = bert_f1.tolist()
    print(f"[{slug}] BERTScore done", file=sys.stderr)

    rows = []
    for i, (g, c) in enumerate(pairs):
        off = row_section_offsets[i]
        sec_scores = {}
        for j, sec in enumerate(SECTIONS):
            ref = flatten_section(g["summary"], sec)
            hyp = flatten_section(c["summary"], sec)
            rl = rouge_l_f1(ref, hyp, scorer)
            bf = bert_f1[off + j]
            sec_scores[sec] = {
                "rouge_l": round(rl, 4),
                "bert_f1": round(bf, 4),
                "n_ref_bullets": len((g["summary"] or {}).get(sec, []) or []),
                "n_hyp_bullets": len((c["summary"] or {}).get(sec, []) or []),
            }
        row = {
            "sid": c["sid"],
            "bucket_start": c["bucket_start"],
            "project": c.get("project"),
            "fleet": c.get("fleet"),
            "msg_count": c.get("msg_count"),
            "sections": sec_scores,
            "mean_rouge_l": round(mean(s["rouge_l"] for s in sec_scores.values()), 4),
            "mean_bert_f1": round(mean(s["bert_f1"] for s in sec_scores.values()), 4),
        }
        rows.append(row)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{slug}.jsonl"
    with out_path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # Try to merge LLM-judge dims if present
    llm_path = OUT_DIR / f"{slug}_llm.jsonl"
    llm_scores = {}
    if llm_path.exists():
        for l in llm_path.open():
            o = json.loads(l)
            llm_scores[(o["sid"], o["bucket_start"])] = o

    agg = {
        "slug": slug,
        "n_pairs": len(rows),
        "mean_rouge_l": round(mean(r["mean_rouge_l"] for r in rows), 4),
        "mean_bert_f1": round(mean(r["mean_bert_f1"] for r in rows), 4),
        "per_section_rouge_l": {sec: round(mean(r["sections"][sec]["rouge_l"] for r in rows), 4) for sec in SECTIONS},
        "per_section_bert_f1": {sec: round(mean(r["sections"][sec]["bert_f1"] for r in rows), 4) for sec in SECTIONS},
        "mean_bullets_hyp": {sec: round(mean(r["sections"][sec]["n_hyp_bullets"] for r in rows), 2) for sec in SECTIONS},
        "mean_bullets_ref": {sec: round(mean(r["sections"][sec]["n_ref_bullets"] for r in rows), 2) for sec in SECTIONS},
    }
    if llm_scores:
        dims = ("completeness", "faithfulness", "concision")
        matched_llm = [llm_scores[k] for r in rows for k in [(r["sid"], r["bucket_start"])] if k in llm_scores]
        for dim in dims:
            vals = [x.get(dim) for x in matched_llm if isinstance(x.get(dim), (int, float))]
            agg[f"mean_{dim}"] = round(mean(vals), 3) if vals else None
            agg[f"n_{dim}"] = len(vals)
    (OUT_DIR / f"{slug}_agg.json").write_text(json.dumps(agg, indent=2))
    print(f"[{slug}] wrote {out_path.name} ({len(rows)} rows), agg mean_rouge_l={agg['mean_rouge_l']} mean_bert_f1={agg['mean_bert_f1']}", file=sys.stderr)
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", nargs="+", default=["gemma_27b", "qwen_30b"])
    args = ap.parse_args()
    summary = {}
    for slug in args.slugs:
        agg = judge_one(slug)
        summary[slug] = agg
    out = OUT_DIR / "prose_judge_summary.json"
    out.write_text(json.dumps({"schema": "v0.2-prose-judge", "candidates": summary}, indent=2))
    print(f"[write] {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
