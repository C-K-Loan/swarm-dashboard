#!/usr/bin/env python3
"""Fire 3 parallel LoRA jobs on Nebius Token Factory.

Uploads data/sft/train_v2_sft.jsonl once, then creates 3 fine-tuning jobs:
  1. Qwen/Qwen3.5-4B r=16 alpha=32   suffix=cct-fleet-v2-4b-r16
  2. Qwen/Qwen3.5-4B r=32 alpha=64   suffix=cct-fleet-v2-4b-r32
  3. Qwen/Qwen3-30B-A3B-Instruct-2507 r=16 alpha=32 suffix=cct-fleet-v2-30b

Writes data/sft/last_jobs.json as an array of job records.
Per-job hard-stop: estimated_cost > $5. Session cap $10 enforced on launcher side.

Does NOT poll — poll_sft_jobs.py runs in background to track completion.
"""
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(".")
SFT_FILE = ROOT / "data/sft/train_v2_sft.jsonl"
OUT = ROOT / "data/sft/last_jobs.json"
BASE = "https://api.tokenfactory.nebius.com/v1"
HARD_STOP_USD = 5.00

JOBS = [
    {
        "label": "qwen14b_r16",
        "model": "Qwen/Qwen3-14B",
        "suffix": "cct-fleet-v2-14b-r16",
        "lora_r": 16, "lora_alpha": 32,
        "batch_size": 8,
    },
    {
        "label": "qwen14b_r32",
        "model": "Qwen/Qwen3-14B",
        "suffix": "cct-fleet-v2-14b-r32",
        "lora_r": 32, "lora_alpha": 64,
        "batch_size": 8,
    },
    {
        "label": "qwen30b_r16",
        "model": "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "suffix": "cct-fleet-v2-30b",
        "lora_r": 16, "lora_alpha": 32,
        "batch_size": 16,
    },
]

BASE_HYPER = {
    "batch_size": 8,
    "learning_rate": 1e-4,
    "n_epochs": 3,
    "warmup_ratio": 0.0,
    "weight_decay": 0.0,
    "lora": True,
    "lora_dropout": 0.05,
    "packing": True,
    "max_grad_norm": 1.0,
    "context_length": 8192,
}


def load_env():
    env_file = Path(".env")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)


def _auth():
    return {"Authorization": f"Bearer {os.environ['NEBIUS_TOKEN_FACTORY_KEY']}"}


def upload_file() -> str:
    size_mb = SFT_FILE.stat().st_size / 1024 / 1024
    print(f"[upload] {SFT_FILE.name} ({size_mb:.2f} MB)", file=sys.stderr)
    with SFT_FILE.open("rb") as fh:
        r = requests.post(
            f"{BASE}/files",
            headers=_auth(),
            files={"file": (SFT_FILE.name, fh, "application/jsonl")},
            data={"purpose": "fine-tune"},
            timeout=180,
        )
    if r.status_code >= 300:
        print(f"[upload FAIL] {r.status_code}: {r.text[:600]}", file=sys.stderr)
        sys.exit(3)
    fid = r.json()["id"]
    print(f"[upload OK] file_id={fid}", file=sys.stderr)
    return fid


def _walk_cost(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            sub = f"{path}.{k}" if path else k
            if isinstance(v, (int, float)) and k.lower() in ("estimated_cost", "cost", "cost_usd", "estimated_cost_usd", "price", "price_usd"):
                if v > HARD_STOP_USD:
                    return (sub, v)
            r = _walk_cost(v, sub)
            if r:
                return r
    elif isinstance(o, list):
        for i, v in enumerate(o):
            r = _walk_cost(v, f"{path}[{i}]")
            if r:
                return r
    return None


def create_job(spec: dict, training_file: str) -> dict:
    hyper = {**BASE_HYPER, "batch_size": spec["batch_size"], "lora_r": spec["lora_r"], "lora_alpha": spec["lora_alpha"]}
    body = {
        "model": spec["model"],
        "suffix": spec["suffix"],
        "training_file": training_file,
        "hyperparameters": hyper,
        "seed": 42,
    }
    print(f"[create] {spec['label']}: model={spec['model']} r={spec['lora_r']}", file=sys.stderr)
    r = requests.post(
        f"{BASE}/fine_tuning/jobs",
        headers={**_auth(), "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=60,
    )
    if r.status_code >= 300:
        print(f"[create FAIL] {spec['label']} HTTP {r.status_code}: {r.text[:1500]}", file=sys.stderr)
        return {"label": spec["label"], "error": f"HTTP {r.status_code}: {r.text[:400]}"}
    job = r.json()
    bad = _walk_cost(job)
    if bad:
        print(f"[HARD STOP] {spec['label']} {bad[0]}={bad[1]:.2f} > ${HARD_STOP_USD:.2f} — would cancel", file=sys.stderr)
        # Attempt cancellation to be safe
        jid = job.get("id")
        if jid:
            requests.post(f"{BASE}/fine_tuning/jobs/{jid}/cancel", headers=_auth(), timeout=30)
        return {"label": spec["label"], "job_id": jid, "aborted": True, "reason": f"estimated {bad[0]}={bad[1]:.2f}"}
    print(f"[create OK] {spec['label']} job_id={job.get('id')} status={job.get('status')}", file=sys.stderr)
    return {
        "label": spec["label"],
        "job_id": job.get("id"),
        "status": job.get("status"),
        "model": spec["model"],
        "suffix": spec["suffix"],
        "lora_r": spec["lora_r"],
        "lora_alpha": spec["lora_alpha"],
        "created_at": job.get("created_at"),
    }


def main():
    load_env()
    if not SFT_FILE.exists():
        print(f"[ERR] {SFT_FILE} missing", file=sys.stderr)
        sys.exit(2)
    file_id = upload_file()
    results = []
    for spec in JOBS:
        results.append(create_job(spec, file_id))
    OUT.write_text(json.dumps({"training_file_id": file_id, "jobs": results}, indent=2))
    print(f"[write] {OUT}", file=sys.stderr)
    aborted = [r for r in results if r.get("aborted") or r.get("error")]
    ok = [r for r in results if r.get("job_id") and not r.get("aborted") and not r.get("error")]
    print(f"\n=== FIRED {len(ok)}/{len(JOBS)} ===", file=sys.stderr)
    for r in ok:
        print(f"  {r['label']}: {r['job_id']}", file=sys.stderr)
    if aborted:
        print(f"=== ABORTED/FAILED {len(aborted)} ===", file=sys.stderr)
        for r in aborted:
            print(f"  {r['label']}: {r.get('error') or r.get('reason')}", file=sys.stderr)


if __name__ == "__main__":
    main()
