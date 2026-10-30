#!/usr/bin/env python3
"""Fire a Qwen3-14B LoRA fine-tune on Nebius Token Factory.

Upload data/sft/train_v2_sft.jsonl → create fine-tuning job → poll → record result.

Hard-stop: if the API returns an estimated_cost > $5 anywhere in the job object,
abort and surface it. Also surface the first 20 lines of the error block if the
job fails.

Writes:
  data/sft/last_job.json  — {job_id, status, model_name, created, updated, error}

Env: NEBIUS_TOKEN_FACTORY_KEY (sourced from [REDACTED_PATH]).
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(".")
SFT_FILE = ROOT / "data/sft/train_v2_sft.jsonl"
OUT = ROOT / "data/sft/last_job.json"
BASE = "https://api.tokenfactory.nebius.com/v1"

HARD_STOP_USD = 5.00

# Brainstorm spec defaults (DO NOT change without asking)
MODEL = "Qwen/Qwen3-14B"
SUFFIX = "cct-fleet-v2"
HYPER = {
    "batch_size": 8,
    "learning_rate": 1e-4,
    "n_epochs": 3,
    "warmup_ratio": 0.0,
    "weight_decay": 0.0,
    "lora": True,
    "lora_r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "packing": True,
    "max_grad_norm": 1.0,
    "context_length": 8192,
}


def load_env():
    env_file = Path(".env")
    if not env_file.exists():
        print(f"[ERR] {env_file} missing", file=sys.stderr)
        sys.exit(2)
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)
    if not os.environ.get("NEBIUS_TOKEN_FACTORY_KEY"):
        print("[ERR] NEBIUS_TOKEN_FACTORY_KEY not set", file=sys.stderr)
        sys.exit(2)


def _auth():
    return {"Authorization": f"Bearer {os.environ['NEBIUS_TOKEN_FACTORY_KEY']}"}


def upload_file(path: Path) -> str:
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"[upload] {path.name} ({size_mb:.2f} MB) → POST {BASE}/files", file=sys.stderr)
    with path.open("rb") as fh:
        r = requests.post(
            f"{BASE}/files",
            headers=_auth(),
            files={"file": (path.name, fh, "application/jsonl")},
            data={"purpose": "fine-tune"},
            timeout=180,
        )
    if r.status_code >= 300:
        print(f"[upload FAIL] HTTP {r.status_code}: {r.text[:800]}", file=sys.stderr)
        sys.exit(3)
    body = r.json()
    file_id = body.get("id")
    if not file_id:
        print(f"[upload FAIL] no id in response: {body}", file=sys.stderr)
        sys.exit(3)
    print(f"[upload OK] file_id={file_id}", file=sys.stderr)
    return file_id


def create_job(training_file: str) -> dict:
    body = {
        "model": MODEL,
        "suffix": SUFFIX,
        "training_file": training_file,
        "hyperparameters": HYPER,
        "seed": 42,
    }
    print(f"[create] POST {BASE}/fine_tuning/jobs", file=sys.stderr)
    print(f"         model={MODEL} suffix={SUFFIX} r={HYPER['lora_r']} alpha={HYPER['lora_alpha']} lr={HYPER['learning_rate']} epochs={HYPER['n_epochs']}", file=sys.stderr)
    r = requests.post(
        f"{BASE}/fine_tuning/jobs",
        headers={**_auth(), "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=60,
    )
    if r.status_code >= 300:
        print(f"[create FAIL] HTTP {r.status_code}: {r.text[:1500]}", file=sys.stderr)
        sys.exit(4)
    job = r.json()
    print(f"[create OK] job_id={job.get('id')} status={job.get('status')}", file=sys.stderr)
    return job


def get_job(job_id: str) -> dict:
    r = requests.get(
        f"{BASE}/fine_tuning/jobs/{job_id}",
        headers=_auth(),
        timeout=30,
    )
    if r.status_code >= 300:
        return {"id": job_id, "status": "unknown", "_poll_error": f"HTTP {r.status_code}: {r.text[:400]}"}
    return r.json()


def check_cost_guard(job: dict) -> None:
    """Walk the job dict looking for any 'estimated_cost' / 'cost' / 'cost_usd' field
    above HARD_STOP_USD. If found, print + exit with code 10."""
    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                sub = f"{path}.{k}" if path else k
                if isinstance(v, (int, float)) and k.lower() in ("estimated_cost", "cost", "cost_usd", "estimated_cost_usd", "price", "price_usd"):
                    if v > HARD_STOP_USD:
                        print(f"[HARD STOP] {sub}={v:.2f} > ${HARD_STOP_USD:.2f}", file=sys.stderr)
                        sys.exit(10)
                walk(v, sub)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
    walk(job)


def write_result(job: dict) -> None:
    model_name = job.get("fine_tuned_model") or job.get("fine_tuned_model_name") or job.get("output_model")
    out = {
        "job_id": job.get("id"),
        "status": job.get("status"),
        "model_name": model_name,
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at") or job.get("updated_at"),
        "error": job.get("error"),
        "hyperparameters": job.get("hyperparameters", HYPER),
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"[write] {OUT}", file=sys.stderr)


def main():
    load_env()
    if not SFT_FILE.exists():
        print(f"[ERR] {SFT_FILE} missing — run format_sft_pairs.py first", file=sys.stderr)
        sys.exit(2)

    # 1. Upload
    file_id = upload_file(SFT_FILE)

    # 2. Create job
    job = create_job(file_id)
    check_cost_guard(job)

    job_id = job["id"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    write_result(job)

    # 3. Poll
    print(f"[poll] every 60s on {job_id}", file=sys.stderr)
    terminal = {"succeeded", "failed", "cancelled"}
    last_status = None
    poll_n = 0
    while True:
        job = get_job(job_id)
        status = job.get("status", "?")
        if status != last_status:
            print(f"[poll {poll_n}] status={status}", file=sys.stderr)
            last_status = status
        check_cost_guard(job)
        write_result(job)
        if status in terminal:
            if status == "succeeded":
                mn = job.get("fine_tuned_model") or job.get("output_model") or "?"
                print(f"[DONE] model={mn}", file=sys.stderr)
            else:
                print(f"[DONE] status={status} error={job.get('error')}", file=sys.stderr)
                sys.exit(1 if status != "succeeded" else 0)
            break
        poll_n += 1
        time.sleep(60)


if __name__ == "__main__":
    main()
