"""Focused identity and resolver integration checks; no live data is changed."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("membership", Path(__file__).resolve().parents[2] / "scripts/build_timeline_hierarchy.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class MembershipTests(unittest.TestCase):
    def test_uuid_only_join_never_reuses_historical_name(self):
        agents = [{"id": "old", "title": "same-name", "fleet": "old"}, {"id": "current", "title": "same-name", "fleet": "old"}]
        roster = [{"name": "same-name", "mapping_uuid": "current", "fleet": "new", "tags": ["org:Explicit"]}]
        def resolve(row):
            return ("Explicit", "tag", "detail") if row.get("tags") else ("Historical", "fleet", "detail")
        mapping, sources = module.build_membership(agents, roster, resolve)
        self.assertEqual(mapping, {"old": "Historical", "current": "Explicit"})
        self.assertEqual(sources["old"]["match"], "historical metadata")
        self.assertNotIn("detail", sources["current"])

    def test_conflicting_uuid_records_use_historical_metadata(self):
        def resolve(row):
            return row["org"], "project", "private detail"
        mapping, sources = module.build_membership([{"id": "x", "project": "p"}], [], lambda row: ("Fallback", "default", "private"))
        self.assertEqual(mapping["x"], "Fallback")
        agents = [{"id": "x", "org": "Historical"}]
        # Historical metadata explicitly avoids inheriting any unrelated roster fields.
        roster = [{"mapping_uuid": "x", "org": "A"}, {"mapping_uuid": "x", "org": "B"}]
        mapping, sources = module.build_membership(agents, roster, lambda row: (row.get("org", "Fallback"), "default", "private"))
        self.assertEqual(mapping["x"], "Fallback")
        self.assertIn("conflicting", sources["x"]["match"])

    def test_disk_fallback_reads_only_json_and_tolerates_incomplete_files(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "good.json").write_text('{"uuid":"sid","tmux_name":"name","tags":["org:CKL"]}')
            Path(directory, "partial.json").write_text('{')
            rows = module.disk_roster(directory)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["mapping_uuid"], "sid")

    def test_authoritative_cct_resolver_precedence_read_only(self):
        app = Path("../other-project/dash/app.py")
        if not app.exists():
            self.skipTest("CCT source is local-only")
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory, "seed.json")
            seed.write_text('{"projects":[{"name":"fixture-project","path":"/fixture/project","org":"CKL","fleet":null}]}')
            resolver, orgs, origin, revision = module.load_resolver(app, Path(directory, "missing.sqlite"), seed)
            self.assertEqual(resolver({"tags":["org:OtherOrg"],"project":"fixture-project","fleet":"other-project"})[:2], ("OtherOrg", "tag"))
            self.assertEqual(resolver({"project":"fixture-project","fleet":"other-project"})[:2], ("CKL", "project"))
            self.assertEqual(resolver({"fleet":"other-project"})[:2], ("other-project", "fleet"))
            self.assertEqual(resolver({})[:2], ("CKL", "default"))
            self.assertFalse(Path(directory, "missing.sqlite").exists())
            self.assertEqual(origin, "CCT org seed fallback")


if __name__ == "__main__":
    unittest.main()
