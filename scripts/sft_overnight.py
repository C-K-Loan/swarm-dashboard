#!/usr/bin/env python3
"""Overnight orchestrator for the 3 SFT jobs in data/sft/last_jobs.json.

Polls each job every POLL_SEC. On terminal status:
  - succeeded: records fine_tuned_model id, kicks classify_ft_bundles.py in bg
                against 20 gold bundles → data/eval/ref_<label>.jsonl → re-runs
                compare_models.py → mirrors compare_v2.json to UI/public.
  - failed/cancelled: records error, moves on.

State persisted to data/sft/overnight_state.json after every tick so a crash is
resumable. Log output goes to logs/sft_overnight.log for CKL to review in the AM.

Session cap: $10 total (project-wide). This script does not fire new API jobs;
training spend is bounded by what fire_sft_jobs.py already created.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(".")
JOBS_PATH = ROOT / "data/sft/last_jobs.json"
STATE_PATH = ROOT / "data/sft/overnight_state.json"
LOG_PATH = ROOT / "logs/sft_overnight.log"
BASE = "https://api.tokenfactory.nebius.com/v1"
POLL_SEC = 120
TERMINAL = {"succeeded", "failed", "cancelled"}


def log(msg: str):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def load_env():
    env_file = Path(".env")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)


def get_job(job_id: str) -> dict:
    key = os.environ["NEBIUS_TOKEN_FACTORY_KEY"]
    req = urllib.request.Request(
        f"{BASE}/fine_tuning/jobs/{job_id}",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"id": job_id, "status": "unknown", "_poll_error": str(e)}


def load_jobs() -> list[dict]:
    d = json.loads(JOBS_PATH.read_text())
    return [j for j in d["jobs"] if j.get("job_id") and not j.get("aborted")]


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"jobs": {}, "classifiers": {}, "started_at": time.time()}


def save_state(st: dict):
    STATE_PATH.write_text(json.dumps(st, indent=2))


def kick_classify(label: str, ft_model: str):
    """Launch classify_ft_bundles.py in background for this fine-tuned model."""
    cmd = [
        sys.executable, str(ROOT / "scripts/classify_ft_bundles.py"),
        "--model", ft_model,
        "--slug", f"{label}_sft",
    ]
    log_f = ROOT / f"logs/classify_{label}.log"
    log_f.parent.mkdir(parents=True, exist_ok=True)
    with log_f.open("a") as f:
        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
    log(f"[kick] classify {label} model={ft_model} pid={p.pid} log={log_f}")
    return p.pid


def regen_compare():
    cmd = [sys.executable, str(ROOT / "scripts/compare_models.py")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    log(f"[compare] rc={r.returncode} out={r.stdout[:200]} err={r.stderr[:200]}")
    # mirror to UI public
    src = ROOT / "data/eval/compare_v2.json"
    dst = ROOT / "ui/public/data/compare_v2.json"
    if src.exists():
        dst.write_bytes(src.read_bytes())
        log(f"[mirror] compare_v2.json → {dst}")


def main():
    load_env()
    state = load_state()
    jobs = load_jobs()
    log(f"[start] polling {len(jobs)} jobs, poll_sec={POLL_SEC}")
    for j in jobs:
        log(f"[init] {j['label']} job_id={j['job_id']}")
        state["jobs"].setdefault(j["label"], {"job_id": j["job_id"], "status": "pending", "ft_model": None, "classifier_pid": None})
    save_state(state)

    # Track which labels have had classifier launched
    launched = {k for k, v in state["jobs"].items() if v.get("classifier_pid")}

    tick = 0
    while True:
        tick += 1
        done = 0
        for j in jobs:
            label = j["label"]
            st = state["jobs"][label]
            if st["status"] in TERMINAL:
                done += 1
                continue
            info = get_job(j["job_id"])
            status = info.get("status", "?")
            if status != st["status"]:
                log(f"[tick {tick}] {label}: {st['status']} → {status}")
                st["status"] = status
                st["last_updated"] = int(time.time())
                if status == "succeeded":
                    ft = info.get("fine_tuned_model") or info.get("output_model") or info.get("fine_tuned_model_name")
                    st["ft_model"] = ft
                    log(f"[succeeded] {label} ft_model={ft}")
                    if ft and label not in launched:
                        pid = kick_classify(label, ft)
                        st["classifier_pid"] = pid
                        launched.add(label)
                elif status in ("failed", "cancelled"):
                    st["error"] = info.get("error") or info.get("_poll_error")
                    log(f"[{status}] {label} error={st.get('error')}")
                save_state(state)
            if status in TERMINAL:
                done += 1
        save_state(state)
        # Check if classifiers finished → regen compare
        for label, pid in [(k, v.get("classifier_pid")) for k, v in state["jobs"].items() if v.get("classifier_pid") and not v.get("classifier_done")]:
            # Check if process still alive
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
            if not alive:
                log(f"[classifier done] {label} pid={pid}")
                state["jobs"][label]["classifier_done"] = True
                save_state(state)
                regen_compare()

        if done == len(jobs):
            # All jobs terminal; wait a bit more for classifiers if any still running
            running_cls = [k for k, v in state["jobs"].items() if v.get("classifier_pid") and not v.get("classifier_done")]
            if not running_cls:
                log("[finish] all jobs + classifiers complete")
                return
            log(f"[wait] classifiers still running: {running_cls}")

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
