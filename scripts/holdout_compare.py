#!/usr/bin/env python3
"""Hold-out comparison: classify same 200 msgs across multiple models, record
cost + latency + label agreement for the deck.

usage: holdout_compare.py <prose_jsonl> <sample_size> <out_dir> --models M1 M2 M3
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import urllib.request, urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_batch import classify_one, est_cost


def run_model(model, sample, out_path):
    print(f"[{model}] starting {len(sample)} msgs...", file=sys.stderr)
    out_fh = open(out_path, "w")
    lock = threading.Lock()
    state = {"cost": 0.0, "in": 0, "out": 0, "done": 0, "err": 0, "total_latency": 0.0}
    start = time.time()
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {}
        for o in sample:
            t0 = time.time()
            fut = pool.submit(classify_one, o, model)
            futs[fut] = (o.get("session_id") or o.get("src_id"), t0)
        for fut in as_completed(futs):
            src_id, t0 = futs[fut]
            lat = time.time() - t0
            try:
                r = fut.result()
            except Exception as e:
                state["err"] += 1
                continue
            if "_error" in r:
                state["err"] += 1
                continue
            with lock:
                state["cost"] += r["usage"]["cost_usd"]
                state["in"] += r["usage"]["in"]
                state["out"] += r["usage"]["out"]
                state["done"] += 1
                state["total_latency"] += lat
                r["_latency_s"] = round(lat, 3)
                out_fh.write(json.dumps(r) + "\n")
    out_fh.close()
    elapsed = time.time() - start
    avg_lat = state["total_latency"] / state["done"] if state["done"] else 0
    print(
        f"[{model}] done {state['done']}/{len(sample)} "
        f"cost=${state['cost']:.4f} avg_lat={avg_lat:.2f}s elapsed={elapsed:.0f}s err={state['err']}",
        file=sys.stderr,
    )
    return {
        "model": model,
        "n": state["done"],
        "cost_usd": round(state["cost"], 6),
        "in_tok": state["in"],
        "out_tok": state["out"],
        "avg_latency_s": round(avg_lat, 3),
        "errors": state["err"],
        "elapsed_s": round(elapsed, 1),
    }


def compute_agreement(all_results):
    """For each model pair, compute what fraction of labels agree per field.

    Joins on (src_id, ts) so each row is a unique prose turn, not a session.
    """
    by_src = {}
    for model, rows in all_results.items():
        for r in rows:
            s = r.get("src_id") or r.get("session_id")
            ts = r.get("ts")
            if not s or ts is None:
                continue
            key = (s, round(float(ts), 2))
            by_src.setdefault(key, {})[model] = r.get("labels", {})
    models = sorted(all_results.keys())
    agreement = {}
    fields = ["user_intent", "agent_intent", "agent_role", "blocker_type", "urgency"]
    def _norm(v):
        return None if v in (None, "", "null", "none", "None") else v

    for i, m1 in enumerate(models):
        for m2 in models[i + 1 :]:
            counts = {f: {"total": 0, "agree": 0} for f in fields}
            for s, mdict in by_src.items():
                if m1 in mdict and m2 in mdict:
                    for f in fields:
                        counts[f]["total"] += 1
                        if _norm(mdict[m1].get(f)) == _norm(mdict[m2].get(f)):
                            counts[f]["agree"] += 1
            key = f"{m1} vs {m2}"
            agreement[key] = {
                f: {
                    "agreement_pct": round(100 * c["agree"] / c["total"], 1) if c["total"] else 0,
                    "n": c["total"],
                }
                for f, c in counts.items()
            }
    return agreement


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prose")
    ap.add_argument("sample_size", type=int)
    ap.add_argument("out_dir")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Load prose + sample
    all_rows = [json.loads(l) for l in open(args.prose)]
    random.seed(args.seed)
    sample = random.sample(all_rows, min(args.sample_size, len(all_rows)))
    print(f"sampling {len(sample)} from {len(all_rows)}", file=sys.stderr)
    sample_path = out / "holdout_sample.jsonl"
    with open(sample_path, "w") as fh:
        for o in sample:
            fh.write(json.dumps(o) + "\n")

    # Run each model
    summaries = []
    all_results = {}
    for model in args.models:
        safe = model.replace("/", "__").replace(":", "_")
        out_path = out / f"classified__{safe}.jsonl"
        summary = run_model(model, sample, out_path)
        summaries.append(summary)
        all_results[model] = [json.loads(l) for l in open(out_path)]

    # Agreement matrix
    agreement = compute_agreement(all_results)

    report = {
        "sample_size": len(sample),
        "models": summaries,
        "agreement": agreement,
    }
    report_path = out / "report.json"
    json.dump(report, open(report_path, "w"), indent=2)
    print(f"\nreport: {report_path}", file=sys.stderr)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
