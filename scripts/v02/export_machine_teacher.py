#!/usr/bin/env python3
"""Explicit machine_teacher_reference export for the current V0.2 dataset.

CKL has designated the full Luna Day1 run as `machine_teacher_reference`.
This exporter emits a chat-format training set exclusively from the
machine-teacher ledger. It never mixes human-reviewed-gold rows and it never
calls an API, uploads a file, or starts a training run. `training_allowed`
is always false in the manifest.

Default policy:
 * Completed label run required: `len(predictions.jsonl) == len(turns.json)`.
 * Pass `--allow-incomplete-prefix` only for a preview run; the manifest marks
   the output as a preview in that case.
 * Human reviews never replace machine targets; they remain in the separate
   reviewed-gold export. Reviewing a prediction does not block this export.

Output layout (no collision with `export_training.py`'s `DATA/training/`):

    DATA/training/machine_teacher/
      train.jsonl               chronological prefix
      validation.jsonl          chronological suffix (empty if < 10 pairs)
      machine_teacher_all.jsonl all accepted pairs, in order
      sources.lock.json         sha256 of every source file consulted
      manifest.json             tier, split, provenance, embargo, ids

Each exported chat row is `{"messages": [system, user, assistant], "turn_id",
"generated_at", "provenance"}`. `user` is the exact recorded causal input from
the prediction ledger (`row['input']`), never reconstructed. `assistant` is the
exact recorded `patch`. The rubric (`prompts/v02/extract-turn.txt`) is embedded
verbatim as `system`.

Usage:
    python3 scripts/v02/export_machine_teacher.py
    python3 scripts/v02/export_machine_teacher.py --allow-incomplete-prefix
    python3 scripts/v02/export_machine_teacher.py --out /tmp/mt-preview
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pipeline exports ROOT, DATA, and the schema helpers; we rely on its
# deterministic validator + replay. DATA is the active dataset dir.
import pipeline  # noqa: E402
import sender_attribution  # noqa: E402
from dataset import DATA  # noqa: E402

REFERENCE_TIER = "machine_teacher_reference"


# ----------------- helpers --------------------------------------------------

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_path(p: Path) -> str | None:
    return _sha256_bytes(p.read_bytes()) if p.exists() else None


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _rubric_path() -> Path:
    return pipeline.ROOT / "prompts/v02/extract-turn.txt"


def _refuse(msg: str) -> None:
    raise SystemExit(f"export_machine_teacher: {msg}")


# ----------------- core -----------------------------------------------------

def _validate_contiguous_ledger(turns: list[dict], records: list[dict],
                                allow_incomplete: bool) -> tuple[int, str | None]:
    """Replay ledger strictly in turn order; return (prefix_len, stopped_before)."""
    if not records:
        _refuse("predictions.jsonl is empty; nothing to export.")
    if len(records)>len(turns):
        _refuse("ledger is longer than frozen source")
    if any(r.get('model')!='gpt-6-luna' or r.get('annotator_kind')!='LLM' or r.get('label_status')!='machine_draft' for r in records):
        _refuse("ledger contains an undesignated teacher or non-machine label")
    graph = pipeline.initial()
    stopped_before = None
    for idx, (turn, row) in enumerate(zip(turns, records)):
        if row.get("turn_id") != turn["id"]:
            _refuse(
                f"ledger desync at index {idx}: ledger turn_id={row.get('turn_id')!r} "
                f"vs frozen turn id={turn['id']!r}"
            )
        patch = row.get("patch")
        if not isinstance(patch, dict):
            _refuse(f"row {idx} missing patch dict")
        try:
            pipeline.check(patch, turn, graph)
        except Exception as exc:
            _refuse(f"schema/replay check failed at {turn['id']}: {exc}")
        graph, _ = pipeline.apply(graph, patch, turn)
    prefix_len = min(len(records), len(turns))
    if prefix_len < len(turns):
        stopped_before = turns[prefix_len]["id"]
        if not allow_incomplete:
            _refuse(
                f"incomplete ledger: {prefix_len}/{len(turns)} turns, stopped before "
                f"{stopped_before}. Pass --allow-incomplete-prefix for a preview run."
            )
    return prefix_len, stopped_before


def _rubric_sha_and_text() -> tuple[str, str]:
    p = _rubric_path()
    if not p.exists():
        _refuse(f"rubric missing at {p}")
    body = p.read_bytes()
    return _sha256_bytes(body), body.decode("utf-8")


def _build_pair(row: dict, rubric_text: str, source_hashes: dict) -> dict:
    """Serialize a single machine-teacher chat example with provenance."""
    user_content = row.get("input")
    if not isinstance(user_content, dict):
        _refuse(f"row {row.get('turn_id')!r} has no recorded causal input")
    assistant_patch = row["patch"]
    provenance = {
        "reference_tier": REFERENCE_TIER,
        "model": row.get("model"),
        "annotator_kind": row.get("annotator_kind"),
        "label_status": row.get("label_status"),
        "generated_at": row.get("generated_at"),
        "inference_provenance": row.get("inference_provenance"),
        "ledger_sha256": source_hashes["predictions"],
        "turns_sha256": source_hashes["turns"],
        "rubric_sha256": source_hashes["rubric"],
    }
    return {
        "turn_id": row["turn_id"],
        "generated_at": row.get("generated_at"),
        "provenance": provenance,
        "messages": [
            {"role": "system", "content": rubric_text},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(assistant_patch, ensure_ascii=False)},
        ],
    }


def _chronological_split(pairs: list[dict]) -> tuple[list[dict], list[dict], int]:
    """Match export_training.py's rule: 80/20 with a 2-turn embargo at >= 10 pairs."""
    n = len(pairs)
    holdout = max(1, int(n * 0.2)) if n >= 10 else 0
    gap = min(2, max(0, n - holdout - 1)) if holdout else 0
    split = n - holdout - gap
    train = pairs[:split] if holdout else []
    validation = pairs[-holdout:] if holdout else []
    return train, validation, gap


def _write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(serialized)
    return _sha256_bytes(serialized.encode("utf-8"))


def export(allow_incomplete: bool = False, out_dir: Path | None = None) -> dict:
    turns = pipeline.read("turns.json") or []
    actors = pipeline.read("actors.json") or []
    manifest_src = pipeline.read("manifest.json") or {}
    # Capture once: background labeling may append while this preview exports.
    with (DATA/'.writer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_SH)
        ledger_bytes=(DATA/'predictions.jsonl').read_bytes() if (DATA/'predictions.jsonl').exists() else b''
    records=[json.loads(line) for line in ledger_bytes.splitlines() if line.strip()]
    if manifest_src.get('dataset_id')!='gamejam-first-24h-v02' or manifest_src.get('teacher_model')!='gpt-6-luna':
        _refuse("only the designated Luna Day1 machine baseline is authorized")
    if not turns:
        _refuse(f"turns.json empty or missing at {DATA/'turns.json'}")

    prefix_len, stopped_before = _validate_contiguous_ledger(turns, records, allow_incomplete)

    source_hashes = {
        "turns": _sha256_path(DATA / "turns.json") or "",
        "actors": _sha256_path(DATA / "actors.json") or "",
        "manifest": _sha256_path(DATA / "manifest.json") or "",
        "predictions": _sha256_bytes(ledger_bytes),
    }
    rubric_sha, rubric_text = _rubric_sha_and_text()
    source_hashes["rubric"] = rubric_sha

    accepted_records = records[:prefix_len]
    pairs = [_build_pair(row, rubric_text, source_hashes) for row in accepted_records]
    # Recorded inputs stay as the teacher saw them; turns the indexer wrongly called "human" are flagged.
    senders = sender_attribution.overlay_for(DATA.name)
    for pair in pairs:
        if pair["turn_id"] in senders:
            pair["provenance"]["sender_attribution"] = senders[pair["turn_id"]]

    train, validation, embargo = _chronological_split(pairs)

    out = out_dir or (DATA / "training" / "machine_teacher")
    out.mkdir(parents=True, exist_ok=True)
    (out/"ledger.snapshot.jsonl").write_bytes(ledger_bytes)

    train_sha = _write_jsonl(out / "train.jsonl", train)
    val_sha = _write_jsonl(out / "validation.jsonl", validation)
    all_sha = _write_jsonl(out / "machine_teacher_all.jsonl", pairs)

    (out / "sources.lock.json").write_text(json.dumps({
        "dataset_dir": str(DATA),
        "source_hashes": source_hashes,
        "turn_count": len(turns),
        "actor_count": len(actors),
        "ledger_rows": len(records),
        "prefix_length": prefix_len,
        "preview_mode": prefix_len < len(turns),
        "stopped_before": stopped_before,
    }, indent=2) + "\n")

    by_id={t["id"]:t for t in turns}
    manifest = {
        "schema_version": "0.2",
        "export_kind": "machine_teacher",
        "reference_tier": REFERENCE_TIER,
        "dataset_id": manifest_src.get("dataset_id"),
        "teacher_model": manifest_src.get("teacher_model"),
        "window_start_iso": manifest_src.get("window_start_iso"),
        "window_end_iso": manifest_src.get("window_end_iso"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preview_mode": prefix_len < len(turns),
        "stopped_before": stopped_before,
        "turn_count": len(turns),
        "accepted_pairs": len(pairs),
        "train_examples": len(train),
        "validation_examples": len(validation),
        "embargo_turns": embargo,
        "train_ids": [
            {"turn_id": r["turn_id"], "generated_at": r["generated_at"], "source_start_ts":by_id[r["turn_id"]]["start_ts"], "source_end_ts":by_id[r["turn_id"]]["end_ts"]} for r in train
        ],
        "validation_ids": [
            {"turn_id": r["turn_id"], "generated_at": r["generated_at"], "source_start_ts":by_id[r["turn_id"]]["start_ts"], "source_end_ts":by_id[r["turn_id"]]["end_ts"]} for r in validation
        ],
        "source_hashes": source_hashes,
        "output_hashes": {
            "train.jsonl": train_sha,
            "validation.jsonl": val_sha,
            "machine_teacher_all.jsonl": all_sha,
        },
        "sender_attribution": {
            "classifier": "ui/src/lib/transcript-roles.mjs",
            "reattributed_pairs": sum("sender_attribution" in p["provenance"] for p in pairs),
            "note": "Input recorded actor 'human' for these turns, but CKL only types in gamejam-master; provenance.sender_attribution names the real sender.",
        },
        "human_review_policy": "Human reviews remain separate and do not replace machine targets, including rejected drafts; this export is teacher imitation only.",
        "split_basis": "within-project temporal; exact recorded teacher inputs; two-turn gap is not proof of dependency isolation",
        "training_allowed": False,
        "reason": "Export only. Paid APIs, uploads and training launches are disabled by this script.",
        "eval_note": (
            "A machine-teacher evaluation of this export may report agreement with the "
            "teacher. It is not a factual-quality or human-gold score; it only measures "
            "how closely a trained model mimics the teacher's labels."
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


# ----------------- CLI ------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-incomplete-prefix", action="store_true",
                    help="allow preview over an incomplete ledger (manifest marks preview)")
    ap.add_argument("--out", type=Path, default=None,
                    help="override output directory (default: DATA/training/machine_teacher)")
    ap.add_argument("--stdout", action="store_true", help="also print the manifest JSON")
    args = ap.parse_args()
    manifest = export(allow_incomplete=args.allow_incomplete_prefix, out_dir=args.out)
    if args.stdout:
        print(json.dumps(manifest, indent=2))
    else:
        print(f"[write] {(args.out or (DATA / 'training' / 'machine_teacher'))}/manifest.json")
        print(f"  accepted_pairs={manifest['accepted_pairs']}  "
              f"train={manifest['train_examples']}  "
              f"val={manifest['validation_examples']}  "
              f"embargo={manifest['embargo_turns']}  "
              f"preview={manifest['preview_mode']}")


if __name__ == "__main__":
    main()
