#!/usr/bin/env python3
"""v12-S: pre-compute per-bucket prose summaries via Nebius Qwen3-30B-A3B.

For every (session_id, hour_bucket_start) with >=min_msgs messages (default 1 =
any non-empty bucket), send a bounded summarization prompt and store 4 bullet
lists: started / finished / blocked / key_decisions. Output: data/bucket_summaries.jsonl.

Rule: default min-msgs is 1 so new fleets never ship with "no summary" holes.
Only bump threshold higher when running under a tight budget.

Budget: hard cap, aborts cleanly if exceeded.

Usage: summarize_buckets.py [--limit N] [--budget-cents 40] [--workers 10] [--model Qwen/Qwen3-30B-A3B-Instruct-2507] [--grain 3600]
"""
import os
import sys
import json
import time
import argparse
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

PROSE_DIR = Path("data/prose")
OUT_PATH = Path("data/bucket_summaries.jsonl")
API_URL = "https://api.studio.nebius.ai/v1/chat/completions"

DEFAULT_MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"
PRICING = {DEFAULT_MODEL: (0.10, 0.30)}  # per 1M input/output tokens
from summary_contract import (
    PROMPT, MAX_CONTEXT_CHARS, MAX_OUTPUT_TOKENS, SUMMARY_VERSION,
    build_body, parse_summary,
)


def enumerate_buckets(grain_sec, min_msgs=1):
    """Yield (sid, bucket_start, project, agent_title, provider, [(ts, role, text), ...]) sorted."""
    bags = defaultdict(list)
    sess_meta = {}
    for p in sorted(PROSE_DIR.glob("*.jsonl")):
        for line in open(p):
            try:
                o = json.loads(line)
            except Exception:
                continue
            sid = o.get("session_id")
            ts = float(o.get("ts") or 0)
            if not sid or not ts:
                continue
            hb = int(ts // grain_sec) * grain_sec
            text = o.get("text") or ""
            role = o.get("role") or ""
            if not text.strip():
                continue
            bags[(sid, hb)].append((ts, role, text))
            if sid not in sess_meta:
                sess_meta[sid] = {
                    "project": o.get("project") or "",
                    "agent_title": o.get("agent_title") or "",
                    "provider": o.get("provider") or "",
                    "model": o.get("model") or "",
                }
    for (sid, hb), msgs in bags.items():
        if len(msgs) < min_msgs:
            continue
        msgs.sort(key=lambda x: x[0])
        yield sid, hb, sess_meta[sid], msgs


def est_cost(model, in_tok, out_tok):
    pin, pout = PRICING.get(model, (0.10, 0.30))
    return in_tok / 1_000_000 * pin + out_tok / 1_000_000 * pout


def call_api(model, prompt, max_tokens=MAX_OUTPUT_TOKENS, timeout=90):
    key = os.environ["NEBIUS_TOKEN_FACTORY_KEY"]
    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(API_URL, data=data, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def summarize_one(bucket, model, grain_sec=3600):
    sid, hb, meta, msgs = bucket
    window = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(hb))
    body = build_body(msgs)
    prompt = PROMPT.format(
        AGENT=meta.get("agent_title") or "unknown",
        PROJECT=meta.get("project") or "unknown",
        WINDOW=window,
        BODY=body,
    )
    resp = call_api(model, prompt)
    if "error" in resp:
        return {"_error": resp["error"], "sid": sid, "hb": hb}
    usage = resp.get("usage")
    if not isinstance(usage, dict) or not all(k in usage for k in ("prompt_tokens", "completion_tokens")):
        return {"_error": "API response missing usage; retained full cost reservation", "sid": sid, "hb": hb}
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    cost = est_cost(model, in_tok, out_tok)
    reply = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    summary = parse_summary(reply)
    return {
        "sid": sid,
        "bucket_start": hb,
        "grain_sec": grain_sec,
        "summary_version": SUMMARY_VERSION,
        "source_fingerprint": source_fingerprint(msgs),
        "project": meta.get("project"),
        "agent_title": meta.get("agent_title"),
        "provider": meta.get("provider"),
        "msg_count": len(msgs),
        "summary": summary,
        "usage": {"in": in_tok, "out": out_tok, "cost_usd": cost},
    }


def source_fingerprint(msgs):
    return hashlib.sha256(json.dumps(msgs, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def needs_refresh(bucket, previous, grain_sec):
    if not previous:
        return True
    if (previous.get("summary") or {}).get("_parse_error") and previous.get("summary_version") != SUMMARY_VERSION:
        return True
    if previous.get("grain_sec", 3600) != grain_sec:
        return True
    if previous.get("summary_version", SUMMARY_VERSION) != SUMMARY_VERSION:
        return True
    if "source_fingerprint" in previous:
        return previous["source_fingerprint"] != source_fingerprint(bucket[3])
    # Existing rows predate fingerprints; avoid an unrequested full rebake.
    return previous.get("msg_count") != len(bucket[3])


def request_reservation(bucket, model):
    # UTF-8 bytes bound ordinary BPE input tokens; include prompt/metadata + framing.
    _, hb, meta, msgs = bucket
    prompt = PROMPT.format(AGENT=meta.get("agent_title") or "unknown",
                           PROJECT=meta.get("project") or "unknown",
                           WINDOW=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(hb)),
                           BODY=build_body(msgs))
    return est_cost(model, len(prompt.encode("utf-8")) + 4096, MAX_OUTPUT_TOKENS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget-cents", type=int, default=40)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--grain", type=int, default=3600, choices=[3600], help="hourly grain required by the timeline index")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--project", action="append", default=None, help="restrict to specific project(s); may repeat")
    ap.add_argument("--append", action="store_true", help="append new/stale/failed buckets; skip unchanged valid buckets")
    ap.add_argument("--min-msgs", type=int, default=1, help="min messages per bucket to qualify (default 1 = any non-empty bucket)")
    ap.add_argument("--refresh-existing", action="store_true", help="explicitly regenerate unchanged buckets too")
    args = ap.parse_args()
    if args.budget_cents < 0 or args.workers < 1 or args.grain < 1 or args.min_msgs < 1 or args.limit < 0:
        ap.error("budget/limit must be nonnegative; workers/grain/min-msgs must be positive")
    if args.model not in PRICING:
        ap.error("model needs an explicit PRICING entry before budget-capped use")
    budget = args.budget_cents / 100.0

    buckets = list(enumerate_buckets(args.grain, min_msgs=args.min_msgs))
    print(f"[info] {len(buckets)} qualifying buckets (grain={args.grain}s, >={args.min_msgs} msgs)", file=sys.stderr)
    if args.project:
        proj_set = set(args.project)
        buckets = [b for b in buckets if (b[2].get("project") or "") in proj_set]
        print(f"[info] project filter {sorted(proj_set)} → {len(buckets)} buckets", file=sys.stderr)
    if args.append and args.out.exists() and not args.refresh_existing:
        previous = {}
        for line in open(args.out):
            try:
                row = json.loads(line)
                previous[(row["sid"], int(row["bucket_start"]))] = row
            except (ValueError, KeyError):
                continue
        before = len(buckets)
        buckets = [b for b in buckets if needs_refresh(b, previous.get((b[0], b[1])), args.grain)]
        print(f"[info] --append skipped {before - len(buckets)} unchanged; {len(buckets)} new/stale/failed remain", file=sys.stderr)
    if args.limit:
        buckets = buckets[:args.limit]
        print(f"[info] limiting to {len(buckets)}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    state = {"cost": 0.0, "in": 0, "out": 0, "done": 0, "errors": 0, "parse_err": 0}
    start = time.time()
    mode = "a" if args.append else "w"
    pending = {}
    next_bucket = 0
    reserved = 0.0
    with open(args.out, mode) as fout, ThreadPoolExecutor(max_workers=args.workers) as pool:
        while pending or next_bucket < len(buckets):
            while len(pending) < args.workers and next_bucket < len(buckets) and state["errors"] <= 20:
                bucket = buckets[next_bucket]
                allowance = request_reservation(bucket, args.model)
                if state["cost"] + reserved + allowance > budget:
                    break
                fut = pool.submit(summarize_one, bucket, args.model, args.grain)
                pending[fut] = allowance
                reserved += allowance
                next_bucket += 1
            if not pending:
                if next_bucket < len(buckets):
                    print("[STOP] budget reservation or error limit; no more requests submitted", file=sys.stderr)
                break
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in completed:
                allowance = pending.pop(fut)
                reserved = max(0.0, reserved - allowance)
                try:
                    result = fut.result()
                except Exception as exc:
                    result = {"_error": str(exc)}
                if "_error" in result:
                    # Unknown usage may still have been billed. Keep its reservation spent.
                    state["cost"] += allowance
                    state["errors"] += 1
                    print(f"[error] {result['_error'][:100]}", file=sys.stderr)
                    continue
                state["cost"] += result["usage"]["cost_usd"]
                state["in"] += result["usage"]["in"]
                state["out"] += result["usage"]["out"]
                state["done"] += 1
                if result["summary"].get("_parse_error"):
                    state["parse_err"] += 1
                fout.write(json.dumps(result) + "\n")
                fout.flush()
    elapsed = time.time() - start
    print(f"\nDONE: {state['done']} rows, cost=${state['cost']:.4f}, in={state['in']} out={state['out']}, parse_err={state['parse_err']}, errors={state['errors']}, elapsed={elapsed:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
