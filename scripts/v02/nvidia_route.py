#!/usr/bin/env python3
"""Run an openrouter_free consumer unchanged on the NVIDIA build route.

  python3 scripts/v02/nvidia_route.py <script.py> [script args...]

Points the real openrouter_free's request surface (endpoint, model, key path, api, audit, preflight, error
classes) at nvidia_build, keeping its prompt/feedback helpers, then runs the script as __main__.
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nvidia_build

import openrouter_free

SURFACE = ('API', 'MODEL', 'PROVIDER', 'DEFAULT_KEY', 'ZERO_PRICES', 'api', 'audit_response', 'preflight',
           'GuardError', 'RateLimited', 'TransientProviderError')
for name in SURFACE:
    setattr(openrouter_free, name, getattr(nvidia_build, name))
script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(script).resolve().parent))
runpy.run_path(script, run_name='__main__')
