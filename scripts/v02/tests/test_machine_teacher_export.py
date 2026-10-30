"""Contract tests for scripts/v02/export_machine_teacher.py.

Temp-data only. No existing dataset is read or written.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pipeline as p  # noqa: E402
import export_machine_teacher as emt  # noqa: E402


def _turn(ordinal: int, *, session: str, actor: str = "actor:a") -> dict:
    tid = f"turn-{ordinal:04d}"
    return {
        "id": tid,
        "ordinal": ordinal,
        "session_id": session,
        "actor_id": actor,
        "actor_name": "Actor A",
        "kind": "assistant_turn",
        "boundary": False,
        "start_ts": 1_000_000 + ordinal,
        "end_ts": 1_000_000 + ordinal + 1,
        "messages": [
            {"id": f"m{ordinal}", "text": f"Work note {ordinal}.", "ts": 1_000_000 + ordinal},
        ],
    }


def _empty_patch(turn_id: str) -> dict:
    return {
        "schema_version": "0.2",
        "turn_id": turn_id,
        "actor_patch": {},
        "actor_evidence": [],
        "tasks": [],
        "decisions": [],
        "relationships": [],
    }


def _pred_row(turn_id: str, generated_at: str = "2026-10-11T00:00:00+00:00") -> dict:
    return {
        "turn_id": turn_id,
        "model": "gpt-6-luna",
        "annotator_kind": "LLM",
        "label_status": "machine_draft",
        "generated_at": generated_at,
        "input": {"project_description": "stub", "current_turn_prose": [{"id": "m1", "text": "x"}]},
        "patch": _empty_patch(turn_id),
    }


class ExportMachineTeacherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        for module in (p, emt):
            mp = patch.object(module, "DATA", self.data)
            mp.start()
            self.addCleanup(mp.stop)

        self.turns = [_turn(i + 1, session=f"sess-{(i % 3) + 1}") for i in range(12)]
        self._write("turns.json", self.turns)
        self._write("actors.json", [{"id": "actor:a", "name": "Actor A"}])
        self._write("manifest.json", {
            "root_turn_id": "turn-0001",
            "teacher_model": "gpt-6-luna",
            "dataset_id": "gamejam-first-24h-v02",
            "window_start_iso": "2026-10-10T00:00:00+00:00",
            "window_end_iso": "2026-10-11T00:00:00+00:00",
        })

    def _write(self, name: str, obj):
        (self.data / name).write_text(json.dumps(obj))

    def _write_jsonl(self, name: str, rows: list[dict]):
        (self.data / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def _seed(self, n: int):
        rows = [_pred_row(self.turns[i]["id"], f"2026-10-11T00:{i:02d}:00+00:00") for i in range(n)]
        self._write_jsonl("predictions.jsonl", rows)
        return rows

    # ---------------- behavior ---------------------------------------------

    def test_incomplete_prefix_rejected_by_default(self):
        self._seed(5)
        with self.assertRaises(SystemExit) as ctx:
            emt.export(allow_incomplete=False, out_dir=self.data / "out")
        self.assertIn("incomplete ledger", str(ctx.exception).lower())

    def test_incomplete_prefix_marked_preview_with_flag(self):
        self._seed(5)
        manifest = emt.export(allow_incomplete=True, out_dir=self.data / "out")
        self.assertTrue(manifest["preview_mode"])
        self.assertEqual(manifest["accepted_pairs"], 5)
        self.assertEqual(manifest["stopped_before"], "turn-0006")
        self.assertFalse(manifest["training_allowed"])

    def test_human_correction_neither_blocks_nor_changes_machine_target(self):
        rows=self._seed(12)
        self._write_jsonl("reviews.jsonl", [{"turn_id":"turn-0001","status":"corrected","correction":{"do_not_use":"human target"},"revision":1}])
        emt.export(out_dir=self.data/"out")
        first=json.loads((self.data/"out/machine_teacher_all.jsonl").read_text().splitlines()[0])
        self.assertEqual(json.loads(first['messages'][2]['content']),rows[0]['patch'])

    def test_excess_rows_or_wrong_teacher_rejected(self):
        rows=self._seed(12)
        self._write_jsonl('predictions.jsonl',rows+[rows[-1]])
        with self.assertRaisesRegex(SystemExit,'longer'):emt.export(out_dir=self.data/'out')
        rows[0]['model']='another-model';self._write_jsonl('predictions.jsonl',rows)
        with self.assertRaisesRegex(SystemExit,'undesignated'):emt.export(out_dir=self.data/'out')

    def test_snapshot_hash_matches_exported_source(self):
        import hashlib
        self._seed(12);m=emt.export(out_dir=self.data/'out')
        self.assertEqual(hashlib.sha256((self.data/'out/ledger.snapshot.jsonl').read_bytes()).hexdigest(),m['source_hashes']['predictions'])

    def test_rejected_only_reviews_do_not_block(self):
        self._seed(12)
        self._write_jsonl("reviews.jsonl", [
            {"turn_id": "turn-0001", "status": "rejected", "revision": 1, "reviewer": "CKL"},
        ])
        manifest = emt.export(allow_incomplete=False, out_dir=self.data / "out")
        self.assertEqual(manifest["accepted_pairs"], 12)

    def test_complete_ledger_produces_split_and_embargo(self):
        self._seed(12)
        out = self.data / "out"
        manifest = emt.export(allow_incomplete=False, out_dir=out)
        self.assertFalse(manifest["preview_mode"])
        self.assertEqual(manifest["train_examples"], 8)
        self.assertEqual(manifest["validation_examples"], 2)
        self.assertEqual(manifest["embargo_turns"], 2)
        self.assertEqual(manifest["reference_tier"], "machine_teacher_reference")
        self.assertFalse(manifest["training_allowed"])

        train_rows = [json.loads(l) for l in (out / "train.jsonl").read_text().splitlines()]
        val_rows = [json.loads(l) for l in (out / "validation.jsonl").read_text().splitlines()]
        all_rows = [json.loads(l) for l in (out / "machine_teacher_all.jsonl").read_text().splitlines()]
        self.assertEqual(len(train_rows), 8)
        self.assertEqual(len(val_rows), 2)
        self.assertEqual(len(all_rows), 12)

        # Chronological order preserved end-to-end.
        self.assertEqual([r["turn_id"] for r in train_rows],
                         [t["id"] for t in self.turns[:8]])
        self.assertEqual([r["turn_id"] for r in val_rows],
                         [t["id"] for t in self.turns[-2:]])
        self.assertEqual([r["turn_id"] for r in all_rows],
                         [t["id"] for t in self.turns])

        # Train/val id arrays carry both ids and timestamps.
        self.assertEqual([x["turn_id"] for x in manifest["train_ids"]],
                         [t["id"] for t in self.turns[:8]])
        self.assertTrue(all(x["generated_at"] for x in manifest["train_ids"]))

    def test_manifest_has_required_hashes_and_provenance(self):
        self._seed(12)
        manifest = emt.export(allow_incomplete=False, out_dir=self.data / "out")
        src = manifest["source_hashes"]
        for k in ("turns", "actors", "manifest", "predictions", "rubric"):
            self.assertIn(k, src)
            self.assertTrue(src[k], f"{k} hash should be non-empty")
        for k in ("train.jsonl", "validation.jsonl", "machine_teacher_all.jsonl"):
            self.assertIn(k, manifest["output_hashes"])

        all_rows = [json.loads(l) for l in
                    (self.data / "out" / "machine_teacher_all.jsonl").read_text().splitlines()]
        prov = all_rows[0]["provenance"]
        self.assertEqual(prov["reference_tier"], "machine_teacher_reference")
        self.assertEqual(prov["model"], "gpt-6-luna")
        self.assertEqual(prov["label_status"], "machine_draft")
        self.assertEqual(prov["ledger_sha256"], src["predictions"])
        self.assertEqual(prov["rubric_sha256"], src["rubric"])

    def test_under_ten_pairs_emits_only_all(self):
        self._write("turns.json", self.turns[:7])
        self._seed(7)
        manifest = emt.export(allow_incomplete=False, out_dir=self.data / "out")
        self.assertEqual(manifest["train_examples"], 0)
        self.assertEqual(manifest["validation_examples"], 0)
        self.assertEqual(manifest["embargo_turns"], 0)
        self.assertEqual(manifest["accepted_pairs"], 7)
        self.assertEqual((self.data / "out" / "train.jsonl").read_text(), "")
        self.assertEqual((self.data / "out" / "validation.jsonl").read_text(), "")
        all_rows = (self.data / "out" / "machine_teacher_all.jsonl").read_text().splitlines()
        self.assertEqual(len(all_rows), 7)

    def test_turn_id_mismatch_fails_fast(self):
        rows = self._seed(12)
        rows[3]["turn_id"] = "turn-9999"
        self._write_jsonl("predictions.jsonl", rows)
        with self.assertRaises(SystemExit) as ctx:
            emt.export(allow_incomplete=False, out_dir=self.data / "out")
        self.assertIn("ledger desync", str(ctx.exception).lower())

    def test_exact_recorded_input_passed_through(self):
        rows = self._seed(12)
        rows[0]["input"] = {"marker": "exact-saved-input-preserved"}
        self._write_jsonl("predictions.jsonl", rows)
        emt.export(allow_incomplete=False, out_dir=self.data / "out")
        first = json.loads((self.data / "out" / "machine_teacher_all.jsonl").read_text().splitlines()[0])
        user_json = json.loads(first["messages"][1]["content"])
        self.assertEqual(user_json, {"marker": "exact-saved-input-preserved"})


if __name__ == "__main__":
    unittest.main()
