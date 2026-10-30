#!/usr/bin/env python3
"""v14-HH: live refresh daemon.

Loops every --interval seconds; on each tick:
  1. Scan local + enabled-remote codex session dirs for new/changed rollout files.
  2. Scan Claude bundle dir mtimes for new sessions.
  3. If any delta: rsync remote hosts, re-run extract_codex_prose.py (full) + extract_prose.py
     (per changed project), identify newly-extracted prose rows (by src_id+ts not yet in
     classified JSONL), classify just those via classify_batch.py, summarize new buckets,
     rebuild timeline/cards/transcript/bucket_summary indexes, update cost_summary, write
     ui/public/data/last-refresh.txt with the new ISO timestamp.
  4. Pause if cumulative Nebius spend hits BUDGET_HARD_STOP ($2.80 by default).

Logs to logs/live_refresh.log. Config at data/live_refresh_config.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "live_refresh.log"
LAST_REFRESH_PATH = ROOT / "ui" / "public" / "data" / "last-refresh.txt"
CONFIG_PATH = ROOT / "data" / "live_refresh_config.json"
COST_SUMMARY_PATH = ROOT / "data" / "cost_summary.json"
PROSE_DIR = ROOT / "data" / "prose"
CLASSIFIED_DIR = ROOT / "data" / "classified"
REMOTE_HOSTS_PATH = ROOT / "data" / "remote_hosts.json"
SCRATCH_DELTA = ROOT / "data" / "live-tmp"
SCRATCH_DELTA.mkdir(parents=True, exist_ok=True)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "enabled": True,
    "interval_sec": 180,
    "budget_hard_stop_usd": 2.80,
    "budget_hourly_soft_cap_usd": 0.05,
    "last_tick": None,
    "last_tick_cost": None,
    "last_tick_delta_cents": 0,
    "status": "idle",
    "classify_model": "Qwen/Qwen3-30B-A3B-Instruct-2507",
}


def log(msg, level="info"):
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    line = f"[{ts}] {level.upper():5s} {msg}"
    print(line, file=sys.stderr)
    with open(LOG_PATH, "a") as fh:
        fh.write(line + "\n")


def load_config():
    if CONFIG_PATH.exists():
        try:
            d = json.load(open(CONFIG_PATH))
            # merge defaults
            for k, v in DEFAULT_CONFIG.items():
                d.setdefault(k, v)
            return d
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)


def current_cost():
    if not COST_SUMMARY_PATH.exists():
        return 0.0
    try:
        d = json.load(open(COST_SUMMARY_PATH))
        return float(d.get("nebius_total_cost_usd") or (d.get("ours", {}).get("cost_usd", 0.0) + (d.get("extras", {}).get("summarization", {}).get("cost_usd", 0.0))))
    except Exception:
        return 0.0


def scan_codex_mtimes():
    """Return dict of local+remote-cache rollout file paths → mtime."""
    out = {}
    local = Path.home() / ".codex" / "sessions"
    if local.exists():
        for p in local.rglob("rollout-*.jsonl"):
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    remote_root = ROOT / "data" / "remote-codex"
    if remote_root.exists():
        for p in remote_root.rglob("rollout-*.jsonl"):
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    return out


def scan_claude_bundle_mtimes():
    """Return dict of claude bundle project json → mtime (fast proxy for new sessions)."""
    out = {}
    b = Path("[REDACTED_PATH]")
    if b.exists():
        for p in b.glob("*.json"):
            try:
                out[p.stem] = p.stat().st_mtime
            except OSError:
                continue
    return out


def rsync_remote_hosts():
    """Pull enabled remote hosts via scrape_remote_hosts.py (quiet)."""
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "scrape_remote_hosts.py")],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            log(f"rsync returned {r.returncode}: {r.stderr[:200]}", "warn")
    except Exception as e:
        log(f"rsync failed: {e}", "warn")


def run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    if r.returncode != 0:
        log(f"{cmd[1] if len(cmd) > 1 else cmd[0]} rc={r.returncode}: {r.stderr[:200]}", "warn")
    return r


def find_unclassified_rows():
    """Scan data/prose/*.jsonl for rows not yet in corresponding data/classified/*.jsonl.
    Returns dict project_name → list[row dicts] (dedup by src_id+ts)."""
    classified_keys = {}  # project → set of (sid, round(ts,2))
    for f in CLASSIFIED_DIR.glob("*.jsonl"):
        proj = f.stem.split("__", 1)[0]
        keys = classified_keys.setdefault(proj, set())
        for line in open(f):
            try:
                o = json.loads(line)
            except Exception:
                continue
            keys.add((o.get("src_id"), round(float(o.get("ts") or 0), 2)))
    new_rows = {}
    for f in PROSE_DIR.glob("*.jsonl"):
        proj = f.stem
        done = classified_keys.get(proj, set())
        rows = []
        for line in open(f):
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("role") not in ("assistant_prose", "assistant"):
                # Only classify agent prose; user_prose has no classification pass today
                continue
            key = (o.get("session_id"), round(float(o.get("ts") or 0), 2))
            if key in done:
                continue
            rows.append(o)
        if rows:
            new_rows[proj] = rows
    return new_rows


def tick(cfg):
    """One refresh tick. Returns dict with status + spend_delta_cents."""
    t0 = time.time()
    status = {"status": "ok", "spend_delta_cents": 0, "new_rows": 0, "classified_rows": 0, "projects": []}
    cost_before = current_cost()

    # Hard budget stop
    if cost_before >= cfg["budget_hard_stop_usd"]:
        status["status"] = "budget_stop"
        log(f"budget hard stop hit (${cost_before:.4f} >= ${cfg['budget_hard_stop_usd']})", "warn")
        return status

    # 1. Pull remote hosts first (quiet, no-op if nothing to pull)
    rsync_remote_hosts()

    # 2. Re-extract codex (fast, deterministic, dedup via content hash)
    run([sys.executable, "scripts/extract_codex_prose.py"], timeout=300)

    # 3. Re-extract claude projects whose bundle mtime grew; simplest: re-extract all 4 known
    for proj in ("gamejam", "other-project", "other-project", "other-project"):
        bundle = Path(f"[REDACTED_PATH]{proj}.json")
        if not bundle.exists():
            continue
        run([sys.executable, "scripts/extract_prose.py", proj], timeout=300)

    # 4. Identify unclassified rows
    new_rows = find_unclassified_rows()
    status["new_rows"] = sum(len(v) for v in new_rows.values())
    status["projects"] = list(new_rows.keys())
    if not new_rows:
        log(f"tick: no new rows ({time.time()-t0:.1f}s)", "info")
        return status

    # 5. Classify per project, writing to a temp jsonl and appending to data/classified
    for proj, rows in new_rows.items():
        delta_path = SCRATCH_DELTA / f"{proj}__delta.jsonl"
        with open(delta_path, "w") as fh:
            for o in rows:
                fh.write(json.dumps(o) + "\n")
        model = cfg["classify_model"]
        model_slug = model.lower().replace("/", "-").replace(":", "-").split("-")[0] + "3-30b-a3b"
        # Simpler: use the exact filename pattern codex-gamejam__qwen3-30b-a3b.jsonl
        existing = list(CLASSIFIED_DIR.glob(f"{proj}__*.jsonl"))
        if existing:
            out_classified = existing[0]
        else:
            out_classified = CLASSIFIED_DIR / f"{proj}__qwen3-30b-a3b.jsonl"
        # Classify into scratch then append
        scratch_out = SCRATCH_DELTA / f"{proj}__delta_classified.jsonl"
        remaining_budget = max(0.0, cfg["budget_hard_stop_usd"] - current_cost())
        cents = max(1, int(remaining_budget * 100))
        env = os.environ.copy()
        # Ensure NEBIUS key is loaded
        key_env = Path(".env")
        if key_env.exists() and "NEBIUS_TOKEN_FACTORY_KEY" not in env:
            for line in open(key_env):
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        r = subprocess.run(
            [sys.executable, "scripts/classify_batch.py", str(delta_path), str(scratch_out),
             "--model", model, "--budget-cents", str(cents), "--workers", "15"],
            capture_output=True, text=True, timeout=900, cwd=str(ROOT), env=env,
        )
        if r.returncode != 0:
            log(f"classify {proj} rc={r.returncode}: {r.stderr[:200]}", "warn")
            continue
        if scratch_out.exists():
            with open(out_classified, "a") as dst, open(scratch_out) as src:
                for line in src:
                    dst.write(line)
                    status["classified_rows"] += 1

    # 6. Bake fresh bucket summaries on gamejam (append-only)
    if "gamejam" in new_rows:
        env = os.environ.copy()
        key_env = Path(".env")
        if key_env.exists() and "NEBIUS_TOKEN_FACTORY_KEY" not in env:
            for line in open(key_env):
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        subprocess.run(
            [sys.executable, "scripts/summarize_buckets.py", "--project", "gamejam",
             "--append", "--budget-cents", "10", "--workers", "10"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT), env=env,
        )

    # 7. Rebuild all downstream indexes
    run([sys.executable, "scripts/make_timeline.py", "data/classified", "data/prose", "data/timeline.json"])
    run([sys.executable, "scripts/reduce_to_cards.py", "data/classified", "data/cards.json", "--prose-dir", "data/prose"])
    run([sys.executable, "scripts/build_transcript_index.py"])
    run([sys.executable, "scripts/build_bucket_summary_index.py"])
    run([sys.executable, "scripts/build_fleet_index.py"])
    run([sys.executable, "scripts/build_comms_index.py"])
    run([sys.executable, "scripts/build_spawn_tree.py"])
    run([sys.executable, "scripts/build_file_diffs_index.py"])
    run([sys.executable, "scripts/build_tree_index.py"])
    run([sys.executable, "scripts/regen_cost_summary.py"])

    cost_after = current_cost()
    status["spend_delta_cents"] = int(round((cost_after - cost_before) * 100))
    log(f"tick done: +{status['new_rows']} rows, {status['classified_rows']} classified, +${(cost_after-cost_before):.4f}, {time.time()-t0:.1f}s", "info")
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    cfg["interval_sec"] = args.interval or cfg["interval_sec"]
    log(f"daemon start · interval={cfg['interval_sec']}s · hard_stop=${cfg['budget_hard_stop_usd']}", "info")

    while True:
        if not cfg.get("enabled", True):
            log("disabled via config; sleeping 60s", "info")
            time.sleep(60)
            cfg = load_config()
            continue
        cfg["status"] = "ticking"
        save_config(cfg)
        try:
            result = tick(cfg)
            cfg["last_tick"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            cfg["last_tick_cost"] = current_cost()
            cfg["last_tick_delta_cents"] = result["spend_delta_cents"]
            cfg["status"] = result["status"]
            cfg["last_tick_new_rows"] = result["new_rows"]
            cfg["last_tick_classified_rows"] = result["classified_rows"]
            save_config(cfg)
            # Mirror the two large data files that live outside ui/public so the
            # browser's cache-busted re-fetches ( /data/*.json?cb=... ) hit 200.
            # bucket_summaries.json and transcript.json already land in ui/public/data
            # from their respective build_* scripts, but timeline.json + artifacts.json
            # are written to data/ root and need to be copied here.
            import shutil
            for fname in ("timeline.json", "artifacts.json", "spawn_tree.json", "file_diffs.json", "tree.json", "repo_registry.json"):
                src = ROOT / "data" / fname
                if src.exists():
                    dst = ROOT / "ui" / "public" / "data" / fname
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
            # eval data: compare_v2.json + prose_compare.json live at data/eval/
            for ef in ("compare_v2.json", "prose_compare.json"):
                eval_src = ROOT / "data" / "eval" / ef
                if eval_src.exists():
                    shutil.copyfile(eval_src, ROOT / "ui" / "public" / "data" / ef)
            judge_src = ROOT / "data" / "eval" / "prose_judge" / "prose_judge_summary.json"
            if judge_src.exists():
                shutil.copyfile(judge_src, ROOT / "ui" / "public" / "data" / "prose_judge_summary.json")
            sft_src = ROOT / "data" / "sft" / "sft_final.json"
            if sft_src.exists():
                shutil.copyfile(sft_src, ROOT / "ui" / "public" / "data" / "sft_final.json")
            # Update sentinel for UI polling
            LAST_REFRESH_PATH.parent.mkdir(parents=True, exist_ok=True)
            LAST_REFRESH_PATH.write_text(cfg["last_tick"] + "\n")
        except Exception as e:
            log(f"tick exception: {e}", "error")
        if args.once:
            break
        time.sleep(cfg["interval_sec"])


if __name__ == "__main__":
    main()
