#!/usr/bin/env python3
"""Offline $0 guard tests: no network or private corpus access."""
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import openrouter_free as runner


class ZeroDollarTests(unittest.TestCase):
    def test_all_price_dimensions_must_be_zero(self):
        runner.require_free({'prompt': '0', 'completion': '0', 'reasoning': '0', 'request': 0})
        for prices in ({}, {'prompt': 0}, {'prompt': 0, 'completion': '0.01'},
                       {'prompt': 0, 'completion': 0, 'reasoning': '0.001'},
                       {'prompt': 0, 'completion': 0, 'input_cache_read': None},
                       {'prompt': 0, 'completion': 0, 'request': 'unknown'}):
            with self.subTest(prices=prices), self.assertRaises(runner.GuardError):
                runner.require_free(prices)

    def test_request_has_exact_free_route_no_fallback_or_paid_features(self):
        request = runner.make_request('schema', {'turn': 1})
        self.assertEqual(request['model'], runner.MODEL)
        self.assertTrue(request['model'].endswith(':free'))
        self.assertEqual(request['provider']['only'], ['nvidia'])
        self.assertFalse(request['provider']['allow_fallbacks'])
        self.assertEqual(request['provider']['max_price'], runner.ZERO_PRICES)
        self.assertEqual(request['provider']['data_collection'], 'deny')
        self.assertFalse(request['reasoning']['enabled'])
        for field in ('models', 'tools', 'plugins', 'web_search_options'):
            self.assertNotIn(field, request)

    def test_cost_receipt_is_required_and_all_cost_fields_checked(self):
        good = {'model': runner.MODEL, 'provider': 'Nvidia', 'usage': {'cost': 0,
                'cost_details': {'upstream_inference_cost': 0},
                'completion_tokens_details': {'reasoning_tokens': 0}}}
        runner.audit_response(good)
        cases = []
        for key, val in [('model', runner.MODEL[:-5]), ('provider', 'other'), ('usage', {})]:
            item = copy.deepcopy(good); item[key] = val; cases.append(item)
        item = copy.deepcopy(good); item['usage']['cost_details']['upstream_inference_cost'] = 1; cases.append(item)
        item = copy.deepcopy(good); item['usage']['cost'] = None; cases.append(item)
        for response in cases:
            with self.subTest(response=response), self.assertRaises(runner.GuardError):
                runner.audit_response(response)

    @staticmethod
    def free_fixtures(remaining=1000):
        return [{'data': [{'id': runner.MODEL, 'pricing': {'prompt': '0', 'completion': '0'}}]},
                {'data': {'endpoints': [{'tag': 'nvidia', 'model_id': runner.MODEL,
                 'pricing': {'prompt': '0', 'completion': '0'}}]}},
                {'data': {'free_model_daily_requests': {'remaining': remaining, 'limit': 1000},
                          'limit_remaining': 0}}]

    def test_zero_remaining_dollars_still_permits_free_quota(self):
        with patch.object(runner, 'api', side_effect=self.free_fixtures()) as api:
            result = runner.preflight('not-a-real-key')
        self.assertEqual(result['key_limit_remaining'], 0)
        self.assertEqual(api.call_count, 3)

    def test_exhausted_free_quota_stops(self):
        with patch.object(runner, 'api', side_effect=self.free_fixtures(0)), self.assertRaises(runner.RateLimited):
            runner.preflight('not-a-real-key')

    def test_paid_sibling_never_substitutes_missing_free_model(self):
        with patch.object(runner, 'api', return_value={'data': [
            {'id': runner.MODEL[:-5], 'pricing': {'prompt': '0', 'completion': '0'}}
        ]}), self.assertRaises(runner.GuardError):
            runner.preflight('not-a-real-key')

    def test_endpoint_price_can_block_even_if_catalog_free(self):
        responses = self.free_fixtures()
        responses[1]['data']['endpoints'][0]['pricing']['completion'] = '0.000001'
        with patch.object(runner, 'api', side_effect=responses) as api, self.assertRaises(runner.GuardError):
            runner.preflight('not-a-real-key')
        self.assertEqual(api.call_count, 2)

    def test_json_parser_never_executes_or_rewrites_candidates(self):
        self.assertEqual(runner.parse_patch('```json\n{"tasks":[]}\n```'), {'tasks': []})
        for text in ('Here is the JSON: {}', '[]', '__import__("os").system("false")', None):
            with self.subTest(text=text), self.assertRaises(ValueError):
                runner.parse_patch(text)

    def test_repair_receives_rejected_json_and_specific_error(self):
        request = runner.make_request('schema', {'turn': 1}, True,
                                      correction='quote exceeds 300 characters', candidate='{"tasks":[]}')
        self.assertEqual([m['role'] for m in request['messages']], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(request['messages'][2]['content'], '{"tasks":[]}')
        self.assertIn('quote exceeds 300 characters', request['messages'][3]['content'])
        self.assertEqual(request['provider']['max_price'], runner.ZERO_PRICES)

    def test_schema_feedback_names_constraint_without_repeating_large_source(self):
        error = SimpleNamespace(validator='maxLength', validator_value=300,
                                absolute_path=['tasks', 0, 'evidence', 0, 'quote'], instance='x' * 20000)
        feedback = json.loads(runner.validation_feedback(error))
        self.assertEqual(feedback['constraint'], 300)
        self.assertEqual(feedback['actual_length'], 20000)
        self.assertLess(len(runner.validation_feedback(error)), 300)

    def test_all_evidence_errors_are_reported_together(self):
        import pipeline
        candidate = {'schema_version': '0.2', 'turn_id': 't1', 'actor_patch': {'state': 'working'},
                     'actor_evidence': [{'message_id': 'old-message', 'quote': 'Old text'}],
                     'tasks': [{'ref': 'new:t1', 'resolution': 'new', 'changes': {
                         'title': 'Example', 'summary': 'Example', 'status': 'blocked',
                         'importance': None, 'complexity': None,
                         'blocker': {'reason': 'Waiting', 'need_human_help': None, 'waiting_on_actor': 'made-up-id'}},
                         'evidence': [{'message_id': 'current-message', 'quote': 'Source text'}]}],
                     'decisions': [], 'relationships': []}
        result = json.loads(runner.complete_feedback(pipeline, json.dumps(candidate),
            {'messages': [{'id': 'current-message', 'text': 'Source\ntext'}]}, {'actors': {'human': {}}}, ValueError('Invalid evidence')))
        self.assertEqual(result['allowed_current_message_ids'], ['current-message'])
        self.assertEqual(len(result['errors']), 3)

    def test_restarts_cannot_reset_five_attempt_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'extract-turn.txt').write_text('Prompt')
            for i in range(5):
                job = directory / 'openrouter-jobs' / ('t1-' + str(i)); job.mkdir(parents=True)
                (job / 'request.json').write_text('{}')
            fake = SimpleNamespace(DATA=directory, SCHEMA={}, ledger=lambda: [], read=lambda name: [{'id': 't1'}],
                                   atomic=lambda path, value: path.write_text(json.dumps(value)),
                                   initial=lambda: {}, context=lambda *args: {})
            with patch.object(runner, 'api') as api, self.assertRaises(runner.GuardError):
                runner.run(fake, 'fake', SimpleNamespace(max_requests=990, max_corrections=4))
            api.assert_not_called()

    def test_reasoning_is_explicit_and_keeps_zero_dollar_route(self):
        config = {'max_tokens': 16384, 'reasoning': {'enabled': True, 'max_tokens': 4096, 'exclude': True}}
        request = runner.make_request('schema', {'turn': 1}, inference_config=config)
        self.assertEqual(request['reasoning'], config['reasoning'])
        self.assertEqual(request['max_tokens'], 16384)
        self.assertEqual(request['provider']['max_price'], runner.ZERO_PRICES)

    def test_frozen_reasoning_config_cannot_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = SimpleNamespace(DATA=Path(tmp), ledger=lambda: [], atomic=lambda path, value: path.write_text(json.dumps(value)))
            runner.freeze_config(fake, SimpleNamespace(reasoning='enabled', max_corrections=4))
            with self.assertRaises(runner.GuardError):
                runner.freeze_config(fake, SimpleNamespace(reasoning='disabled', max_corrections=4))

    def test_reasoning_run_requires_advertised_bounded_support(self):
        fixture = self.free_fixtures()
        fixture[1]['data']['endpoints'][0]['supported_parameters'] = ['reasoning']
        with patch.object(runner, 'api', side_effect=fixture), self.assertRaises(runner.GuardError):
            runner.preflight('fake', reasoning=True)

    def test_only_transient_upstream_error_codes_are_retryable(self):
        for code in (502, 503, 504):
            with self.subTest(code=code), self.assertRaises(runner.TransientProviderError):
                runner.audit_response({'error': {'code': code, 'message': 'Upstream error'}})
        for code in (400, 401, 402, 404):
            with self.subTest(code=code), self.assertRaises(runner.GuardError) as error:
                runner.audit_response({'error': {'code': code, 'message': 'Terminal'}})
            self.assertNotIsInstance(error.exception, runner.TransientProviderError)

    def test_confirmed_outages_do_not_consume_candidate_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'request.json').write_text('{}')
            for code in (502, 503, 504, 429):
                (path / 'response.json').write_text(json.dumps({'error': {'code': code}}))
                self.assertFalse(runner.consumes_candidate_attempt(path))
            (path / 'response.json').write_text(json.dumps({'choices': [{'message': {'content': 'invalid JSON'}}]}))
            self.assertTrue(runner.consumes_candidate_attempt(path))
            self.assertTrue((path / 'request.json').exists())

    def test_provider_error_cannot_hide_a_charge_as_retryable(self):
        for usage in ({'cost': 1}, {'cost': 0, 'cost_details': {'upstream_inference_cost': 1}}, {'cost': None}):
            with self.subTest(usage=usage), self.assertRaises(runner.GuardError) as error:
                runner.audit_response({'error': {'code': 502}, 'usage': usage})
            self.assertNotIsInstance(error.exception, runner.TransientProviderError)

    def test_http_outage_without_candidate_is_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'request.json').write_text('{}')
            (path / 'provider-error.json').write_text(json.dumps({'error_kind': 'provider_unavailable', 'error': 'HTTP 502'}))
            self.assertFalse(runner.consumes_candidate_attempt(path))

    def test_provider_window_persists_and_expires_without_reset(self):
        import openrouter_free_background as background
        start = datetime(2026, 10, 28, 21, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, patch.object(background, 'SUPERVISOR', Path(tmp)):
            with patch.object(background, 'utc', return_value=start):
                first, allowed = background.provider_failure_budget('nemotron-reasoning-day1', 60)
            self.assertTrue(allowed)
            self.assertEqual(datetime.fromisoformat(first['next_retry_at']), start + timedelta(minutes=5))
            with patch.object(background, 'utc', return_value=start + timedelta(minutes=10)):
                second, allowed = background.provider_failure_budget('nemotron-reasoning-day1', 60)
            self.assertTrue(allowed)
            self.assertEqual(second['deadline'], first['deadline'])
            self.assertEqual(datetime.fromisoformat(second['next_retry_at']), start + timedelta(minutes=20))
            with patch.object(background, 'utc', return_value=start + timedelta(minutes=61)):
                final, allowed = background.provider_failure_budget('nemotron-reasoning-day1', 60)
            self.assertFalse(allowed)
            self.assertEqual(final['state'], 'exhausted')
            self.assertEqual(final['deadline'], first['deadline'])

    def test_causal_ledger_independent_rollout_resumes_without_recharging(self):
        import pipeline
        actor = {'id': 'actor:a', 'state': 'unknown'}
        turns = [{'id': 'turn-' + str(i), 'ordinal': i, 'actor_id': 'actor:a', 'end_ts': i,
                  'messages': [{'id': 'm' + str(i), 'text': 'Create example task'}]} for i in (1, 2)]
        first = {'schema_version': '0.2', 'turn_id': 'turn-1', 'actor_patch': {}, 'actor_evidence': [],
                 'tasks': [{'ref': 'new:t1', 'resolution': 'new', 'changes': {
                     'title': 'Example', 'summary': 'Example task', 'status': 'not_started',
                     'importance': None, 'complexity': None, 'blocker': None},
                     'evidence': [{'message_id': 'm1', 'quote': 'Create example task'}]}],
                 'decisions': [], 'relationships': []}
        second = {'schema_version': '0.2', 'turn_id': 'turn-2', 'actor_patch': {}, 'actor_evidence': [],
                  'tasks': [], 'decisions': [], 'relationships': []}
        responses = [{'model': runner.MODEL, 'provider': 'Nvidia', 'usage': {'cost': 0},
                      'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(v)}}]}
                     for v in (first, second)]
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, 'DATA', Path(tmp)):
            pipeline.atomic(Path(tmp) / 'actors.json', [actor])
            pipeline.atomic(Path(tmp) / 'turns.json', turns)
            pipeline.atomic(Path(tmp) / 'manifest.json', {'root_turn_id': 'turn-1', 'teacher_model': runner.MODEL})
            (Path(tmp) / 'extract-turn.txt').write_text('Extract tasks')
            args = SimpleNamespace(max_requests=2, allow_provider_retention=False)
            with patch.object(runner, 'preflight', return_value={}), patch.object(runner, 'api', side_effect=responses) as api, patch.object(runner.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                runner.run(pipeline, 'fake', args)
                runner.run(pipeline, 'fake', args)
            self.assertEqual(api.call_count, 2)
            rows = pipeline.ledger()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['input']['relevant_existing_tasks_with_ids'], [])
            self.assertEqual(len(rows[1]['input']['relevant_existing_tasks_with_ids']), 1)
            self.assertEqual(rows[1]['model'], runner.MODEL)
            self.assertEqual(len(pipeline.read('review.json')['graph']['tasks']), 1)


if __name__ == '__main__':
    unittest.main()
