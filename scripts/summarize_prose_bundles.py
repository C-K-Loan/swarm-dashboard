#!/usr/bin/env python3
"""Run a Nebius chat model as a bucket summarizer against prose_bundles.

Reuses the same PROMPT / body-building logic as scripts/summarize_buckets.py
so results are directly comparable with the existing bucket_summaries.jsonl.

For each item in data/eval/prose_bundles/bundle_NN.json:
  - build body from item['msgs']
  - call the chosen model
  - parse the started/finished/blocked/key_decisions JSON

Output: data/eval/prose_candidates/<slug>.jsonl
  Rows aligned to prose_bundles ordering (bundle_01 → _20, items 0..9).
  Each row: {bundle_id, item_i, sid, bucket_start, project, fleet, msg_count,
             summary: {started, finished, blocked, key_decisions},
             usage: {in, out, cost_usd}, model, _api_error?, _parse_error?}
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(".")
BUNDLES_DIR = ROOT / "data/eval/prose_bundles"
OUT_DIR = ROOT / "data/eval/prose_candidates"

API_URL = "https://api.studio.nebius.ai/v1/chat/completions"
MAX_CONTEXT_CHARS = 6000

PRICING = {
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30),
    "google/gemma-3-27b-it": (0.10, 0.30),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "Qwen/Qwen3-235B-A22B-Instruct-2507": (0.20, 0.60),
    "deepseek-ai/DeepSeek-V4-Flash-0731": (0.10, 0.40),
}
MODEL_SLUGS = {
    "Qwen/Qwen3-30B-A3B-Instruct-2507": "qwen_30b",
    "google/gemma-3-27b-it": "gemma_27b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": "nemotron_30b",
    "Qwen/Qwen3-235B-A22B-Instruct-2507": "qwen_235b",
    "deepseek-ai/DeepSeek-V4-Flash-0731": "deepseek_flash",
}

from summary_contract import (
    PROMPT as LIVE_PROMPT, build_body as live_build_body,
    parse_summary, MAX_OUTPUT_TOKENS, SUMMARY_VERSION,
)
PROMPT = (LIVE_PROMPT.replace("{AGENT}", "@@AGENT@@")
          .replace("{PROJECT}", "@@PROJECT@@").replace("{WINDOW}", "@@WINDOW@@")
          .replace("{BODY}", "@@BODY@@").replace("{{", "{").replace("}}", "}"))


def build_body(msgs, max_chars=MAX_CONTEXT_CHARS):
    return live_build_body([(float(m.get("ts", 0)), m.get("role", ""), m.get("text", ""))
                            for m in msgs], max_chars)


def _parse(text):
    summary = parse_summary(text)
    failed = bool(summary.get("_parse_error"))
    return ({} if failed else summary), failed, summary.get("raw", "")


def _call(model: str, prompt: str, timeout: int = 90) -> dict:
    key = os.environ["NEBIUS_TOKEN_FACTORY_KEY"]
    low = model.lower()
    if any(x in low for x in ("nemotron", "qwen3.8", "gpt-oss", "kimi")):
        max_tok = 1200
    elif "deepseek" in low:
        max_tok = 900
    else:
        max_tok = MAX_OUTPUT_TOKENS
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tok,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def summarize_one(item: dict, model: str) -> dict:
    body = build_body(item["msgs"])
    hb = item["bucket_start"]
    window = f"{hb}..{hb + item['grain_sec']}"
    prompt = (PROMPT
        .replace("@@AGENT@@", item["agent_title"] or "?")
        .replace("@@PROJECT@@", item["project"] or "?")
        .replace("@@WINDOW@@", window)
        .replace("@@BODY@@", body))
    t0 = time.time()
    resp = _call(model, prompt)
    latency = time.time() - t0
    if "error" in resp:
        return {"_api_error": resp["error"], "latency_s": latency, "in_tok": 0, "out_tok": 0, "summary": None}
    usage = resp.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    summary, parse_err, raw = _parse(content)
    pin, pout = PRICING.get(model, (0.10, 0.30))
    cost = in_tok / 1_000_000 * pin + out_tok / 1_000_000 * pout
    out = {
        "summary": summary,
        "in_tok": in_tok,
        "out_tok": out_tok,
        "cost_usd": cost,
        "latency_s": round(latency, 2),
    }
    if parse_err:
        out["_parse_error"] = True
        out["_raw"] = raw
    return out


def load_env():
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k, v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--budget-cents", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    load_env()

    slug = args.slug or MODEL_SLUGS.get(args.model) or args.model.split("/")[-1].lower()
    budget = args.budget_cents / 100.0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{slug}.jsonl"

    # Load all bundles in order
    bundles = sorted(BUNDLES_DIR.glob("bundle_*.json"))
    all_items = []
    for bp in bundles:
        d = json.loads(bp.read_text())
        for i, it in enumerate(d["items"]):
            it["_bundle_id"] = d["bundle_id"]
            it["_item_i"] = i
            all_items.append(it)
    print(f"[START] model={args.model} slug={slug} items={len(all_items)} budget=${budget:.2f}", file=sys.stderr)

    state = {"cost": 0.0, "done": 0, "errs": 0}
    lock = threading.Lock()
    start = time.time()
    results = [None] * len(all_items)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(summarize_one, it, args.model): i for i, it in enumerate(all_items)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"_api_error": str(e), "summary": None, "in_tok": 0, "out_tok": 0, "cost_usd": 0, "latency_s": 0}
            results[i] = r
            with lock:
                state["done"] += 1
                state["cost"] += r.get("cost_usd", 0) or 0
                if r.get("_api_error") or r.get("_parse_error"):
                    state["errs"] += 1
                if state["done"] % 20 == 0:
                    elapsed = time.time() - start
                    print(f"[{state['done']}/{len(all_items)}] cost=${state['cost']:.4f} errs={state['errs']} elapsed={elapsed:.0f}s", file=sys.stderr)
                if state["cost"] > budget:
                    print(f"[BUDGET STOP] cost=${state['cost']:.4f}", file=sys.stderr)
                    for f in futs:
                        f.cancel()

    with out_path.open("w") as fout:
        for it, r in zip(all_items, results):
            if r is None:
                r = {"_api_error": "cancelled", "summary": None}
            row = {
                "bundle_id": it["_bundle_id"],
                "item_i": it["_item_i"],
                "sid": it["sid"],
                "bucket_start": it["bucket_start"],
                "project": it["project"],
                "fleet": it["fleet"],
                "msg_count": it["msg_count"],
                "model": args.model,
                "summary_version": SUMMARY_VERSION,
                "slug": slug,
                "summary": r.get("summary"),
                "usage": {"in": r.get("in_tok", 0), "out": r.get("out_tok", 0), "cost_usd": r.get("cost_usd", 0)},
                "latency_s": r.get("latency_s", 0),
            }
            if r.get("_api_error"):
                row["_api_error"] = r["_api_error"]
            if r.get("_parse_error"):
                row["_parse_error"] = True
                row["_raw"] = r.get("_raw", "")
            fout.write(json.dumps(row) + "\n")

    print(f"\nDONE: {state['done']}/{len(all_items)} rows at {out_path}  cost=${state['cost']:.4f}  errs={state['errs']}", file=sys.stderr)


if __name__ == "__main__":
    main()
