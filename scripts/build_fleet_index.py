#!/usr/bin/env python3
"""Build data/fleet_index.json from the cct-dash bundle index + our classified corpus.

Each fleet carries:
- canonical: merged fleet label (e.g. "ghost")
- sources: list of raw fleet names merged into canonical
- sessions: sum across sources from bundle index
- cost_usd: sum across sources (historic $ reported by bundle index)
- last_active: max date across sources
- classified: true if we have any classified rows for that canonical
- classified_msgs: # of classified rows attributed to canonical
- privacy_locked: true for [REDACTED]
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fleet_merge import canonicalize, siblings_of, is_privacy_locked, FLEET_MERGE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_INDEX = Path("[REDACTED_PATH]")
TIMELINE_PATH = ROOT / "data" / "timeline.json"
OUT = ROOT / "data" / "fleet_index.json"


def parse_bundle_index(path):
    """Return list of {name, sessions, cost_usd, last_active} from the ## fleets table."""
    lines = Path(path).read_text().splitlines()
    rows = []
    in_fleets = False
    for ln in lines:
        if ln.strip().startswith("## fleets"):
            in_fleets = True
            continue
        if in_fleets and ln.strip().startswith("## "):
            break
        if not in_fleets:
            continue
        m = re.match(r"\|\s*([^|]+?)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|\s*([\d.M k]+)\s*\|\s*([\d\-]+)\s*\|", ln)
        if not m:
            continue
        name = m.group(1).strip()
        if name in ("name", "---"):
            continue
        try:
            sessions = int(m.group(2).replace(",", ""))
            cost_usd = float(m.group(3).replace(",", ""))
        except ValueError:
            continue
        rows.append({
            "name": name,
            "sessions": sessions,
            "cost_usd": cost_usd,
            "last_active": m.group(5).strip(),
        })
    return rows


def live_cct_fleets():
    """Enumerate current cct agents via `cct top --json` and group by fleet.

    Catches opencode / codex / claude sessions that haven't produced classified
    prose yet (e.g. opencode is transcript-less, so prose pipelines never see it).
    Returns a list of {name, sessions, cost_usd, last_active, engines, agents}.
    """
    try:
        out = subprocess.check_output(["cct", "top", "--json"], text=True, timeout=60)
        agents = json.loads(out)
        # Only live/managed; the json dump also includes 38k+ unmanaged.
        agents = [a for a in agents if a.get("kind") in ("live", "dormant") or a.get("state") not in ("unmanaged",)]
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return []
    by_fleet = {}
    for a in agents:
        fleet = (a.get("fleet") or "").strip()
        if not fleet:
            continue
        last_epoch = a.get("last_activity_epoch") or 0
        rec = by_fleet.setdefault(fleet, {
            "name": fleet,
            "sessions": 0,
            "cost_usd": 0.0,
            "last_active_epoch": 0,
            "engines": set(),
            "agents": [],
        })
        rec["sessions"] += 1
        rec["last_active_epoch"] = max(rec["last_active_epoch"], last_epoch)
        if a.get("engine"):
            rec["engines"].add(a["engine"])
        rec["agents"].append(a.get("name", ""))
    rows = []
    for rec in by_fleet.values():
        if rec["last_active_epoch"]:
            la = datetime.fromtimestamp(rec["last_active_epoch"], tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            la = ""
        rows.append({
            "name": rec["name"],
            "sessions": rec["sessions"],
            "cost_usd": 0.0,
            "last_active": la,
            "engines": sorted(rec["engines"]),
            "agents": sorted(rec["agents"]),
        })
    return rows


def classified_coverage(timeline_path):
    """Return {canonical_fleet: msg_count} across our agents."""
    if not timeline_path.exists():
        return {}
    data = json.load(open(timeline_path))
    out = {}
    for a in data.get("agents", []):
        can = canonicalize(a.get("fleet") or "")
        if not can:
            continue
        out[can] = out.get(can, 0) + (a.get("total_turns") or 0)
    return out


def main():
    rows = parse_bundle_index(BUNDLE_INDEX)
    live_rows = live_cct_fleets()
    coverage = classified_coverage(TIMELINE_PATH)

    # Aggregate by canonical. Bundle index + live cct both feed the sessions/cost
    # columns; live cct is the only source for opencode fleets (prose-blind).
    by_canon = {}
    for r in rows + live_rows:
        can = canonicalize(r["name"])
        if can in ("(no fleet)", "(unnamed)"):
            continue
        rec = by_canon.setdefault(can, {
            "canonical": can,
            "sources": [],
            "sessions": 0,
            "cost_usd": 0.0,
            "last_active": "",
            "classified": False,
            "classified_msgs": 0,
            "privacy_locked": is_privacy_locked(can),
            "engines": [],
            "agents": [],
        })
        if r["name"] not in rec["sources"]:
            rec["sources"].append(r["name"])
        rec["sessions"] += r["sessions"]
        rec["cost_usd"] += r["cost_usd"]
        if r["last_active"] > rec["last_active"]:
            rec["last_active"] = r["last_active"]
        for e in r.get("engines", []):
            if e not in rec["engines"]:
                rec["engines"].append(e)
        for a in r.get("agents", []):
            if a not in rec["agents"]:
                rec["agents"].append(a)

    # Classification layer: a canonical is classified if coverage > 0
    for can, msgs in coverage.items():
        rec = by_canon.setdefault(can, {
            "canonical": can, "sources": siblings_of(can) or [can],
            "sessions": 0, "cost_usd": 0.0, "last_active": "",
            "classified": False, "classified_msgs": 0,
            "privacy_locked": is_privacy_locked(can),
        })
        rec["classified"] = True
        rec["classified_msgs"] = msgs

    # Sort: classified first (desc by classified_msgs), then unclassified (desc by cost_usd)
    sorted_fleets = sorted(
        by_canon.values(),
        key=lambda r: (0 if r["classified"] else 1, -r["classified_msgs"], -r["cost_usd"]),
    )

    index = {
        "fleet_merge": FLEET_MERGE,
        "privacy_locked": sorted([f["canonical"] for f in sorted_fleets if f["privacy_locked"]]),
        "fleets": sorted_fleets,
        "stats": {
            "total_canonical": len(sorted_fleets),
            "classified_count": sum(1 for f in sorted_fleets if f["classified"]),
            "unclassified_count": sum(1 for f in sorted_fleets if not f["classified"]),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(index, open(OUT, "w"), indent=2)
    print(f"[done] {OUT}: {index['stats']}", file=sys.stderr)
    for f in sorted_fleets[:15]:
        tag = "CLASS" if f["classified"] else "    -"
        print(f"  {tag} {f['canonical']:<20} sessions={f['sessions']:<5} msgs={f['classified_msgs']:<6} ${f['cost_usd']:.0f}", file=sys.stderr)


if __name__ == "__main__":
    main()
