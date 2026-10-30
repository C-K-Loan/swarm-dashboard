#!/usr/bin/env python3
"""Score each candidate model against gold_v2.jsonl.

Gold (truth, not scored): data/eval/gold_v2.jsonl (labelled by Claude Opus 5.5)

Candidates discovered automatically: every data/eval/ref_<slug>.jsonl file with
1000 rows aligned 1:1 to gold.

Metrics computed per model:
  per field:
    accuracy, macro_f1
  overall:
    alignment_rate   = fraction of rows where ALL applicable fields match truth
    agent_role_jaccard (multi-label average Jaccard, agent rows only)
  per-row cost:
    cost_per_1k_msgs = PRICING × average per-row (in,out) × 1000

Agent-schema fields: user_intent, agent_intent, agent_role, blocker_type, urgency, task_phase
User-schema fields:  user_intent, urgency, addresses

Outputs:
  data/eval/compare_v2.json          — structured metrics
  docs/eval/compare_report.md        — human table
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(".")
EVAL = ROOT / "data/eval"
GOLD = EVAL / "gold_v2.jsonl"
OUT_JSON = EVAL / "compare_v2.json"
OUT_MD = ROOT / "docs/eval/compare_report.md"

AGENT_FIELDS = ("user_intent", "agent_intent", "agent_role", "blocker_type", "urgency", "task_phase")
USER_FIELDS = ("user_intent", "urgency", "addresses")

# Pricing (USD per 1M tokens, in/out). Keep in sync with classify_bundles.py.
PRICING = {
    "qwen_30b":       (0.10, 0.30),
    "gemma_27b":      (0.10, 0.30),
    "nemotron_30b":   (0.06, 0.24),
    "qwen_235b":      (0.20, 0.60),
    "deepseek_flash": (0.10, 0.40),
    "qwen_sft":       (0.08, 0.24),  # placeholder for future SFT candidate (Qwen3.5-4B-SFT)
    # legacy anthropic refs (parked, appendix only)
    "haiku":  (0.80, 4.00),
    "sonnet": (3.00, 15.00),
    "fable":  (1.00, 5.00),
}

PRIMARY_SLUGS = ("qwen_30b", "gemma_27b", "nemotron_30b", "qwen_235b", "deepseek_flash", "qwen_sft")
APPENDIX_SLUGS = ("haiku", "sonnet", "fable")


def load_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(l) for l in f if l.strip()]


def norm_role_list(v):
    """agent_role is List[str] or null → canonical sorted tuple."""
    if v is None:
        return None
    if isinstance(v, str):
        return (v,)
    if isinstance(v, list):
        return tuple(sorted(set(x for x in v if x is not None)))
    return None


def jaccard(a, b):
    """Jaccard index of two role lists (empty vs empty = 1.0)."""
    sa = set(a or [])
    sb = set(b or [])
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0


def per_field_f1_accuracy(gold_vals, pred_vals):
    """Compute (accuracy, macro_f1) for a single-label field.
    None counts as a label. Macro F1 averages per-class F1 across unique classes."""
    assert len(gold_vals) == len(pred_vals)
    classes = set(gold_vals) | set(pred_vals)
    classes.discard(None)
    # Accuracy on all rows
    correct = sum(1 for g, p in zip(gold_vals, pred_vals) if g == p)
    acc = correct / len(gold_vals) if gold_vals else 0.0
    # Macro F1: per-class precision/recall averaged
    f1s = []
    for c in classes:
        tp = sum(1 for g, p in zip(gold_vals, pred_vals) if g == c and p == c)
        fp = sum(1 for g, p in zip(gold_vals, pred_vals) if g != c and p == c)
        fn = sum(1 for g, p in zip(gold_vals, pred_vals) if g == c and p != c)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) else 0.0
        f1s.append(f1)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    return acc, macro_f1


def align_all(gold_row, pred_row, is_user_row):
    """Return True if every applicable field matches between gold and pred."""
    fields = USER_FIELDS if is_user_row else AGENT_FIELDS
    for k in fields:
        g = gold_row.get(k)
        p = pred_row.get(k)
        if k == "agent_role":
            if norm_role_list(g) != norm_role_list(p):
                return False
        else:
            if g != p:
                return False
    return True


def score_candidate(slug: str, gold_rows: list[dict]) -> dict | None:
    path = EVAL / f"ref_{slug}.jsonl"
    if not path.exists():
        return None
    pred_rows = load_jsonl(path)
    if len(pred_rows) != len(gold_rows):
        print(f"[warn] {slug}: {len(pred_rows)} pred vs {len(gold_rows)} gold — truncating to min", file=sys.stderr)
        n = min(len(pred_rows), len(gold_rows))
        pred_rows = pred_rows[:n]
        gold_rows_local = gold_rows[:n]
    else:
        gold_rows_local = gold_rows

    is_user_mask = [str(r.get("_bundle", "")).startswith(("user_", "train_user_")) for r in gold_rows_local]
    agent_mask = [not u for u in is_user_mask]

    # Alignment Rate
    aligned = sum(1 for g, p, u in zip(gold_rows_local, pred_rows, is_user_mask) if align_all(g, p, u))
    alignment_rate = aligned / len(gold_rows_local) if gold_rows_local else 0.0

    # Per-field accuracy + Macro F1
    per_field = {}
    for field in sorted(set(AGENT_FIELDS) | set(USER_FIELDS)):
        if field == "agent_role":
            # multi-label jaccard (agent rows only)
            vals = [(norm_role_list(g.get(field)), norm_role_list(p.get(field)))
                    for g, p, u in zip(gold_rows_local, pred_rows, is_user_mask) if not u]
            if vals:
                avg_j = sum(jaccard(g, p) for g, p in vals) / len(vals)
                exact = sum(1 for g, p in vals if g == p) / len(vals)
                per_field[field] = {"jaccard": round(avg_j, 4), "exact_match": round(exact, 4), "n": len(vals)}
            continue
        # Field applies to agent-only, user-only, or both?
        if field in AGENT_FIELDS and field in USER_FIELDS:
            scope_mask = [True] * len(gold_rows_local)
        elif field in AGENT_FIELDS:
            scope_mask = agent_mask
        else:
            scope_mask = is_user_mask
        gvals = [g.get(field) for g, m in zip(gold_rows_local, scope_mask) if m]
        pvals = [p.get(field) for p, m in zip(pred_rows, scope_mask) if m]
        if not gvals:
            continue
        acc, mf1 = per_field_f1_accuracy(gvals, pvals)
        per_field[field] = {"accuracy": round(acc, 4), "macro_f1": round(mf1, 4), "n": len(gvals)}

    # Cost: sum per-row usage × PRICING; fallback: estimate from gold text length.
    in_tok = sum(p.get("_in_tok", 0) for p in pred_rows)
    out_tok = sum(p.get("_out_tok", 0) for p in pred_rows)
    if in_tok == 0:
        # Fallback: estimate 600 in + 60 out per row (prompts are ~2.4k chars ≈ 600 tokens)
        in_tok = 600 * len(pred_rows)
        out_tok = 60 * len(pred_rows)
    pin, pout = PRICING.get(slug, (0.30, 0.60))
    total_cost = in_tok / 1_000_000 * pin + out_tok / 1_000_000 * pout
    per_row_cost = total_cost / max(1, len(pred_rows))
    cost_per_1k = per_row_cost * 1000

    api_errs = sum(1 for p in pred_rows if p.get("_api_error"))
    parse_errs = sum(1 for p in pred_rows if p.get("_parse_error"))

    return {
        "slug": slug,
        "n_rows": len(pred_rows),
        "n_agent": sum(agent_mask),
        "n_user": sum(is_user_mask),
        "alignment_rate": round(alignment_rate, 4),
        "per_field": per_field,
        "api_errors": api_errs,
        "parse_errors": parse_errs,
        "cost_total_usd": round(total_cost, 6),
        "cost_per_1k_msgs_usd": round(cost_per_1k, 6),
        "in_tok": in_tok,
        "out_tok": out_tok,
        "pricing_in_per_1m": pin,
        "pricing_out_per_1m": pout,
    }


def write_md(results: dict) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    primary = [r for r in results["primary"] if r is not None]
    appendix = [r for r in results["appendix"] if r is not None]
    lines = []
    lines.append("# Classifier comparison (v0.2) — gold = Claude Opus 5.5\n")
    lines.append(f"Generated: {results['generated_at']} · gold rows: {results['n_gold']}\n\n")

    lines.append("## Headline\n")
    lines.append("| model | alignment rate | agent_role jaccard | errors | cost/1K msgs |\n")
    lines.append("|---|---:|---:|---:|---:|\n")
    for r in primary:
        aj = r['per_field'].get('agent_role', {}).get('jaccard', 0)
        lines.append(f"| {r['slug']} | {r['alignment_rate']*100:.1f}% | {aj:.3f} | {r['api_errors']+r['parse_errors']} | ${r['cost_per_1k_msgs_usd']:.4f} |\n")
    lines.append("\n")

    lines.append("## Per-field macro F1\n")
    fields_order = ("user_intent", "agent_intent", "blocker_type", "urgency", "task_phase", "addresses")
    header = "| model | " + " | ".join(fields_order) + " |\n"
    sep = "|---|" + "|".join(["---:"] * len(fields_order)) + "|\n"
    lines.append(header); lines.append(sep)
    for r in primary:
        row = [r['slug']]
        for f in fields_order:
            v = r['per_field'].get(f, {}).get('macro_f1')
            row.append(f"{v:.3f}" if v is not None else "—")
        lines.append("| " + " | ".join(row) + " |\n")

    if appendix:
        lines.append("\n## Appendix: Anthropic refs (parked, local-only)\n")
        lines.append("| model | alignment rate | agent_role jaccard | cost/1K msgs |\n")
        lines.append("|---|---:|---:|---:|\n")
        for r in appendix:
            aj = r['per_field'].get('agent_role', {}).get('jaccard', 0)
            lines.append(f"| {r['slug']} | {r['alignment_rate']*100:.1f}% | {aj:.3f} | ${r['cost_per_1k_msgs_usd']:.4f} |\n")
    OUT_MD.write_text("".join(lines))
    print(f"[write] {OUT_MD}", file=sys.stderr)


def main():
    import datetime
    if not GOLD.exists():
        print(f"[ERR] no gold at {GOLD}", file=sys.stderr); sys.exit(2)
    gold = load_jsonl(GOLD)
    print(f"[gold] {len(gold)} rows", file=sys.stderr)

    primary = []
    for slug in PRIMARY_SLUGS:
        r = score_candidate(slug, gold)
        if r is None:
            print(f"[skip] {slug} (no ref file yet)", file=sys.stderr)
            primary.append(None)
        else:
            primary.append(r)
            print(f"[ok] {slug}: alignment={r['alignment_rate']*100:.1f}%  jaccard(role)={r['per_field'].get('agent_role',{}).get('jaccard',0):.3f}  errs={r['api_errors']+r['parse_errors']}", file=sys.stderr)
    appendix = []
    for slug in APPENDIX_SLUGS:
        r = score_candidate(slug, gold)
        if r is not None:
            appendix.append(r)
            print(f"[appendix] {slug}: alignment={r['alignment_rate']*100:.1f}%", file=sys.stderr)

    out = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "schema_version": "v0.2",
        "gold_source": "Claude Opus 5.5",
        "n_gold": len(gold),
        "primary": primary,
        "appendix": appendix,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2))
    print(f"[write] {OUT_JSON}", file=sys.stderr)
    write_md(out)
    print("DONE", file=sys.stderr)


if __name__ == "__main__":
    main()
