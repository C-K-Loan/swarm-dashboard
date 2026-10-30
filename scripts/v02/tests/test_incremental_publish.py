"""Publication migration/recovery contracts. Temp fixtures only, no inference."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline as p
import backfill_turn_states as migration


class IncrementalPublishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        override = patch.object(p, 'DATA', self.data)
        override.start()
        self.addCleanup(override.stop)
        self.turns = [{'id': f'turn-{i:04d}', 'ordinal': i, 'actor_id': 'actor:a',
                       'session_id': 'session:a', 'actor_name': 'A', 'start_ts': i, 'end_ts': i + .5,
                       'messages': [{'id': f'm{i}', 'text': 'Build UI.', 'ts': i}]}
                      for i in range(1, 4)]
        for name, value in [('actors.json', [{'id': 'actor:a', 'name': 'A'}]),
                            ('turns.json', self.turns),
                            ('manifest.json', {'root_turn_id': 'turn-0001', 'teacher_model': 'test'})]:
            p.atomic(self.data / name, value)
        self.rows = [{'turn_id': t['id'], 'generated_at': 'timestamp',
                      'input': {'marker': t['id'], 'project_description': 'Exact recorded input'},
                      'patch': {'schema_version': '0.2', 'turn_id': t['id'], 'actor_patch': {},
                                'actor_evidence': [], 'tasks': [], 'decisions': [], 'relationships': []}}
                     for t in self.turns]
        self.rows[0]['patch']['tasks'] = [{'ref': 'new:a', 'resolution': 'new',
            'changes': {'title': 'Build UI', 'summary': 'Build UI', 'status': 'progressing',
                        'importance': 'high', 'complexity': 'low', 'blocker': None},
            'evidence': [{'message_id': 'm1', 'quote': 'Build UI.'}]}]
        tid = p.uid('task', 'turn-0001', 'new:a')
        self.rows[1]['patch']['tasks'] = [{'ref': tid, 'resolution': 'existing',
            'changes': {'status': 'done'}, 'evidence': [{'message_id': 'm2', 'quote': 'Build UI.'}]}]

    def saved(self, i):
        return self.data / 'turn-states' / (self.turns[i]['id'] + '.json')

    def test_append_only_writes_new_turn_and_replays_only_new_patch(self):
        p.publish(self.rows[:1])
        first = self.saved(0).read_bytes()
        first_mtime = self.saved(0).stat().st_mtime_ns
        with patch.object(p, 'apply', wraps=p.apply) as apply:
            p.publish(self.rows[:2])
            self.assertEqual(apply.call_count, 1)
            self.assertEqual(apply.call_args.args[2]['id'], 'turn-0002')
        with patch.object(p, 'apply', side_effect=AssertionError('Old turns replayed')):
            p.publish(self.rows[:2])
        self.assertEqual(self.saved(0).read_bytes(), first)
        self.assertEqual(self.saved(0).stat().st_mtime_ns, first_mtime)
        self.assertFalse(self.saved(2).exists())
        index = p.read('turn-index.json')
        self.assertEqual(index, p.read('review.json'))
        self.assertEqual([t['has_prediction'] for t in index['turns']], [True, True, False])
        for turn in index['turns']:
            self.assertFalse({'state_after', 'prediction', 'llm_input', 'task_names'} & turn.keys())

    def test_full_turn_payload_matches_legacy_snapshot_semantics(self):
        p.publish(self.rows)
        graph = p.initial()
        for turn, row in zip(self.turns, self.rows):
            graph, resolved = p.apply(graph, row['patch'], turn)
            old = {**turn, 'prediction': row['patch'], 'resolved_prediction': resolved,
                   'generated_at': row['generated_at'],
                   'task_states': {x['ref']: graph['tasks'][x['ref']] for x in resolved['tasks']},
                   'actor_state_after': graph['actors'][turn['actor_id']],
                   'task_names': {k: v.get('title') for k, v in graph['tasks'].items()},
                   'state_after': {**graph, 'project_description': row['input']['project_description']},
                   'llm_input': row['input']}
            saved = p.read('turn-states/' + turn['id'] + '.json')
            saved.pop('_publication')
            self.assertEqual(saved, old)
        self.assertEqual(p.read('turn-index.json')['graph'], graph)

    def test_crash_before_index_commit_reuses_orphan_and_preserves_old_index(self):
        p.publish(self.rows[:1])
        index = (self.data / 'turn-index.json').read_bytes()
        atomic = p.atomic
        def fail_index(path, value):
            if path.name == 'turn-index.json':
                raise OSError('simulated disk failure')
            atomic(path, value)
        with patch.object(p, 'atomic', side_effect=fail_index):
            with self.assertRaises(OSError):
                p.publish(self.rows[:2])
        self.assertEqual((self.data / 'turn-index.json').read_bytes(), index)
        orphan_mtime = self.saved(1).stat().st_mtime_ns
        with patch.object(p, 'apply', side_effect=AssertionError('Orphan replayed')):
            p.publish(self.rows[:2])
        self.assertEqual(self.saved(1).stat().st_mtime_ns, orphan_mtime)
        self.assertEqual(p.read('turn-index.json')['status']['completed'], 2)

    def test_changed_prefix_or_source_cannot_overwrite_historical_state(self):
        p.publish(self.rows[:2])
        before = self.saved(0).read_bytes()
        edited = copy.deepcopy(self.rows)
        edited[0]['input']['marker'] = 'changed history'
        for rows in (edited, self.rows[:1]):
            with self.assertRaisesRegex(ValueError, 'prefix changed or shrank'):
                p.publish(rows)
        turns = copy.deepcopy(self.turns)
        turns[0]['messages'][0]['text'] = 'changed source'
        p.atomic(self.data / 'turns.json', turns)
        with self.assertRaisesRegex(ValueError, 'source changed'):
            p.publish(self.rows)
        self.assertEqual(self.saved(0).read_bytes(), before)

    def test_deleted_snapshot_is_rebuilt_without_rewriting_survivors(self):
        p.publish(self.rows)
        expected = self.saved(0).read_bytes()
        survivor_mtime = self.saved(2).stat().st_mtime_ns
        self.saved(0).unlink()
        p.publish(self.rows)
        self.assertEqual(self.saved(0).read_bytes(), expected)
        self.assertEqual(self.saved(2).stat().st_mtime_ns, survivor_mtime)

    def test_migration_preserves_ledger_reviews_stop_and_runner_status(self):
        (self.data / 'predictions.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in self.rows))
        (self.data / 'reviews.jsonl').write_text('saved review bytes\n')
        (self.data / 'STOP_LOCAL').write_text('Paused\n')
        p.atomic(self.data / 'status.json', {'completed': 3, 'total': 3, 'state': 'stopped', 'reason': 'paused by owner'})
        names = ['predictions.jsonl', 'reviews.jsonl', 'STOP_LOCAL', 'status.json']
        before = {name: (self.data / name).read_bytes() for name in names}
        migration.backfill(self.data)
        self.assertEqual(before, {name: (self.data / name).read_bytes() for name in names})
        self.assertEqual(p.read('turn-index.json')['status']['state'], 'stopped')

    def test_orphan_from_different_ledger_is_rejected(self):
        p.publish(self.rows[:1])
        (self.data / 'turn-index.json').unlink()
        changed = copy.deepcopy(self.rows[:1])
        changed[0]['input']['marker'] = 'different accepted input'
        with self.assertRaisesRegex(ValueError, 'differs from ledger'):
            p.publish(changed)


if __name__ == '__main__':
    unittest.main()
