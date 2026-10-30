#!/usr/bin/env python3
"""Compute per-tier agreement vs Claude pseudo-truth reference, per field.

Reads data/classified/*.jsonl (one file per model) + data/holdout/claude_reference.jsonl.
Joins on (src_id, round(ts, 2)) — same key convention as holdout_compare.py.

Output: data/holdout/vs_reference.json with per-tier {field: pct_agree} + N.
"""
import glob
import json
import sys
from pathlib import Path
from collections import defaultdict

FIELDS = ["user_intent", "agent_intent", "agent_role", "blocker_type", "urgency"]


def _nn(v):
    return None if v in (None, "", "null", "none", "None") else v


def load_classified_by_model(classified_dir):
    """Returns {model_slug: {(src_id, ts_round): labels}}."""
    by_model = defaultdict(dict)
    for f in sorted(glob.glob(str(Path(classified_dir) / "*.jsonl"))):
        for line in open(f):
            try:
                o = json.loads(line)
            except Exception:
                continue
            model = o.get("classifier_model") or Path(f).stem.split("__", 1)[-1]
            key = (o.get("src_id"), round(float(o.get("ts", 0)), 2))
            if key[0] is None:
                continue
            by_model[model][key] = o.get("labels") or {}
    return by_model


def load_reference(ref_path):
    out = {}
    if not Path(ref_path).exists():
        return out
    for line in open(ref_path):
        try:
            o = json.loads(line)
        except Exception:
            continue
        key = (o.get("src_id"), round(float(o.get("ts", 0)), 2))
        if key[0] is None:
            continue
        out[key] = o.get("labels") or {}
    return out


def agreement(tier_labels, ref_labels):
    joined = set(tier_labels.keys()) & set(ref_labels.keys())
    stats = {"n_joined": len(joined), "per_field": {}}
    for field in FIELDS:
        hits = 0
        total = 0
        for k in joined:
            a = _nn(tier_labels[k].get(field))
            b = _nn(ref_labels[k].get(field))
            total += 1
            if a == b:
                hits += 1
        stats["per_field"][field] = {
            "n": total,
            "match": hits,
            "pct": round(hits / total * 100, 1) if total else None,
        }
    return stats


def main():
    classified_dir = sys.argv[1] if len(sys.argv) > 1 else "data/classified"
    ref_path = sys.argv[2] if len(sys.argv) > 2 else "data/holdout/claude_reference.jsonl"
    out_path = sys.argv[3] if len(sys.argv) > 3 else "data/holdout/vs_reference.json"

    ref = load_reference(ref_path)
    if not ref:
        print(f"[err] no reference labels at {ref_path}", file=sys.stderr)
        sys.exit(1)
    print(f"[info] reference: {len(ref)} rows", file=sys.stderr)

    by_model = load_classified_by_model(classified_dir)
    print(f"[info] classifier models seen: {sorted(by_model.keys())}", file=sys.stderr)

    results = {}
    for model, labels in by_model.items():
        results[model] = agreement(labels, ref)

    report = {
        "reference_n": len(ref),
        "fields": FIELDS,
        "by_classifier": results,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    json.dump(report, open(out_path, "w"), indent=2)
    print(f"[done] wrote {out_path}", file=sys.stderr)
    # Print summary
    for model, r in results.items():
        line = ", ".join(f"{k}={r['per_field'][k]['pct']}%" for k in FIELDS if r['per_field'][k]['pct'] is not None)
        print(f"  {model[:60]:<60}  n={r['n_joined']}  {line}", file=sys.stderr)


if __name__ == "__main__":
    main()
