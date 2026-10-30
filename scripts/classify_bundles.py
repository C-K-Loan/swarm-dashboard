#!/usr/bin/env python3
"""Classify every message in gold eval bundles using a Nebius chat model.

Reuses the single-message v0.2 rubric prompt from scripts/format_sft_pairs.py
(so training-prompt == inference-prompt == classifier-prompt).

Input:
  data/eval/bundles/agent_01..agent_16.txt (800 agent msgs)
  data/eval/bundles/user_01..user_04.txt   (200 user msgs)
  (same 1000-msg set gold_v2.jsonl was built from)

Output:
  data/eval/ref_<SLUG>/<bundle_name>.jsonl   — per-bundle, 50 rows each
  data/eval/ref_<SLUG>.jsonl                 — flattened 1000 rows, aligned to gold_v2.jsonl order

Hard-budget: if the running cost exceeds --budget-cents cents, abort.
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

# Reuse prompts + parsers from format_sft_pairs
sys.path.insert(0, str(Path(__file__).resolve().parent))
from format_sft_pairs import AGENT_PROMPT, USER_PROMPT, AGENT_FIELDS, USER_FIELDS, parse_bundle  # noqa: E402

ROOT = Path(".")
BUNDLES = ROOT / "data/eval/bundles"
EVAL = ROOT / "data/eval"

API_URL = "https://api.studio.nebius.ai/v1/chat/completions"
API_URL_TF = "https://api.tokenfactory.nebius.com/v1/chat/completions"

# Pricing (USD per 1M tokens, in/out). From classify_batch.py; refine as needed.
PRICING = {
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30),
    "google/gemma-3-27b-it": (0.10, 0.30),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "Qwen/Qwen3-14B": (0.08, 0.24),
}
# Short slug written into filenames + used as _labeler_model tag
MODEL_SLUGS = {
    "Qwen/Qwen3-30B-A3B-Instruct-2507": "qwen_30b",
    "google/gemma-3-27b-it": "gemma_27b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": "nemotron_30b",
    "Qwen/Qwen3-14B": "qwen14b_base",
}


def _render_prompt(msg: dict, is_user: bool) -> str:
    tpl = USER_PROMPT if is_user else AGENT_PROMPT
    return (tpl
        .replace("@@SID@@", msg["sid"])
        .replace("@@FLEET@@", msg["fleet"])
        .replace("@@PROJECT@@", msg["project"])
        .replace("@@AGENT@@", msg["agent"])
        .replace("@@ROLE@@", msg["role"])
        .replace("@@TS@@", msg["ts"])
        .replace("@@TEXT@@", msg["text"]))


def _call(model: str, prompt: str, timeout: int = 90) -> dict:
    key = os.environ["NEBIUS_TOKEN_FACTORY_KEY"]
    api_url = os.environ.get("CLASSIFY_API_URL", API_URL)
    # Reasoning / thinking models burn hundreds of tokens on inner-monologue
    # before emitting the JSON answer. Give them wide headroom.
    low = model.lower()
    if any(x in low for x in ("nemotron", "qwen3.8", "qwen3-reasoning", "gpt-oss", "kimi")):
        max_tok = 1500
    elif "deepseek" in low:
        max_tok = 1000  # DeepSeek V4 uses ~450 reasoning + ~100 answer
    else:
        max_tok = 180
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tok,
        "temperature": 0.0,
    }).encode()
    req = urllib.request.Request(
        api_url,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse(text, fields: tuple[str, ...]) -> dict:
    if text is None or not isinstance(text, str):
        return {k: None for k in fields} | {"_parse_error": True, "_raw": repr(text)[:200]}
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    m = _JSON_RE.search(s)
    if m:
        s = m.group(0)
    try:
        obj = json.loads(s)
    except Exception:
        return {k: None for k in fields} | {"_parse_error": True, "_raw": text[:200]}

    def norm(v):
        if isinstance(v, str) and v.strip().lower() in ("null", "none", "n/a", ""):
            return None
        if isinstance(v, list):
            return [norm(x) for x in v if norm(x) is not None]
        return v
    return {k: norm(obj.get(k)) for k in fields}


def _classify_one(msg: dict, is_user: bool, model: str, fields: tuple[str, ...]) -> dict:
    prompt = _render_prompt(msg, is_user)
    resp = _call(model, prompt)
    if "error" in resp:
        return {"_api_error": resp["error"], **{k: None for k in fields}}
    usage = resp.get("usage", {})
    content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    labels = _parse(content, fields)
    labels["_in_tok"] = usage.get("prompt_tokens", 0)
    labels["_out_tok"] = usage.get("completion_tokens", 0)
    return labels


def classify_bundle(bundle_path: Path, model: str, slug: str, out_path: Path,
                    budget_usd: float, state: dict, lock: threading.Lock,
                    workers: int = 8) -> list[dict]:
    msgs = parse_bundle(bundle_path)
    is_user = bundle_path.stem.startswith("user_") or bundle_path.stem.startswith("train_user_")
    fields = USER_FIELDS if is_user else AGENT_FIELDS
    bundle_name = bundle_path.stem
    pin, pout = PRICING.get(model, (0.30, 0.60))

    results: list[dict | None] = [None] * len(msgs)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_classify_one, m, is_user, model, fields): i for i, m in enumerate(msgs)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"_api_error": str(e), **{k: None for k in fields}}
            results[i] = r
            with lock:
                state["in"] += r.get("_in_tok", 0) or 0
                state["out"] += r.get("_out_tok", 0) or 0
                state["cost"] = state["in"] / 1_000_000 * pin + state["out"] / 1_000_000 * pout
                if state["cost"] > budget_usd:
                    print(f"[BUDGET STOP] cost=${state['cost']:.4f} > ${budget_usd:.4f}", file=sys.stderr)
                    for pending in futures:
                        pending.cancel()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out_path.open("w") as f:
        for r in results:
            if r is None:
                r = {k: None for k in fields}
            keep = {k: r.get(k) for k in fields}
            keep["_bundle"] = bundle_name
            keep["_labeler_model"] = slug
            keep["_annotator_kind"] = "LLM"
            keep["_in_tok"] = r.get("_in_tok", 0) or 0
            keep["_out_tok"] = r.get("_out_tok", 0) or 0
            if r.get("_api_error"):
                keep["_api_error"] = r["_api_error"]
            if r.get("_parse_error"):
                keep["_parse_error"] = True
            f.write(json.dumps(keep) + "\n")
            rows.append(keep)
    return rows


def gold_bundle_order() -> list[str]:
    """Return gold bundle names in the order gold_v2.jsonl rows reference them."""
    seen = []
    seen_set = set()
    for l in (EVAL / "gold_v2.jsonl").open():
        b = json.loads(l).get("_bundle")
        if b and b not in seen_set:
            seen.append(b)
            seen_set.add(b)
    return seen


def flatten(slug: str, order: list[str]) -> int:
    out = EVAL / f"ref_{slug}.jsonl"
    n = 0
    with out.open("w") as fout:
        for bname in order:
            path = EVAL / f"ref_{slug}" / f"{bname}.jsonl"
            if not path.exists():
                print(f"[flatten] missing {path}", file=sys.stderr)
                continue
            for l in path.open():
                fout.write(l)
                n += 1
    print(f"[flatten] {out}  n={n}", file=sys.stderr)
    return n


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
    ap.add_argument("--model", required=True, help="Nebius model id (e.g. Qwen/Qwen3-30B-A3B-Instruct-2507)")
    ap.add_argument("--slug", default=None, help="override slug (filename key)")
    ap.add_argument("--budget-cents", type=int, default=20)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    load_env()
    if not os.environ.get("NEBIUS_TOKEN_FACTORY_KEY"):
        print("[ERR] NEBIUS_TOKEN_FACTORY_KEY missing", file=sys.stderr)
        sys.exit(2)

    slug = args.slug or MODEL_SLUGS.get(args.model) or args.model.split("/")[-1].lower()
    budget = args.budget_cents / 100.0
    order = gold_bundle_order()
    print(f"[START] model={args.model} slug={slug} bundles={len(order)} budget=${budget:.2f}", file=sys.stderr)

    state = {"in": 0, "out": 0, "cost": 0.0}
    lock = threading.Lock()
    start = time.time()
    for i, bname in enumerate(order, 1):
        bundle_path = BUNDLES / f"{bname}.txt"
        out_path = EVAL / f"ref_{slug}" / f"{bname}.jsonl"
        if out_path.exists():
            print(f"[skip] {bname} (already exists, delete to rerun)", file=sys.stderr)
            continue
        print(f"[{i}/{len(order)}] {bname} …", file=sys.stderr)
        rows = classify_bundle(bundle_path, args.model, slug, out_path, budget, state, lock, args.workers)
        errs = sum(1 for r in rows if r.get("_api_error") or r.get("_parse_error"))
        print(f"           {len(rows)} rows ({errs} errs)  cost=${state['cost']:.4f}  elapsed={time.time()-start:.0f}s", file=sys.stderr)
        if state["cost"] > budget:
            print(f"[STOP] budget exceeded", file=sys.stderr)
            break

    total = flatten(slug, order)
    print(f"\nDONE: {total} rows at data/eval/ref_{slug}.jsonl  cost=${state['cost']:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
