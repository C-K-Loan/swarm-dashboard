"""Offline regression coverage: evidence retention, refreshes, index, spend admission."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import summary_contract as contract
import summarize_buckets as live
import summarize_prose_bundles as evaluation
import build_bucket_summary_index as indexer


class SummaryTests(unittest.TestCase):
    def test_context_retains_completion_after_long_window(self):
        msgs = [(n, 'assistant', f'turn-{n}: ' + 'x' * 1800) for n in range(100)]
        msgs[-1] = (99, 'assistant', 'Finished root-cause fix; regression checks passed.')
        body = contract.build_body(msgs)
        self.assertLessEqual(len(body), 6000)
        self.assertIn('turn-0:', body)
        self.assertIn('Finished root-cause fix', body)
        self.assertIn('messages omitted', body)

    def test_long_turn_retains_ending_and_user_role(self):
        body = contract.build_body([(1, 'u', 'Please investigate. ' + 'x' * 5000 + ' DO NOT CLAIM COMPLETION')])
        self.assertIn('[USER 1]', body)
        self.assertTrue(body.endswith('DO NOT CLAIM COMPLETION'))
        self.assertEqual(contract.build_body([(1, 'u', 'x' * 9000)], 0), '')

    def test_live_eval_identical_evidence_and_prompt(self):
        msgs = [{'ts': 1, 'role': 'user', 'text': 'Fix it'}, {'ts': 2, 'role': 'assistant', 'text': 'Investigating'}]
        body = contract.build_body([(m['ts'], m['role'], m['text']) for m in msgs])
        self.assertEqual(evaluation.build_body(msgs), body)
        expected = contract.PROMPT.format(AGENT='a', PROJECT='p', WINDOW='w', BODY=body)
        actual = evaluation.PROMPT.replace('@@AGENT@@', 'a').replace('@@PROJECT@@', 'p').replace('@@WINDOW@@', 'w').replace('@@BODY@@', body)
        self.assertEqual(actual, expected)

    def test_parser_rejects_malformed_shapes(self):
        for text in ('[]', '{}', '{"finished":"yes"}', '{"started":[3]}', None, '{"finished":['):
            self.assertTrue(contract.parse_summary(text).get('_parse_error'), text)

    def test_parser_caps_deduplicates_and_keeps_empty_sections(self):
        summary = contract.parse_summary(json.dumps({'started': [' did  work ', 'DID WORK'] + [f'task {n}' for n in range(8)]}))
        self.assertEqual(len(summary['started']), 5)
        self.assertEqual(summary['started'][0], 'did work')
        self.assertEqual(summary['finished'], [])
        self.assertEqual(evaluation._parse(json.dumps(summary)), (summary, False, ''))

    def test_refresh_detects_new_changed_failed_and_grain(self):
        bucket = ('s', 0, {}, [(1, 'assistant', 'done')])
        row = {'msg_count': 1, 'summary': {'finished': ['done']}}
        self.assertFalse(live.needs_refresh(bucket, row, 3600))
        for previous in (None, {**row, 'msg_count': 2}, {**row, 'summary': {'_parse_error': True}}, {**row, 'grain_sec': 60}):
            self.assertTrue(live.needs_refresh(bucket, previous, 3600))
        row['source_fingerprint'] = live.source_fingerprint(bucket[3])
        self.assertFalse(live.needs_refresh(bucket, row, 3600))
        failed = {**row, 'summary_version': contract.SUMMARY_VERSION, 'summary': {'_parse_error': True}}
        self.assertFalse(live.needs_refresh(bucket, failed, 3600))
        self.assertTrue(live.needs_refresh((*bucket[:3], [(1, 'assistant', 'changed')]), row, 3600))

    def test_index_empty_refresh_removes_old_but_parse_failure_keeps_good(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/'in.jsonl', Path(tmp)/'out.json'
            rows = [{'sid': s, 'bucket_start': 0, 'summary': summary} for s, summary in [
                ('a', {'finished': ['old']}), ('b', {'finished': ['good']}),
                ('a', {'finished': []}), ('b', {'_parse_error': True})]]
            src.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            with patch.object(indexer, 'SRC', src), patch.object(indexer, 'OUT', dst), contextlib.redirect_stdout(io.StringIO()):
                indexer.main()
            self.assertEqual(list(json.loads(dst.read_text())), ['b|0'])

    def test_scheduler_reserves_budget_before_submitting(self):
        buckets = [('s', n, {}, [(n, 'assistant', 'done')]) for n in range(100)]
        with tempfile.TemporaryDirectory() as tmp:
            # Each request reserves the entire one-cent budget; failed requests retain it.
            with patch.object(sys, 'argv', ['summary', '--budget-cents', '1', '--workers', '10', '--out', str(Path(tmp)/'out')]), \
                 patch.object(live, 'enumerate_buckets', return_value=iter(buckets)), \
                 patch.object(live, 'request_reservation', return_value=.01), \
                 patch.object(live, 'summarize_one', return_value={'_error': 'timeout'}) as call, \
                 contextlib.redirect_stderr(io.StringIO()):
                live.main()
                self.assertEqual(call.call_count, 1)
            with patch.object(sys, 'argv', ['summary', '--budget-cents', '0', '--out', str(Path(tmp)/'zero')]), \
                 patch.object(live, 'enumerate_buckets', return_value=iter(buckets)), \
                 patch.object(live, 'summarize_one') as call, contextlib.redirect_stderr(io.StringIO()):
                live.main()
                call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
