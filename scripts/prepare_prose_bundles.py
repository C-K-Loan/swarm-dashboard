#!/usr/bin/env python3
"""Stratified-sample 200 bucket-summary rows for the prose eval track.

Enumerates (sid, bucket_start) buckets the same way summarize_buckets.py does
(reads data/prose/*.jsonl, groups by hour, drops buckets < 3 msgs). Keeps only
buckets that ALSO exist in data/bucket_summaries.jsonl (so the existing Qwen3-30B
summary is available as a reference).

Stratified across (fleet, msg_count bucket): small (<10), mid (10-30), big (30+).
Round-robin picks across stratum keys. Writes 20 bundle files of 10 buckets each.

Output: data/eval/prose_bundles/bundle_NN.json
  Each file: {"schema":"v0.2-prose","bundle_id":"bundle_NN", "items":[{...}, ...]}
  Each item:
    { "sid": str,
      "bucket_start": int,
      "grain_sec": int,
      "project": str,
      "agent_title": str,
      "provider": str,
      "msg_count": int,
      "msgs": [{"ts": float, "role": str, "text": str}, ...],   # raw prose
      "qwen30_summary": {"started":[], "finished":[], "blocked":[], "key_decisions":[]}
    }
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(".")
PROSE_DIR = ROOT / "data/prose"
BUCKETS_PATH = ROOT / "data/bucket_summaries.jsonl"
OUT_DIR = ROOT / "data/eval/prose_bundles"

N_TARGET = 200
BUNDLE_SIZE = 10
GRAIN_SEC = 3600  # match summarize_buckets.py default
MAX_MSG_CHARS = 1500  # per-msg truncation to keep bundles sane
SEED = 42


def mc_bucket(n: int) -> str:
    if n < 10:
        return "small"
    if n < 30:
        return "mid"
    return "big"


def load_bucket_summaries() -> dict:
    """Map (sid, bucket_start) → bucket_summary row from data/bucket_summaries.jsonl."""
    out = {}
    for l in BUCKETS_PATH.open():
        d = json.loads(l)
        out[(d["sid"], int(d["bucket_start"]))] = d
    return out


def enumerate_prose_buckets(grain_sec: int):
    """Yield one (sid, hb, meta, [(ts, role, text), ...]) per bucket from prose files."""
    bags = defaultdict(list)
    sess_meta = {}
    for p in sorted(PROSE_DIR.glob("*.jsonl")):
        for line in p.open():
            try:
                o = json.loads(line)
            except Exception:
                continue
            sid = o.get("session_id")
            ts = float(o.get("ts") or 0)
            text = (o.get("text") or "").strip()
            if not sid or not ts or not text:
                continue
            hb = int(ts // grain_sec) * grain_sec
            role = o.get("role") or ""
            bags[(sid, hb)].append((ts, role, text))
            if sid not in sess_meta:
                sess_meta[sid] = {
                    "project": o.get("project") or "",
                    "fleet": o.get("fleet") or o.get("project") or "",
                    "agent_title": o.get("agent_title") or "",
                    "provider": o.get("provider") or "",
                }
    for (sid, hb), msgs in bags.items():
        if len(msgs) < 3:
            continue
        msgs.sort(key=lambda x: x[0])
        yield sid, hb, sess_meta[sid], msgs


def stratified_pick(candidates: list, n_target: int, seed: int) -> list:
    """Round-robin across (fleet, mc_bucket) strata."""
    random.seed(seed)
    strata = defaultdict(list)
    for c in candidates:
        key = (c["fleet"], mc_bucket(c["msg_count"]))
        strata[key].append(c)
    for k in strata:
        random.shuffle(strata[k])
    out = []
    stratum_keys = sorted(strata.keys())
    exhausted = set()
    bi = 0
    while len(out) < n_target and len(exhausted) < len(stratum_keys):
        k = stratum_keys[bi % len(stratum_keys)]
        if k not in exhausted:
            if strata[k]:
                out.append(strata[k].pop())
            else:
                exhausted.add(k)
        bi += 1
    return out


def main():
    bucket_summaries = load_bucket_summaries()
    print(f"[gold] {len(bucket_summaries)} existing bucket summaries loaded", file=sys.stderr)

    candidates = []
    for sid, hb, meta, msgs in enumerate_prose_buckets(GRAIN_SEC):
        key = (sid, hb)
        if key not in bucket_summaries:
            continue
        bs = bucket_summaries[key]
        candidates.append({
            "sid": sid,
            "bucket_start": hb,
            "grain_sec": GRAIN_SEC,
            "project": meta["project"],
            "fleet": meta["fleet"],
            "agent_title": meta["agent_title"],
            "provider": meta["provider"],
            "msg_count": len(msgs),
            "msgs": [
                {"ts": ts, "role": role,
                 "text": text if len(text) < MAX_MSG_CHARS else text[:MAX_MSG_CHARS] + "…[truncated]"}
                for ts, role, text in msgs
            ],
            "qwen30_summary": bs.get("summary"),
        })
    print(f"[enum] {len(candidates)} buckets with prose + existing summary", file=sys.stderr)

    picks = stratified_pick(candidates, N_TARGET, SEED)
    print(f"[pick] {len(picks)} stratified picks", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Clean existing bundle files
    for p in OUT_DIR.glob("bundle_*.json"):
        p.unlink()

    manifest = {"bundles": [], "n_total": len(picks), "schema": "v0.2-prose", "seed": SEED}
    bundle_i = 1
    for start in range(0, len(picks), BUNDLE_SIZE):
        chunk = picks[start:start + BUNDLE_SIZE]
        if len(chunk) < BUNDLE_SIZE:
            break
        bname = f"bundle_{bundle_i:02d}"
        out_path = OUT_DIR / f"{bname}.json"
        out_path.write_text(json.dumps(
            {"schema": "v0.2-prose", "bundle_id": bname, "n_items": len(chunk), "items": chunk},
            indent=1,
        ))
        manifest["bundles"].append({
            "bundle_id": bname,
            "path": str(out_path),
            "n_items": len(chunk),
            "fleets": sorted({c["fleet"] for c in chunk}),
            "msg_count_range": [min(c["msg_count"] for c in chunk), max(c["msg_count"] for c in chunk)],
        })
        bundle_i += 1

    (OUT_DIR / "_index.json").write_text(json.dumps(manifest, indent=2))
    print(f"[write] {bundle_i - 1} bundles → {OUT_DIR}", file=sys.stderr)
    print(f"[write] {OUT_DIR / '_index.json'}", file=sys.stderr)


if __name__ == "__main__":
    main()
