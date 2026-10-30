"""Claude via the Max subscription for v0.1.B jobs, reusing scripts/v02/claude_haiku_day1.py guards:
OAuth subscription only, extra usage off, model checked on every receipt, stop on overage or a weekly-meter budget.

ClaudeSub(model, max_weekly_pp)(system, user) -> parsed JSON object from the reply.
"""
import json, os, re, subprocess, sys, tempfile, threading, time
from pathlib import Path


class QuotaStop(SystemExit):
    pass


class ClaudeSub:
    def __init__(self, model='claude-haiku-4-5-20251001', max_weekly_pp=None, max_tokens=None, max_week_abs=None):
        os.environ.setdefault('CCT_V02_DATASET', 'prototype')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v02'))
        import claude_haiku_day1 as h
        h.MODEL = model
        self.h, self.model, self.max_pp = h, model, max_weekly_pp
        self.max_week_abs = max_week_abs  # absolute backstop on the shared weekly meter (fraction, e.g. 0.24)
        self.max_tokens = max_tokens  # (input incl. cache, output): own-receipt budget; the shared meter is 1-pp coarse
        self.auth = h.check_auth()
        self.tmp = Path(tempfile.mkdtemp(prefix='v01b-claude-'))
        self.lock = threading.Lock()
        self.calls, self.first_week, self.week, self.usage = 0, None, None, {'in': 0, 'out': 0, 'cache_read': 0, 'cache_write': 0}

    def weekly_delta_pp(self):
        return None if self.first_week is None else round((self.week - self.first_week) * 100, 2)

    def __call__(self, system, user):
        sysmsg = system + '\nReply with ONLY the JSON object, no prose, no code fences.'
        log = self.tmp / f'{time.time_ns()}-{threading.get_ident()}.jsonl'
        with log.open('w') as out:
            subprocess.run(self.h.command(sysmsg), cwd=self.tmp, env=self.h.environment(), input=user, text=True,
                           stdout=out, stderr=subprocess.DEVNULL, timeout=300)
        rows = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
        result = next((x for x in reversed(rows) if x.get('type') == 'result'), {})
        rate = next((x.get('rate_limit_info') for x in reversed(rows) if x.get('type') == 'rate_limit_event'), None) or {}
        if rate.get('isUsingOverage') or rate.get('status') not in (None, 'allowed', 'allowed_warning'):
            raise QuotaStop('subscription limit/overage reached')
        if result.get('modelUsage') and set(result['modelUsage']) != {self.model}:
            raise QuotaStop('unexpected model in receipt: ' + ','.join(result['modelUsage']))
        week = ((rate.get('unifiedWindows') or {}).get('seven_day') or {}).get('utilization')
        u = result.get('usage') or {}
        with self.lock:
            self.calls += 1
            self.usage['in'] += u.get('input_tokens', 0); self.usage['out'] += u.get('output_tokens', 0)
            self.usage['cache_read'] += u.get('cache_read_input_tokens', 0); self.usage['cache_write'] += u.get('cache_creation_input_tokens', 0)
            if week is not None:
                self.first_week = week if self.first_week is None else self.first_week; self.week = week
            if self.max_tokens and (self.usage['in'] + self.usage['cache_read'] + self.usage['cache_write'] > self.max_tokens[0] or self.usage['out'] > self.max_tokens[1]):
                raise QuotaStop(f'own token budget exceeded: {self.usage}')
            if self.max_week_abs is not None and week is not None and week >= self.max_week_abs:
                raise QuotaStop(f'shared weekly meter at {week:.2f} >= backstop {self.max_week_abs}')
            if self.max_pp is not None and self.weekly_delta_pp() is not None and self.weekly_delta_pp() > self.max_pp:
                raise QuotaStop(f'weekly meter budget exceeded: +{self.weekly_delta_pp()} pp')
        m = re.search(r'\{.*\}', result.get('result', ''), re.S)
        if not m: raise ValueError('no JSON in reply')
        return json.loads(m.group(0))
