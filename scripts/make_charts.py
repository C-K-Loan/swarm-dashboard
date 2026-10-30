#!/usr/bin/env python3
"""Generate deck charts from real data.

Outputs PNG files to docs/charts/.
"""
import json
import math
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


OUT = Path("docs/charts")
OUT.mkdir(parents=True, exist_ok=True)

# Shared style — match dashboard dark theme
plt.rcParams.update({
    "figure.facecolor": "#0a0a0f",
    "axes.facecolor": "#15151e",
    "axes.edgecolor": "#2a2a3a",
    "axes.labelcolor": "#e8e8f0",
    "xtick.color": "#8a8aa0",
    "ytick.color": "#8a8aa0",
    "text.color": "#e8e8f0",
    "axes.titlecolor": "#e8e8f0",
    "font.family": "sans-serif",
    "font.size": 11,
    "axes.grid": True,
    "grid.color": "#2a2a3a",
    "grid.linestyle": ":",
})
ACCENT = "#00e8c6"
WARN = "#ffb86c"
ERR = "#ff5c6c"
OK = "#5cff8c"


def pareto_cost_vs_delta():
    """Pareto: cost per 100 messages (log) vs 'frontier-equivalent delta'.
    Based on real v06 numbers from cost_summary.json.
    """
    summary = json.load(open("data/cost_summary.json"))
    ours_per_100 = summary["ours"]["cost_usd"] / summary["msgs"] * 100
    rows = [
        ("Nebius Qwen3-30B-A3B\n(ours · cheap tier)", ours_per_100, ACCENT, "o"),
    ]
    for name, meta in summary["baselines"].items():
        c_per_100 = meta["cost_usd"] / summary["msgs"] * 100
        rows.append((name.replace("-", "\n", 1), c_per_100, WARN if "mini" in name or "haiku" in name else ERR, "s"))

    fig, ax = plt.subplots(figsize=(8, 5))
    for i, (name, cost, color, marker) in enumerate(rows):
        ax.scatter(cost, i, s=180, color=color, marker=marker, zorder=3)
        ax.annotate(name, (cost, i), xytext=(10, 0), textcoords="offset points", fontsize=9, va="center")
    ax.set_xscale("log")
    ax.set_xlabel("Cost per 100 messages (USD, log scale)")
    ax.set_yticks([])
    ax.set_title(f"Observability cost · {summary['msgs']:,} real agent messages",
                 fontsize=12, pad=12, loc="left")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(ours_per_100 * 0.3, max(r[1] for r in rows) * 15)
    plt.tight_layout()
    out = OUT / "cost_pareto.png"
    fig.savefig(out, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"wrote {out}")


def agreement_bars():
    """Per-field agreement across the 3-model hold-out, 200-msg sample."""
    rep = json.load(open("data/holdout/report.json"))
    pairs = list(rep["agreement"].keys())
    fields = ["user_intent", "agent_intent", "agent_role", "blocker_type", "urgency"]

    fig, ax = plt.subplots(figsize=(9, 5))
    bar_w = 0.25
    xs = range(len(fields))

    colors = [ACCENT, WARN, ERR]
    for i, pair in enumerate(pairs):
        vals = [rep["agreement"][pair][f]["agreement_pct"] for f in fields]
        pair_short = pair.replace("Qwen/Qwen3-30B-A3B-Instruct-2507", "Qwen3-30B-A3B").replace("google/gemma-3-27b-it", "Gemma-3-27B").replace("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "Nemotron-Nano")
        ax.bar([x + (i - 1) * bar_w for x in xs], vals, bar_w, label=pair_short, color=colors[i % 3], alpha=0.85)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f.replace("_", "\n") for f in fields])
    ax.set_ylabel("Agreement (%)")
    ax.set_ylim(60, 100)
    ax.axhline(90, color="#2a2a3a", linestyle="--", linewidth=0.8)
    ax.set_title(f"Inter-tier agreement on same 200 messages", fontsize=12, loc="left", pad=12)
    ax.legend(loc="lower right", fontsize=9, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    out = OUT / "agreement_bars.png"
    fig.savefig(out, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"wrote {out}")


def vs_reference_bars():
    """Per-field agreement vs Claude Opus pseudo-truth reference."""
    rep_path = Path("data/holdout/vs_reference.json")
    if not rep_path.exists():
        print(f"[skip] {rep_path} missing")
        return
    rep = json.load(open(rep_path))
    fields = rep.get("fields", ["user_intent", "agent_intent", "agent_role", "blocker_type", "urgency"])
    model_order = [
        ("Qwen/Qwen3-30B-A3B-Instruct-2507", "Fast · Qwen3-30B-A3B"),
        ("google/gemma-3-27b-it", "Balanced · Gemma-3-27B"),
        ("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "Precise · Nemotron Nano"),
    ]
    rows = []
    for k, label in model_order:
        if k in rep["by_classifier"]:
            rows.append((label, rep["by_classifier"][k]["per_field"]))
    if not rows:
        print(f"[skip] no classifier rows matched")
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    bar_w = 0.26
    xs = range(len(fields))
    colors = [ACCENT, WARN, ERR]
    for i, (label, per) in enumerate(rows):
        vals = [per[f]["pct"] or 0 for f in fields]
        ax.bar([x + (i - 1) * bar_w for x in xs], vals, bar_w, label=label, color=colors[i % 3], alpha=0.85)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f.replace("_", "\n") for f in fields])
    ax.set_ylabel("Agreement vs Claude Opus (%)")
    ax.set_ylim(50, 100)
    ax.axhline(90, color="#2a2a3a", linestyle="--", linewidth=0.8)
    ax.set_title("Nebius tiers vs Claude Opus reference, 200 hold-out msgs", fontsize=12, loc="left", pad=12)
    ax.legend(loc="lower right", fontsize=9, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    out = OUT / "vs_reference_bars.png"
    fig.savefig(out, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"wrote {out}")


def main():
    pareto_cost_vs_delta()
    agreement_bars()
    vs_reference_bars()


if __name__ == "__main__":
    main()
