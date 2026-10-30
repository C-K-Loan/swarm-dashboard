import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_sample_fleet_smoke():
    r = subprocess.run([sys.executable, str(ROOT / 'scripts/v02/smoke_adapter.py'), str(ROOT / 'examples/sample-fleet')],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'SMOKE OK' in r.stdout and "'done'" in r.stdout
