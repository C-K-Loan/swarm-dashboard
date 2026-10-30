#!/usr/bin/env python3
"""Compare classifier output to hand-labeled truth.

Reads a truth JSONL (schema: {src_id, ts, truth: {<5 fields>}}) + each model's
hold-out classification JSONL, and prints per-field accuracy per model.

usage: eval_vs_truth.py <truth_jsonl> <holdout_dir>
"""
import json
import sys
from pathlib import Path


def _norm(v):
    return None if v in (None, "", "null", "none", "None") else v


def main():
    if len(sys.argv) < 3:
        print("usage: eval_vs_truth.py <truth_jsonl> <holdout_dir>", file=sys.stderr)
        sys.exit(1)
    truth_path = Path(sys.argv[1])
    hold_dir = Path(sys.argv[2])

    # Load truth
    truth = {}
    for l in open(truth_path):
        o = json.loads(l)
        key = (o["src_id"], round(float(o["ts"]), 2))
        truth[key] = o["truth"]
    print(f"truth: {len(truth)} labeled messages")

    # Each model's classifier output
    fields = ["user_intent", "agent_intent", "agent_role", "blocker_type", "urgency"]
    rows_per_model = {}
    for p in sorted(hold_dir.glob("classified__*.jsonl")):
        model = p.name.replace("classified__", "").replace(".jsonl", "").replace("__", "/")
        rows_per_model[model] = [json.loads(l) for l in p.open()]

    report = {"n_truth": len(truth), "models": {}}
    for model, rows in rows_per_model.items():
        counts = {f: {"total": 0, "correct": 0, "cls_null": 0, "truth_null": 0} for f in fields}
        for r in rows:
            key = (r.get("src_id"), round(float(r.get("ts", 0)), 2))
            if key not in truth:
                continue
            cls_labels = r.get("labels", {})
            t_labels = truth[key]
            for f in fields:
                c = _norm(cls_labels.get(f))
                t = _norm(t_labels.get(f))
                counts[f]["total"] += 1
                if c == t:
                    counts[f]["correct"] += 1
                if c is None:
                    counts[f]["cls_null"] += 1
                if t is None:
                    counts[f]["truth_null"] += 1
        report["models"][model] = {
            f: {
                "n": counts[f]["total"],
                "accuracy_pct": round(100 * counts[f]["correct"] / counts[f]["total"], 1) if counts[f]["total"] else None,
                "classifier_null_pct": round(100 * counts[f]["cls_null"] / counts[f]["total"], 1) if counts[f]["total"] else None,
                "truth_null_pct": round(100 * counts[f]["truth_null"] / counts[f]["total"], 1) if counts[f]["total"] else None,
            }
            for f in fields
        }
    print(json.dumps(report, indent=2))

    # Save
    out = hold_dir / "truth_eval.json"
    json.dump(report, open(out, "w"), indent=2)
    print(f"\nwrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
