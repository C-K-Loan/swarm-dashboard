#!/usr/bin/env python3
"""Classify prose messages via Nebius Token Factory.

Rubric schema v0.1:
  user_intent, agent_intent, agent_role, blocker_type, urgency

Usage:
  classify_batch.py <input.jsonl> <output.jsonl> --model <model_id> [--limit N] [--budget-cents 300]
"""
import os, sys, json, argparse, time, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request, urllib.error
import threading

API_URL = "https://api.studio.nebius.ai/v1/chat/completions"
SCHEMA_VERSION = "0.1"

# Rough per-1M pricing estimates. REFINE from Nebius docs if available.
PRICING_USD_PER_1M = {
    # (input, output) — confirmed from amcompute aggregator Oct 9 2026
    "nvidia/Nemotron-3_5-Lightning": (0.04, 0.10),  # est, not in aggregator
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),  # confirmed
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30),  # confirmed
    "Qwen/Qwen3.8-27B": (0.20, 0.60),  # est (reasoning model, higher)
    "google/gemma-3-27b-it": (0.10, 0.30),  # confirmed
    "Qwen/Qwen3-235B-A22B-Instruct-2507": (0.20, 0.60),  # confirmed
    "deepseek-ai/DeepSeek-V4.1-Flash": (0.10, 0.40),  # est
    "nvidia/nemotron-3-super-120b-a12b": (0.60, 1.80),  # est
    "openai/gpt-oss-120b": (0.15, 0.60),  # confirmed
}

RUBRIC_PROMPT = """You classify a single AI agent prose message for fleet observability.
Schema version: 0.1
Return ONLY compact JSON on one line. No explanation.

Fields (all optional, use null when unclear):
- user_intent: feature-def | feature-update | unblock-context | unblock-infra | status-check | praise | frustration | null
- agent_intent: report-progress | report-blocked | ask-clarification | handoff | celebration | summary | null
- agent_role: worker | dev | manager | reviewer | planner | null
- blocker_type: waiting-human | rate-limit | tool-failure | retry-loop | context-exhausted | scope-ambiguous | null
- urgency: low | mid | high | null

Role of this message: {ROLE}
Agent fleet: {FLEET}
Project: {PROJECT}

Message:
---
{TEXT}
---

Reply only with one-line JSON like {{"user_intent":null,"agent_intent":"report-progress","agent_role":"dev","blocker_type":null,"urgency":"low"}}
"""


def est_cost(model, in_tok, out_tok):
    pin, pout = PRICING_USD_PER_1M.get(model, (0.30, 0.60))
    return in_tok / 1_000_000 * pin + out_tok / 1_000_000 * pout


def call_api(model, prompt, max_tokens=100, timeout=60, max_retries=10):
    """Call Nebius with exponential backoff on 429/5xx. Returns parsed JSON or {"error": ...}."""
    key = os.environ["NEBIUS_TOKEN_FACTORY_KEY"]
    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }).encode()
    delay = 1.0
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(API_URL, data=data, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            # Retry on 429 (rate limit) and 5xx (server) with exponential backoff + jitter
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                import random
                sleep_s = delay + random.random() * delay
                time.sleep(sleep_s)
                delay = min(delay * 2, 30)
                continue
            return {"error": f"HTTP {e.code}: {body}", "_transient": e.code in (429,)}
        except Exception as e:
            if attempt < max_retries:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            return {"error": str(e)}
    return {"error": "max_retries exhausted"}


def parse_json_reply(text):
    """Extract JSON from reply, tolerant of surrounding whitespace or code fences."""
    if text is None:
        return {"_parse_error": True, "raw": None}
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    # Find the first { ... } span
    m = re.search(r"\{[^{}]*\}", s)
    if m:
        s = m.group(0)
    try:
        return json.loads(s)
    except Exception:
        return {"_parse_error": True, "raw": text[:200]}


def classify_one(o, model, max_tokens=None):
    text = o["text"]
    if len(text) > 4000:
        text = text[:4000] + " ...[truncated]"
    prompt = RUBRIC_PROMPT.format(
        ROLE=o.get("role", "assistant_prose"),
        FLEET=o.get("fleet") or "?",
        PROJECT=o.get("project") or "?",
        TEXT=text,
    )
    # Reasoning-style models need more tokens to produce non-empty content
    if max_tokens is None:
        if any(x in model.lower() for x in ["qwen3.8", "nemotron-3", "gpt-oss", "kimi"]):
            max_tokens = 500
        else:
            max_tokens = 120
    resp = call_api(model, prompt, max_tokens=max_tokens)
    if "error" in resp:
        return {"_error": resp["error"], "src": o}
    usage = resp.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    cost = est_cost(model, in_tok, out_tok)
    reply_text = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    labels = parse_json_reply(reply_text)
    return {
        "schema_v": SCHEMA_VERSION,
        "src_id": o.get("session_id"),
        "project": o.get("project"),
        "fleet": o.get("fleet"),
        "provider": o.get("provider"),
        "model": o.get("model"),
        "ts": o.get("ts"),
        "role": o.get("role"),
        "agent_title": o.get("agent_title"),
        "classifier_model": model,
        "labels": labels,
        "usage": {"in": in_tok, "out": out_tok, "cost_usd": round(cost, 6)},
    }


def classify_file(in_path, out_path, model, limit, budget_usd, workers=12):
    """Parallel classification with ThreadPool. Budget-aware: stops when limit reached."""
    rows = []
    with open(in_path) as fin:
        for i, line in enumerate(fin):
            if limit and i >= limit:
                break
            rows.append(json.loads(line))
    print(f"[START] {len(rows)} rows, workers={workers}, budget=${budget_usd:.2f}, model={model}", file=sys.stderr)

    lock = threading.Lock()
    state = {"cost": 0.0, "in": 0, "out": 0, "done": 0, "errors": 0, "stop": False}
    start = time.time()
    with open(out_path, "w") as fout, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(classify_one, o, model): i for i, o in enumerate(rows)}
        for fut in as_completed(futures):
            if state["stop"]:
                continue
            try:
                result = fut.result()
            except Exception as e:
                state["errors"] += 1
                print(f"[fut-err] {e}", file=sys.stderr)
                continue
            if "_error" in result:
                state["errors"] += 1
                print(f"[cls-err] {result['_error'][:100]}", file=sys.stderr)
                # Abort only on persistent non-transient errors; retries already covered 429s.
                if state["errors"] > 50:
                    print(f"[ABORT] 50+ errors", file=sys.stderr)
                    state["stop"] = True
                continue
            with lock:
                state["cost"] += result["usage"]["cost_usd"]
                state["in"] += result["usage"]["in"]
                state["out"] += result["usage"]["out"]
                state["done"] += 1
                fout.write(json.dumps(result) + "\n")
                fout.flush()
                if state["cost"] >= budget_usd:
                    print(f"[BUDGET STOP] cost=${state['cost']:.4f}", file=sys.stderr)
                    state["stop"] = True
                if state["done"] % 100 == 0:
                    elapsed = time.time() - start
                    rate = state["done"] / elapsed
                    eta = (len(rows) - state["done"]) / rate if rate else 0
                    print(f"[{state['done']}/{len(rows)}] cost=${state['cost']:.4f}  rate={rate:.1f}/s  ETA={eta:.0f}s  errors={state['errors']}", file=sys.stderr)
    elapsed = time.time() - start
    print(f"\nDONE: {state['done']} rows, cost=${state['cost']:.4f}, in={state['in']} out={state['out']}, errors={state['errors']}, elapsed={elapsed:.0f}s", file=sys.stderr)
    return state["cost"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--model", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget-cents", type=int, default=50)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    classify_file(args.input, args.output, args.model, args.limit or None, args.budget_cents / 100.0, args.workers)
