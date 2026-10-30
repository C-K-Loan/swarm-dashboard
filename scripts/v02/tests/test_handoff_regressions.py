"""Shared V0.2 contracts for the Haiku migration; temporary fixtures, no inference.

These exercise the common pipeline, not an in-progress model runner. Keep them
green when changing prompts, acceptance, dataset selection, or state replay.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dataset
import pipeline as p


class HandoffRegressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / 'candidate'
        self.data.mkdir()
        override = patch.object(p, 'DATA', self.data)
        override.start()
        self.addCleanup(override.stop)
        self.turns = [
            {'id': f'turn-{i:04d}', 'ordinal': i, 'actor_id': 'actor:a',
             'start_ts': i * 10, 'end_ts': i * 10 + 1,
             'messages': [{'id': f'm{i}-4f8d', 'text': text, 'ts': i * 10}]}
            for i, text in enumerate([
                'Build UI. Review is pending. Build tests.',
                'UI is done. Tests depend on UI.',
                'All tests passed.',
            ], 1)
        ]
        self.write('actors.json', [{'id': 'actor:a', 'name': 'A'}])
        self.write('turns.json', self.turns)
        self.write('manifest.json', {'root_turn_id': 'turn-0001', 'teacher_model': 'gpt-6-luna'})
        self.base = self.empty(0)
        self.base['tasks'] = [self.new_task('new:ui', 'Build UI', 'Build UI.')]

    def write(self, name, value):
        (self.data / name).write_text(json.dumps(value))

    def empty(self, index):
        return {'schema_version': '0.2', 'turn_id': self.turns[index]['id'],
                'actor_patch': {}, 'actor_evidence': [], 'tasks': [],
                'decisions': [], 'relationships': []}

    def evidence(self, index, quote):
        return [{'message_id': self.turns[index]['messages'][0]['id'], 'quote': quote}]

    def new_task(self, ref, title, quote):
        return {'ref': ref, 'resolution': 'new',
                'changes': {'title': title, 'summary': title, 'status': 'not_started',
                            'importance': 'high', 'complexity': 'medium', 'blocker': None},
                'evidence': self.evidence(0, quote)}

    def accept(self, candidate):
        path = self.root / 'candidate.json'
        path.write_text(json.dumps(candidate))
        with contextlib.redirect_stdout(io.StringIO()):
            p.accept(path)

    def public_bytes(self):
        return {name: (self.data / name).read_bytes()
                for name in ('predictions.jsonl', 'review.json', 'turn-index.json', 'status.json')}

    def test_invalid_candidate_never_changes_accepted_prefix_or_published_state(self):
        self.accept(self.base)
        before = self.public_bytes()
        task_id = next(iter(p.replay()['tasks']))
        valid = self.empty(1)
        valid['tasks'] = [{'ref': task_id, 'resolution': 'existing',
                           'changes': {'status': 'done'},
                           'evidence': self.evidence(1, 'UI is done.')}]
        candidates = {}
        bad = copy.deepcopy(valid)
        bad['tasks'][0]['evidence'][0]['message_id'] = 'm2-4f8f'
        candidates['one-character evidence UUID typo'] = bad
        bad = copy.deepcopy(valid)
        bad['tasks'][0]['evidence'] = self.evidence(0, 'Build UI.')
        candidates['real quote from an earlier turn'] = bad
        bad = copy.deepcopy(valid)
        bad['tasks'][0]['changes']['status'] = 'almost_finished'
        candidates['unsupported status enum'] = bad
        bad = copy.deepcopy(valid)
        bad['turn_id'] = 'turn-0003'
        candidates['skipped source turn'] = bad
        bad = copy.deepcopy(valid)
        bad['tasks'][0]['ref'] = 'task:does-not-exist'
        candidates['unknown existing task'] = bad
        bad = copy.deepcopy(valid)
        bad['actor_patch'] = {'role': 'dev'}
        candidates['actor update without evidence'] = bad
        for description, candidate in candidates.items():
            with self.subTest(description=description):
                with self.assertRaises((ValueError, jsonschema.ValidationError)):
                    self.accept(candidate)
                self.assertEqual(self.public_bytes(), before)

    def test_stitched_real_fragments_and_missing_new_task_fields_are_rejected(self):
        bad = copy.deepcopy(self.base)
        bad['tasks'][0]['evidence'] = self.evidence(0, 'Build UI. Build tests.')
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            self.accept(bad)
        bad = copy.deepcopy(self.base)
        del bad['tasks'][0]['changes']['blocker']
        with self.assertRaisesRegex(ValueError, 'complete fields'):
            self.accept(bad)
        self.assertFalse((self.data / 'predictions.jsonl').exists())
        self.assertFalse((self.data / 'review.json').exists())

    def test_dependency_edges_and_sparse_state_survive_replay_without_future_leaks(self):
        first = copy.deepcopy(self.base)
        first['tasks'].append(self.new_task('new:tests', 'Build tests', 'Build tests.'))
        first['relationships'] = [
            {'type': 'works_on', 'from_ref': 'actor:a', 'to_ref': 'new:tests',
             'evidence': self.evidence(0, 'Build tests.')}
        ]
        self.accept(first)
        state1 = p.replay()
        ui, tests = [task['ref'] for task in p.read('turn-states/turn-0001.json')['resolved_prediction']['tasks']]
        second = self.empty(1)
        second['tasks'] = [{'ref': ui, 'resolution': 'existing', 'changes': {'status': 'done'},
                            'evidence': self.evidence(1, 'UI is done.')}]
        dependency = {'type': 'depends_on', 'from_ref': tests, 'to_ref': ui,
                      'evidence': self.evidence(1, 'Tests depend on UI.')}
        second['relationships'] = [dependency, copy.deepcopy(dependency)]
        self.accept(second)
        published = [p.read('turn-states/'+t['id']+'.json', p.turn_view(t)) for t in self.turns]
        after = p.replay()
        self.assertEqual(after['tasks'][ui]['status'], 'done')
        self.assertEqual(after['tasks'][tests]['status'], 'not_started')
        self.assertEqual(len(after['relationships']), 2)
        self.assertEqual(published[0]['state_after']['tasks'][ui]['status'], 'not_started')
        self.assertEqual(published[0]['state_after']['relationships'], state1['relationships'])
        prior_tasks = {t['id']: t for t in published[1]['llm_input']['relevant_existing_tasks_with_ids']}
        self.assertEqual(prior_tasks[ui]['status'], 'not_started')
        self.assertNotIn('depends_on', json.dumps(published[1]['llm_input']))
        self.assertIsNone(published[2]['state_after'])
        self.assertEqual(after, p.read('review.json')['graph'])

    def test_candidate_accept_and_review_preserve_sibling_baseline_and_stop_marker(self):
        baseline = self.root / 'day1'
        baseline.mkdir()
        for name, value in {'predictions.jsonl': b'original Luna bytes\n',
                            'STOP_LOCAL': b'Keep Codex stopped\n',
                            'reviews.jsonl': b'original baseline review\n'}.items():
            (baseline / name).write_bytes(value)
        before = {f.name: f.read_bytes() for f in baseline.iterdir()}
        self.accept(self.base)
        accepted = (self.data / 'predictions.jsonl').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            p.save_review({'turn_id': 'turn-0001', 'status': 'approved', 'base_revision': 0})
        self.assertEqual((self.data / 'predictions.jsonl').read_bytes(), accepted)
        self.assertEqual({f.name: f.read_bytes() for f in baseline.iterdir()}, before)
        self.assertEqual(json.loads((self.data / 'reviews.jsonl').read_text())['turn_id'], 'turn-0001')

    def test_review_cannot_borrow_a_task_created_after_the_reviewed_turn(self):
        self.accept(self.base)
        second = self.empty(1)
        new = self.new_task('new:later', 'Later task', 'Build tests.')
        new['evidence'] = self.evidence(1, 'Tests depend on UI.')
        second['tasks'] = [new]
        self.accept(second)
        later = p.read('turn-states/turn-0002.json')['resolved_prediction']['tasks'][0]['ref']
        correction = copy.deepcopy(self.base)
        correction['tasks'][0].update(ref=later, resolution='existing')
        before = self.public_bytes()
        with self.assertRaisesRegex(ValueError, 'Unknown existing task'):
            p.save_review({'turn_id': 'turn-0001', 'status': 'corrected',
                           'base_revision': 0, 'correction': correction})
        self.assertFalse((self.data / 'reviews.jsonl').exists())
        self.assertEqual(self.public_bytes(), before)

    def test_explicit_dataset_override_does_not_switch_active_dataset(self):
        root = self.root / 'repo'
        active = root / 'data/v02/active.json'
        active.parent.mkdir(parents=True)
        active.write_text('{"dataset":"day1"}\n')
        before = active.read_bytes()
        with patch.object(dataset, 'ROOT', root), patch.dict(os.environ, {'CCT_V02_DATASET': 'haiku-day1'}):
            self.assertEqual(dataset.data_dir(), root / 'data/v02/haiku-day1')
        self.assertEqual(active.read_bytes(), before)
        self.assertFalse((root / 'data/v02/haiku-day1').exists())

    def test_dataset_paths_cannot_escape_data_directory(self):
        for name in ('../day1', '/tmp/other', 'haiku/day1', '.', '..'):
            with self.subTest(name=name), patch.dict(os.environ, {'CCT_V02_DATASET': name}):
                with self.assertRaisesRegex(ValueError, 'Invalid local dataset'):
                    dataset.data_dir()


if __name__ == '__main__':
    unittest.main()
