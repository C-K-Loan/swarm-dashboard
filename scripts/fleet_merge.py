"""Canonical fleet-name merging for dashboard display + build-time index.

Importers: make_timeline.py, reduce_to_cards.py, build_fleet_index.py.
"""

import json
import os
from pathlib import Path

# Sibling fleet names collapsed into one canonical label, plus canonical fleets that must never be
# classified or sent to any API. Both come from a local, untracked JSON file:
# {"merge": {"raw-name": "canonical"}, "privacy_locked": ["canonical"]}
_CFG = Path(os.environ.get("FLEET_ALIASES_FILE") or Path(__file__).resolve().parents[1] / "config" / "fleet-aliases.json")
try:
    _DATA = json.loads(_CFG.read_text())
except (OSError, ValueError):
    _DATA = {}
FLEET_MERGE = dict(_DATA.get("merge", {}))
PRIVACY_LOCKED = set(_DATA.get("privacy_locked", []))


def canonicalize(fleet):
    """Return the canonical fleet label for a raw fleet string. Unknown names pass through."""
    if not fleet:
        return fleet
    return FLEET_MERGE.get(fleet, fleet)


def siblings_of(canonical):
    """Return list of raw names that merge into the given canonical label."""
    return sorted([k for k, v in FLEET_MERGE.items() if v == canonical])


def is_privacy_locked(canonical):
    return canonical in PRIVACY_LOCKED
