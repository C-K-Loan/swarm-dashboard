#!/usr/bin/env python3
"""Build an SFT dataset for fine-tuning Qwen3-14B on Nebius Managed SFT.

Reads (prose turn, classifier labels) pairs from data/prose/ + data/classified/
and emits Nebius-compatible JSONL training examples.

Output format (per Nebius Managed SFT docs — confirm Fri):
  {"messages": [{"role": "user", "content": "<prompt>"}, {"role": "assistant", "content": "<json labels>"}]}

Context cap: 8K tokens. Script truncates long inputs to fit.

usage: make_sft_dataset.py <prose_jsonl> <classified_jsonl> <out_jsonl> [--holdout_pct 10]
"""
import argparse
import json
import random
import sys
from pathlib import Path
from classify_batch import RUBRIC_PROMPT


def build_pairs(prose_path, classified_path):
    """Join prose turns with their classified labels by src_id + ts."""
    classified = {}
    for l in open(classified_path):
        o = json.loads(l)
        k = (o.get("src_id"), o.get("ts"))
        classified[k] = o["labels"]

    pairs = []
    for l in open(prose_path):
        o = json.loads(l)
        k = (o.get("session_id"), o.get("ts"))
        labels = classified.get(k)
        if not labels:
            continue
        pairs.append((o, labels))
    return pairs


def to_sft_example(prose, labels, max_input_chars=24000):
    """Build one SFT training example."""
    text = prose["text"]
    if len(text) > max_input_chars:
        text = text[:max_input_chars] + " ...[truncated]"
    prompt = RUBRIC_PROMPT.format(
        ROLE=prose.get("role", "assistant_prose"),
        FLEET=prose.get("fleet") or "?",
        PROJECT=prose.get("project") or "?",
        TEXT=text,
    )
    # Normalize labels to the exact 5 fields
    norm = {
        k: labels.get(k) for k in ["user_intent", "agent_intent", "agent_role", "blocker_type", "urgency"]
    }
    return {
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": json.dumps(norm, separators=(",", ":"))},
        ]
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prose")
    ap.add_argument("classified")
    ap.add_argument("out_train")
    ap.add_argument("--holdout_pct", type=int, default=10)
    ap.add_argument("--out_holdout", default="")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    pairs = build_pairs(args.prose, args.classified)
    print(f"[info] built {len(pairs)} pairs from {args.prose} + {args.classified}", file=sys.stderr)

    random.seed(args.seed)
    random.shuffle(pairs)
    holdout_n = max(1, len(pairs) * args.holdout_pct // 100)
    holdout = pairs[:holdout_n]
    train = pairs[holdout_n:]

    with open(args.out_train, "w") as fh:
        for p, l in train:
            fh.write(json.dumps(to_sft_example(p, l)) + "\n")
    print(f"[info] wrote train: {args.out_train} ({len(train)} examples)", file=sys.stderr)

    if args.out_holdout:
        with open(args.out_holdout, "w") as fh:
            for p, l in holdout:
                fh.write(json.dumps(to_sft_example(p, l)) + "\n")
        print(f"[info] wrote holdout: {args.out_holdout} ({len(holdout)} examples)", file=sys.stderr)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
