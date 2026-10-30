import io, json, sys, unittest, urllib.error
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nvidia_build as n


class NvidiaBuildTests(unittest.TestCase):
    BODY = {'model': n.MODEL, 'messages': [], 'reasoning': {'enabled': False},
            'provider': {'only': ['nvidia'], 'allow_fallbacks': False, 'max_price': n.ZERO_PRICES}}

    def test_key_file_forms(self):
        for raw in ('NVIDIA_API_KEY=nvapi-x\n', 'export NVIDIA_API_KEY="nvapi-x"\n', 'nvapi-x\n'):
            self.assertEqual(n.key_value(raw), 'nvapi-x')

    def test_openrouter_fields_translated(self):
        body = n.translate(self.BODY)
        self.assertNotIn('provider', body); self.assertNotIn('reasoning', body)
        self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': False})

    def test_wrong_model_refused_before_sending(self):
        with self.assertRaises(n.GuardError):
            n.translate({**self.BODY, 'model': 'nvidia/nemotron-3-ultra-550b-a55b:free'})

    def test_audit(self):
        ok = {'model': n.MODEL, 'usage': {'prompt_tokens': 5, 'completion_tokens': 2}}
        self.assertEqual(n.audit_response(ok)['prompt_tokens'], 5)
        for bad in ({'model': 'other'}, {'model': n.MODEL, 'usage': {'cost': 0.01}}, {'error': {'code': 400}}):
            with self.assertRaises(n.GuardError):
                n.audit_response(bad)
        with self.assertRaises(n.TransientProviderError):
            n.audit_response({'error': {'code': 503, 'message': 'Service temporarily overloaded'}})
        with self.assertRaises(n.RateLimited):
            n.audit_response({'error': {'code': 429, 'message': 'Too many requests'}})

    def test_http_errors_stop(self):
        for code, kind in ((429, n.RateLimited), (402, n.GuardError), (503, n.TransientProviderError)):
            err = urllib.error.HTTPError(n.API, code, 'x', {}, io.BytesIO(b''))
            with mock.patch('urllib.request.urlopen', side_effect=err), self.assertRaises(kind):
                n.api('/chat/completions', 'NVIDIA_API_KEY=k', self.BODY)

    def test_key_sent_only_as_bearer_header(self):
        seen = {}
        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): pass
        def fake(req, timeout):
            seen['auth'] = req.get_header('Authorization'); seen['data'] = req.data
            ev = [{'model': n.MODEL, 'choices': [{'delta': {'content': '{"ok"'}}]},
                  {'model': n.MODEL, 'choices': [{'delta': {'content': ': true}'}, 'finish_reason': 'stop'}]},
                  {'model': n.MODEL, 'choices': [], 'usage': {'prompt_tokens': 3, 'completion_tokens': 4}}]
            return R(''.join('data: ' + json.dumps(e) + '\n\n' for e in ev).encode() + b'data: [DONE]\n\n')
        with mock.patch('urllib.request.urlopen', fake):
            out = n.api('/chat/completions', 'NVIDIA_API_KEY=secret\n', self.BODY)
        self.assertEqual(seen['auth'], 'Bearer secret'); self.assertNotIn(b'secret', seen['data'])
        self.assertTrue(json.loads(seen['data'])['stream'])
        self.assertEqual(out['provider'], 'nvidia'); self.assertEqual(out['choices'][0]['message']['content'], '{"ok": true}')
        self.assertEqual(out['choices'][0]['finish_reason'], 'stop'); self.assertEqual(n.audit_response(out)['completion_tokens'], 4)

    def test_stalled_stream_is_transient(self):
        with mock.patch('urllib.request.urlopen', side_effect=TimeoutError()), self.assertRaises(n.TransientProviderError):
            n.api('/chat/completions', 'k', self.BODY)


class RouteTests(unittest.TestCase):
    def test_launcher_swaps_request_surface_and_keeps_helpers(self):
        import subprocess, tempfile
        probe = ("import openrouter_free as o, nvidia_build as n\n"
                 "assert o.api is n.api and o.MODEL == n.MODEL and o.RateLimited is n.RateLimited\n"
                 "assert callable(o.complete_feedback)\nprint('ok')\n")
        with tempfile.NamedTemporaryFile('w', suffix='.py') as f:
            f.write(probe); f.flush()
            out = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'nvidia_route.py'), f.name],
                                 capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), 'ok', out.stderr[-500:])

if __name__ == '__main__':
    unittest.main()
