#!/usr/bin/env python3
"""Wrapper around classify_bundles.py that targets the Nebius Token Factory
inference endpoint instead of studio. Used by sft_overnight.py to classify the
20 gold bundles through a fine-tuned model.

Pass-through args: --model <ft-model-id>  --slug <label>  [--budget-cents N]
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(".")

if __name__ == "__main__":
    env = os.environ.copy()
    env["CLASSIFY_API_URL"] = "https://api.tokenfactory.nebius.com/v1/chat/completions"
    args = sys.argv[1:]
    if "--budget-cents" not in args:
        args += ["--budget-cents", "80"]
    cmd = [sys.executable, str(ROOT / "scripts/classify_bundles.py"), *args]
    print(f"[ft-wrapper] exec {cmd}", file=sys.stderr)
    sys.exit(subprocess.call(cmd, env=env))
