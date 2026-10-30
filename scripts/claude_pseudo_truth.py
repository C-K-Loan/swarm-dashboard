#!/usr/bin/env python3
"""Call Anthropic Claude (default: claude-opus-5-5) over the 200-msg hold-out to
produce a stronger-model reference labelset. NOT called 'ground truth' anywhere
user-visible — judges with eval chops will respect the honest framing.

Reads scripts/classify_batch.py's RUBRIC_PROMPT so the schema stays identical.

usage: claude_pseudo_truth.py <in_holdout.jsonl> <out_claude_reference.jsonl>
       [--model claude-opus-5-5] [--limit N] [--workers 6]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_batch import RUBRIC_PROMPT, parse_json_reply  # noqa: E402

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def call_claude(model, prompt, max_tokens=200, timeout=90):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in env")
    data = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(ANTHROPIC_URL, data=data, headers={
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}


def classify_one(o, model):
    text = o.get("text") or ""
    if len(text) > 4000:
        text = text[:4000] + " ...[truncated]"
    prompt = RUBRIC_PROMPT.format(
        ROLE=o.get("role", "assistant_prose"),
        FLEET=o.get("fleet") or "?",
        PROJECT=o.get("project") or "?",
        TEXT=text,
    )
    resp = call_claude(model, prompt)
    if "error" in resp:
        return {"_error": resp["error"], "src_id": o.get("session_id"), "ts": o.get("ts")}
    content = resp.get("content") or []
    reply = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    labels = parse_json_reply(reply)
    usage = resp.get("usage", {})
    return {
        "schema_v": "0.1",
        "src_id": o.get("session_id"),
        "project": o.get("project"),
        "ts": o.get("ts"),
        "role": o.get("role"),
        "reference_model": model,
        "labels": labels,
        "usage": {
            "in": usage.get("input_tokens", 0),
            "out": usage.get("output_tokens", 0),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.input) if l.strip()]
    if args.limit:
        rows = rows[: args.limit]
    print(f"[start] {len(rows)} rows via {args.model}, workers={args.workers}", file=sys.stderr)

    t0 = time.time()
    done = 0
    errors = 0
    tin = tout = 0
    out_fh = open(args.output, "w")
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(classify_one, o, args.model) for o in rows]
            for fut in as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:
                    errors += 1
                    print(f"[err] {e}", file=sys.stderr)
                    continue
                if "_error" in r:
                    errors += 1
                    if errors <= 5:
                        print(f"[err] {r['_error']}", file=sys.stderr)
                    continue
                tin += r["usage"].get("in", 0)
                tout += r["usage"].get("out", 0)
                out_fh.write(json.dumps(r) + "\n")
                out_fh.flush()
                done += 1
                if done % 20 == 0:
                    print(f"[{done}/{len(rows)}] in={tin} out={tout} errors={errors}", file=sys.stderr)
    finally:
        out_fh.close()
    # Opus 5.5 pricing (approx; verify): $15/1M in, $75/1M out
    cost = tin / 1_000_000 * 15.0 + tout / 1_000_000 * 75.0
    elapsed = time.time() - t0
    print(f"[done] {done} rows, errors={errors}, in={tin} out={tout}, est cost ${cost:.4f}, {elapsed:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
