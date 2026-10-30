#!/usr/bin/env python3
"""v13-V/W: pull codex sessions from enabled remote hosts into data/remote-codex/<host>/.

Reads data/remote_hosts.json. For each enabled host with a non-null ssh_target,
rsyncs its codex sessions directory to the local cache. Updates `last_scraped`
timestamp in the config.

Never writes to the remote host (pull-only). Respects the PRIVACY_LOCKED_PATHS
blocklist via rsync --exclude.

Usage: scrape_remote_hosts.py [--host NAME] [--config PATH] [--dry-run]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "remote_hosts.json"
CACHE_ROOT = ROOT / "data" / "remote-codex"

EXCLUDES = [
    "--exclude=[REDACTED][REDACTED]/",
    "--exclude=[REDACTED][REDACTED]/",
]


def rsync_host(host, dry_run=False):
    name = host["name"]
    tgt = host.get("ssh_target")
    if not tgt:
        return {"skipped": "local, no ssh_target"}
    src = f"{tgt}:{host['path']}"
    dest = CACHE_ROOT / name
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-av", "--delete-after"] + EXCLUDES
    if dry_run:
        cmd.append("--dry-run")
    cmd.append(src)
    cmd.append(str(dest) + "/")
    print(f"[rsync] {' '.join(cmd)}", file=sys.stderr)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    elapsed = time.time() - t0
    if r.returncode != 0:
        return {"error": r.stderr[:400], "elapsed": elapsed}
    # Count files pulled
    rollout_count = sum(1 for _ in (dest).rglob("rollout-*.jsonl"))
    return {"ok": True, "rollout_files": rollout_count, "elapsed": round(elapsed, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None, help="scrape only this host (by name)")
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(args.config))
    changed = False
    for host in cfg["hosts"]:
        if not host.get("enabled"):
            continue
        if args.host and host["name"] != args.host:
            continue
        if not host.get("ssh_target"):
            continue
        print(f"\n=== {host['name']} ({host['ssh_target']}) ===", file=sys.stderr)
        result = rsync_host(host, dry_run=args.dry_run)
        print(f"  → {result}", file=sys.stderr)
        if result.get("ok") and not args.dry_run:
            host["last_scraped"] = time.strftime("%Y-%m-%d %H:%M:%S")
            host["rollout_files"] = result["rollout_files"]
            changed = True
    if changed:
        with open(args.config, "w") as fh:
            json.dump(cfg, fh, indent=2)
        print(f"\n[saved] updated {args.config}", file=sys.stderr)


if __name__ == "__main__":
    main()
