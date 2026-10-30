"""Offline notification contracts: no real CCT sends or corpus writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import free_cooker as cooker


class NotificationTests(unittest.TestCase):
    def test_detached_notices_keep_explicit_sender_and_literal_message(self):
        text = '[free-cooker] stalled; literal --from text $(do-not-execute)'
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cooker, 'SUPERVISOR', Path(directory)), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(cooker.subprocess, 'run') as run:
            cooker.notify(text, targets=('swarm-dashboard-free-cooker', 'project-manager'))
            self.assertEqual(run.call_count, 2)
            for call, target in zip(run.call_args_list,
                                    ('swarm-dashboard-free-cooker', 'project-manager')):
                argv = call.args[0]
                self.assertEqual(argv, ['cct', 'send', '--from',
                                        'swarm-dashboard-free-cooker', '--', target, text])
                self.assertEqual(argv.count('--from'), 1)
                self.assertEqual(call.kwargs['timeout'], 120)
                self.assertFalse(call.kwargs.get('shell', False))
            rows = (Path(directory) / 'notifications.jsonl').read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])['text'], text)

    def test_transport_failure_does_not_skip_manager_or_lose_local_notice(self):
        for error in (OSError('cct unavailable'), subprocess.TimeoutExpired('cct', 120)):
            with self.subTest(error=type(error).__name__), \
                    tempfile.TemporaryDirectory() as directory, \
                    patch.object(cooker, 'SUPERVISOR', Path(directory)), \
                    patch.object(cooker.subprocess, 'run', side_effect=[error, None]) as run:
                cooker.notify('recheck', targets=('swarm-dashboard-free-cooker', 'project-manager'))
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[1].args[0][-2], 'project-manager')
                self.assertEqual(json.loads((Path(directory) / 'notifications.jsonl').read_text())['text'], 'recheck')


    def test_default_recipient_is_the_cooker_agent_only(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cooker, 'SUPERVISOR', Path(directory)), \
                patch.object(cooker.subprocess, 'run') as run:
            cooker.notify('stall')
            self.assertEqual([c.args[0][-2] for c in run.call_args_list], ['swarm-dashboard-free-cooker'])


if __name__ == '__main__':
    unittest.main()
