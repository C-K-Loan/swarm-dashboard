#!/usr/bin/env python3
"""
Augment data/training_summary.json's sft_data[*].per_field entries with full
label distributions (counted from the SFT jsonl assistant responses).

Each per_field entry gains:
  "distribution": [{"label": "report-progress", "count": 2731, "pct": 99.6}, ...]
Ordered by count descending. Null labels appear as "None".
"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "data" / "training_summary.json"
SFT_DIR = ROOT / "data" / "sft"
FIELDS = ["user_intent", "agent_intent", "agent_role", "blocker_type", "urgency"]


def count_labels(path: Path) -> dict:
    """Return {field: Counter(label -> count)} from an SFT jsonl."""
    counters = {f: Counter() for f in FIELDS}
    with open(path) as fh:
        for line in fh:
            try:
                row = json.loads(line)
                asst = row["messages"][-1]["content"]
                parsed = json.loads(asst)
            except Exception:
                continue
            for f in FIELDS:
                v = parsed.get(f)
                label = "None" if v is None else str(v)
                counters[f][label] += 1
    return counters


def main() -> None:
    summary = json.loads(SUMMARY.read_text())
    for name, entry in summary.get("sft_data", {}).items():
        src = SFT_DIR / name
        if not src.exists():
            print(f"skip {name}: file missing")
            continue
        counters = count_labels(src)
        for f in FIELDS:
            c = counters[f]
            total = sum(c.values())
            if not total:
                continue
            dist = [
                {"label": lbl, "count": n, "pct": round(100 * n / total, 1)}
                for lbl, n in c.most_common()
            ]
            entry.setdefault("per_field", {}).setdefault(f, {})["distribution"] = dist
        print(f"ok  {name}: {sum(sum(c.values()) for c in counters.values())} labels across {len(FIELDS)} fields")
    SUMMARY.write_text(json.dumps(summary, indent=2))
    print(f"wrote {SUMMARY}")


if __name__ == "__main__":
    main()
