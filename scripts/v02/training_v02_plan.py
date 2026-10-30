#!/usr/bin/env python3
"""V0.2 training/eval refactor dry-run audit.

Reads the active ledger, reviews, turns, and manifest. Prints:
 * ledger size + label-status distribution (provenance audit)
 * reviewed-contiguous-prefix (what export_training.py would emit today)
 * recommended temporal + session-aware splits, with embargo
 * char-length and chars/4 token-count estimates per serialized example
 * which Nebius Token Factory bases support SFT for a given context budget

No API calls. No remote uploads. No training launches. No writes outside
`DATA/training/plan/` (a sub-folder that does not collide with export_training).

Usage:
    python3 scripts/v02/training_v02_plan.py                # stdout summary
    python3 scripts/v02/training_v02_plan.py --json         # machine-readable
    python3 scripts/v02/training_v02_plan.py --write        # also write JSON to disk
    python3 scripts/v02/training_v02_plan.py --max-ctx 8192 # eligibility probe
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from dataset import DATA  # noqa: E402 — local import after path insert
from pipeline import ROOT, read, ledger  # noqa: E402

# Nebius Token Factory supported fine-tune catalog, HISTORICAL probe dated
# 2026-10-23. Treat as candidates to re-verify, not current eligibility. Each
# paid launch must independently confirm the catalog, max context, tokenizer /
# chat template, and whether full-SFT is still beta-gated for the account in
# use. All ctx values below are the 8k reported at probe time.
HISTORICAL_PROBE_DATE = "2026-10-23"
NEBIUS_BASES = {
    "DeepSeek-V3-0324": {"params_b": 671, "ctx": 8192, "full_only": True},
    "Llama-3.2-1B-Instruct": {"params_b": 1, "ctx": 8192},
    "Llama-3.2-3B-Instruct": {"params_b": 3, "ctx": 8192},
    "Llama-3.1-8B-Instruct": {"params_b": 8, "ctx": 8192},
    "Llama-3.1-70B": {"params_b": 70, "ctx": 8192},
    "Llama-3.3-70B-Instruct": {"params_b": 70, "ctx": 8192},
    "gpt-oss-20b": {"params_b": 20, "ctx": 8192},
    "gpt-oss-120b": {"params_b": 120, "ctx": 8192},
    "Qwen3-14B": {"params_b": 14, "ctx": 8192},
    "Qwen3-32B": {"params_b": 32, "ctx": 8192},
}

REFERENCE_TIERS = ("machine_draft", "machine_teacher_reference", "human_approved_gold")


def load_rubric_sha256() -> str:
    import hashlib

    rubric = ROOT / "prompts/v02/extract-turn.txt"
    if not rubric.exists():
        return ""
    return hashlib.sha256(rubric.read_bytes()).hexdigest()


def load_reviews() -> dict:
    p = DATA / "reviews.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        # Latest revision wins.
        prev = out.get(row["turn_id"])
        if prev is None or row.get("revision", 0) >= prev.get("revision", 0):
            out[row["turn_id"]] = row
    return out


def serialize_pair(row: dict, review_patch_or_none: dict | None) -> str:
    """Serialize one chat triple like export_training.py would."""
    patch = review_patch_or_none if review_patch_or_none is not None else row["patch"]
    rubric_path = ROOT / "prompts/v02/extract-turn.txt"
    rubric = rubric_path.read_text() if rubric_path.exists() else "<rubric missing>"
    ctx = row.get("input")
    if ctx is None:
        ctx = {}
    msgs = [
        {"role": "system", "content": rubric},
        {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
        {"role": "assistant", "content": json.dumps(patch, ensure_ascii=False)},
    ]
    return json.dumps({"messages": msgs}, ensure_ascii=False)


def session_of(turn: dict) -> str:
    # Day1 turns carry `session_id`. If a dataset ever omits it, surface
    # "unknown_session" explicitly instead of silently grouping by turn id.
    return turn.get("session_id") or "unknown_session"


def plan(max_ctx_tokens: int) -> dict:
    turns = read("turns.json") or []
    records = ledger()
    reviews = load_reviews()
    manifest = read("manifest.json") or {}
    actors = read("actors.json") or []

    # ---- provenance distribution ----------------------------------------
    tier_counts = Counter()
    model_counts = Counter()
    for r in records:
        tier = r.get("label_status", "machine_draft")
        tier_counts[tier] += 1
        model_counts[r.get("model", "unknown")] += 1
    review_status_counts = Counter(v.get("status") for v in reviews.values())

    # ---- reviewed-contiguous-prefix (same rule as export_training.py) ---
    turn_index = {t["id"]: i for i, t in enumerate(turns)}
    prefix = 0
    stop_turn = None
    for turn, row in zip(turns, records):
        rev = reviews.get(turn["id"])
        if not rev or rev["status"] not in ("approved", "corrected"):
            stop_turn = turn["id"]
            break
        prefix += 1

    # ---- char-length + chars/4 token-estimate per serialized example ----
    sample_lengths = []  # chars for the full {messages:...} JSON line
    sample_turn_ids = []
    for turn, row in zip(turns, records):
        rev = reviews.get(turn["id"])
        correction = (rev or {}).get("correction") if rev else None
        line = serialize_pair(row, correction)
        sample_lengths.append(len(line))
        sample_turn_ids.append(turn["id"])
    char_stats = {}
    if sample_lengths:
        char_stats = {
            "min": min(sample_lengths),
            "max": max(sample_lengths),
            "mean": statistics.mean(sample_lengths),
            "median": statistics.median(sample_lengths),
            "p95": sorted(sample_lengths)[int(len(sample_lengths) * 0.95) - 1]
            if len(sample_lengths) >= 20
            else max(sample_lengths),
        }
        max_idx = sample_lengths.index(max(sample_lengths))
        char_stats["max_turn_id"] = sample_turn_ids[max_idx]

    # Chars/4 token estimate is a weak proxy and is the same unit the eval
    # script uses. Not a provider token count.
    estimated_token_max = int(char_stats.get("max", 0) / 4) if char_stats else 0
    estimated_token_sum = sum(sample_lengths) // 4

    # ---- Nebius base eligibility probe (HISTORICAL, see note at top) ----
    nebius_eligible = {
        "historical_probe_date": HISTORICAL_PROBE_DATE,
        "verify_before_launch": True,
        "note": "Catalog, ctx limits, tokenizer and beta-gating must be "
                "re-verified against current Nebius Token Factory before any "
                "paid launch. These are candidates, not eligibility claims.",
        "candidates": {},
    }
    for name, meta in NEBIUS_BASES.items():
        fits = estimated_token_max <= min(max_ctx_tokens, meta["ctx"])
        nebius_eligible["candidates"][name] = {
            **meta,
            "fits_estimated_max_at_probe_date": fits,
            "mode_at_probe_date": "full-SFT beta-gated" if meta.get("full_only")
                                   else "LoRA (ready) | full-SFT beta-gated",
        }

    # ---- split proposals -------------------------------------------------
    # Within-project temporal (current script's rule).
    holdout = max(1, int(prefix * 0.2)) if prefix >= 10 else 0
    gap = min(2, max(0, prefix - holdout - 1)) if holdout else 0
    split = prefix - holdout - gap

    # Within-project cross-session (hash whole sessions out). Session ids come
    # from the turn record; we group reviewed turns by session.
    reviewed_turns = [t for t in turns[:prefix]]
    session_groups = defaultdict(list)
    for t in reviewed_turns:
        session_groups[session_of(t)].append(t["id"])
    # Greedy: hold out whole sessions until ~20% of reviewed turns are isolated.
    session_order = sorted(session_groups, key=lambda s: -len(session_groups[s]))
    target = max(1, int(prefix * 0.2)) if prefix >= 10 else 0
    held_sessions, held_turns = [], 0
    for s in session_order:
        if held_turns >= target:
            break
        held_sessions.append(s)
        held_turns += len(session_groups[s])

    temporal_split = {
        "basis": "within-project temporal (one gamejam)",
        "train_examples": max(0, split),
        "validation_examples": holdout,
        "embargo_turns": gap,
        "stopped_before": stop_turn,
        "generalization_claim": "none; one gamejam cannot support independent generalization",
    }
    cross_session_split = {
        "basis": "within-project cross-session (one gamejam, held-out sessions)",
        "reviewed_sessions_total": len(session_groups),
        "held_out_sessions": held_sessions,
        "held_out_turn_count": held_turns,
        "train_turn_count": max(0, prefix - held_turns),
        "generalization_claim": "weak; reduces style leakage but same project vocabulary",
    }
    cross_project_split = {
        "basis": "cross-project (future, requires >= 2 project datasets)",
        "available": False,
        "generalization_claim": "the only split that can support an independent-generalization claim",
    }

    return {
        "schema_version": "0.2-plan",
        "dataset_dir": str(DATA),
        "dataset_id": manifest.get("dataset_id"),
        "window_iso": [manifest.get("window_start_iso"), manifest.get("window_end_iso")],
        "turn_count": len(turns),
        "actor_count": len(actors),
        "ledger_rows": len(records),
        "reviewed_rows_latest_revision": len(reviews),
        "review_status_counts": dict(review_status_counts),
        "label_status_counts": dict(tier_counts),
        "model_counts": dict(model_counts),
        "contiguous_reviewed_prefix": prefix,
        "contiguous_prefix_stopped_before": stop_turn,
        "reference_tier_policy": {
            "proposed_tiers": list(REFERENCE_TIERS),
            "current_tier_present_in_ledger": "no (label_status is a flat enum)",
            "action_items": [
                "keep reference_tier in a separate export/run manifest, not the prediction schema",
                "preserve original machine_draft ledger rows",
                "use export_machine_teacher.py for the already-designated Luna Day1 baseline",
                "evaluate.py defaults to human_approved_gold only",
            ],
        },
        "provenance_audit": {
            "models_seen": dict(model_counts),
            "fields_missing_vs_recommendation": [
                "engine", "model_version", "prompt_sha256", "trained_from_adapter_id",
                "effort", "inference_run_id",
            ],
            "rubric_sha256_current": load_rubric_sha256(),
        },
        "splits": {
            "temporal_within_project": temporal_split,
            "cross_session_within_project": cross_session_split,
            "cross_project": cross_project_split,
        },
        "example_length_chars": char_stats,
        "example_tokens_estimate_chars_div_4": {
            "max": estimated_token_max,
            "sum": estimated_token_sum,
            "note": "chars/4 is a weak proxy, not provider token usage",
        },
        "nebius_base_eligibility": nebius_eligible,
        "notes": [
            "No API calls or spend in this report.",
            "export_training.py would emit {train: %d, validation: %d} today."
            % (max(0, split), holdout),
            "Rollout eval is unimplemented; evaluate.py is teacher-forced only.",
            "Full-SFT (lora:false) is beta-gated universally on Nebius; LoRA is the only end-to-end path today.",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    ap.add_argument("--write", action="store_true",
                    help="also write the report under DATA/training/plan/plan.json")
    ap.add_argument("--max-ctx", type=int, default=8192,
                    help="max context tokens assumed for base-eligibility probe")
    args = ap.parse_args()

    report = plan(args.max_ctx)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_summary(report)

    if args.write:
        out_dir = DATA / "training" / "plan"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "plan.json"
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"\n[write] {out_path}", file=sys.stderr)


def print_summary(r: dict):
    def line(k, v):
        print(f"  {k:36s} {v}")

    print("V0.2 training/eval dry-run plan")
    print("=" * 60)
    line("dataset_id", r.get("dataset_id"))
    line("window", " / ".join(x or "?" for x in r.get("window_iso") or []))
    line("turns", r["turn_count"])
    line("ledger_rows", r["ledger_rows"])
    line("reviewed_rows", r["reviewed_rows_latest_revision"])
    line("review_status", r["review_status_counts"])
    line("label_status", r["label_status_counts"])
    line("models_seen", r["model_counts"])
    line("contiguous_reviewed_prefix", r["contiguous_reviewed_prefix"])
    line("stopped_before", r["contiguous_prefix_stopped_before"])
    print()
    print("Splits")
    for name, cfg in r["splits"].items():
        print(f"  {name}:")
        for k, v in cfg.items():
            print(f"    - {k}: {v}")
    print()
    print("Example length (chars)")
    for k, v in (r.get("example_length_chars") or {}).items():
        print(f"  {k}: {v}")
    print()
    print("Token estimate (chars/4)")
    for k, v in r["example_tokens_estimate_chars_div_4"].items():
        print(f"  {k}: {v}")
    print()
    neb = r["nebius_base_eligibility"]
    print(f"Nebius base candidates (HISTORICAL probe {neb['historical_probe_date']}, re-verify before launch)")
    for name, meta in neb["candidates"].items():
        fit = "fit" if meta["fits_estimated_max_at_probe_date"] else "trim-needed"
        print(f"  {name:30s} ({meta['params_b']:>3}B ctx={meta['ctx']})  {fit}  {meta['mode_at_probe_date']}")
    print()
    print("Notes")
    for n in r["notes"]:
        print(f"  * {n}")


if __name__ == "__main__":
    main()
