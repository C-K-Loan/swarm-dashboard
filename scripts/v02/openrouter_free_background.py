#!/usr/bin/env python3
"""Supervise the $0 Nemotron comparison; offline plan unless --run is explicit."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data/v02'
RUNNER = ROOT / 'scripts/v02/openrouter_free.py'
SUPERVISOR = DATA / 'nemotron-background'
STOPPING = False


def utc():
    return datetime.now(timezone.utc)


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')
    os.replace(temp, path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prefix_check(day1='day1', full='gamejam-full-source'):
    first, rest = DATA / day1, DATA / full
    if not (rest / 'turns.json').exists():
        return {'compatible': False, 'reason': 'Full source is not prepared'}
    a, b = read(first / 'turns.json'), read(rest / 'turns.json')
    bad = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    actors = {x['id']: x for x in read(rest / 'actors.json')}
    actor_changes = sum(actors.get(x['id']) != x for x in read(first / 'actors.json'))
    return {'compatible': len(b) >= len(a) and not bad and not actor_changes,
            'day1_turns': len(a), 'full_turns': len(b), 'mismatching_prefix_turns': len(bad),
            'first_mismatch_ordinal': bad[0] if bad else None,
            'changed_or_missing_day1_actors': actor_changes,
            'day1_sha256': sha(first / 'turns.json'), 'full_sha256': sha(rest / 'turns.json')}


def write_status(state, **fields):
    value = {'state': state, 'updated_at': utc().isoformat(), 'pid': os.getpid(), 'usd_ceiling': 0, **fields}
    atomic(SUPERVISOR / 'status.json', value)
    with (SUPERVISOR / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps(value) + '\n')
    print(json.dumps(value), flush=True)


def stopped(dataset=None):
    return STOPPING or (SUPERVISOR / 'STOP').exists() or bool(dataset and (DATA / dataset / 'STOP').exists())


def wait_until(deadline, dataset, overall_deadline):
    while utc() < deadline:
        if stopped(dataset) or utc() >= overall_deadline:
            return False
        time.sleep(min(15, max(0.1, (deadline - utc()).total_seconds())))
    return True


def provider_failure_budget(dataset, minutes):
    """Persist an outage window independently of model validation attempts."""
    path = SUPERVISOR / 'provider-retry-budget.json'
    budget = read(path, {})
    current = utc()
    if budget.get('dataset') != dataset or budget.get('state') not in ('active', 'exhausted'):
        budget = {'dataset': dataset, 'state': 'active', 'started_at': current.isoformat(),
                  'deadline': (current + timedelta(minutes=minutes)).isoformat(),
                  'failure_count': 0, 'max_failures': 12, 'window_minutes': minutes}
    budget['failure_count'] += 1
    expires = datetime.fromisoformat(budget['deadline'])
    allowed = budget['state'] == 'active' and current < expires and budget['failure_count'] <= budget['max_failures']
    delay = min(600, 300 * 2 ** min(budget['failure_count'] - 1, 2))
    budget.update(next_retry_at=min(expires, current + timedelta(seconds=delay)).isoformat(),
                  updated_at=current.isoformat(), state='active' if allowed else 'exhausted')
    atomic(path, budget)
    with (SUPERVISOR / 'provider-retry-history.jsonl').open('a') as stream:
        stream.write(json.dumps(budget) + '\n')
    return budget, allowed


def recovered_provider(dataset):
    path = SUPERVISOR / 'provider-retry-budget.json'
    budget = read(path, {})
    if budget.get('dataset') == dataset and budget.get('state') == 'active':
        budget.update(state='recovered', recovered_at=utc().isoformat())
        atomic(path, budget)
        with (SUPERVISOR / 'provider-retry-history.jsonl').open('a') as stream:
            stream.write(json.dumps(budget) + '\n')


def run_child(dataset, source, key_file, run=True, reasoning='disabled'):
    command = [sys.executable, str(RUNNER), '--dataset', dataset, '--source', source,
               '--key-file', str(key_file), '--max-requests', '990', '--reasoning', reasoning]
    if run:
        command += ['--run', '--allow-provider-retention']
    with (SUPERVISOR / (dataset + '.log')).open('a') as log:
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL)
        write_status('child_running', dataset=dataset, source=source, child_pid=child.pid)
        while child.poll() is None:
            if stopped(dataset):
                # The child exits between turns. Do not discard an in-flight free receipt.
                (DATA / dataset / 'STOP').touch()
            time.sleep(2)
    return child.returncode, read(DATA / dataset / 'free-runner-status.json', {})


def seed_full(day_dataset, full_dataset, source):
    report = prefix_check(full=source)
    if not report['compatible']:
        raise ValueError('Full source identities/order differ; explicit provenance resolution required')
    original = DATA / day_dataset
    target = DATA / full_dataset
    rows = (original / 'predictions.jsonl').read_text().splitlines()
    if len(rows) != report['day1_turns']:
        raise ValueError('Cannot seed an incomplete Day1 rollout')
    target.mkdir(parents=True, exist_ok=True)
    with (target / '.writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = target / 'predictions.jsonl'
        if ledger.exists():
            existing = ledger.read_text().splitlines()
            if existing[:len(rows)] != rows:
                raise ValueError('Existing full ledger does not contain the exact Day1 prefix')
        else:
            # Every prediction and its exact LLM input remain byte-for-byte unchanged.
            with ledger.open('x') as stream:
                stream.write('\n'.join(rows) + '\n'); stream.flush(); os.fsync(stream.fileno())
        atomic(target / 'seed-provenance.json', {
            'seed_dataset': day_dataset, 'seed_turns': len(rows), 'seeded_at': utc().isoformat(),
            'seed_ledger_sha256': sha(original / 'predictions.jsonl'), 'prefix_check': report,
            'original_receipt_jobs_base': str(original),
            'actor_catalog': 'Existing actor records are preserved; full-source additional actors are available for continuation.'})
        auth = original / 'source-authorization.json'
        if auth.exists():
            shutil.copyfile(auth, target / auth.name)


def supervise(args):
    overall_deadline = utc() + timedelta(days=args.max_days)
    stages = [(args.day_dataset, 'day1'), (args.full_dataset, args.full_source)]
    for index, (dataset, source) in enumerate(stages):
        if index:
            check = prefix_check(full=source)
            if not check['compatible']:
                write_status('blocked_source_prefix', dataset=dataset, prefix_check=check,
                             reason='Day1 finished, but expanded actor identities/order require explicit resolution; no full inference sent')
                return 4
            # Prepare/seed locally even at a quota boundary; the child owns the
            # next online preflight and its daily-limit result goes through the
            # ordinary cooldown/resume loop below.
            import openrouter_free
            os.environ['CCT_V02_DATASET'] = dataset
            import pipeline
            pipeline.DATA = DATA / dataset
            pipeline.DATA.mkdir(parents=True, exist_ok=True)
            with (pipeline.DATA / '.free-runner.lock').open('a') as full_lock:
                fcntl.flock(full_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                openrouter_free.prepare(pipeline, DATA / source)
                seed_full(args.day_dataset, dataset, source)
        retries = 0
        while utc() < overall_deadline:
            if stopped(dataset):
                write_status('stopped', dataset=dataset, reason='STOP requested')
                return 0
            budget = read(SUPERVISOR / 'provider-retry-budget.json', {})
            if budget.get('dataset') == dataset and budget.get('state') in ('active', 'exhausted'):
                expires = datetime.fromisoformat(budget['deadline'])
                if budget['state'] == 'exhausted' or utc() >= expires:
                    write_status('stopped_provider_outage', dataset=dataset, provider_budget=budget,
                                 reason='Bounded provider retry window exhausted; no model validation attempts reset')
                    return 3
                deadline = datetime.fromisoformat(budget['next_retry_at'])
                if utc() < deadline:
                    write_status('waiting_provider', dataset=dataset, resume_at=deadline.isoformat(), provider_budget=budget)
                    if not wait_until(deadline, dataset, overall_deadline):
                        write_status('stopped', dataset=dataset, reason='STOP or supervisor lifetime reached')
                        return 0
                    if utc() >= expires:
                        continue
            before = read(DATA / dataset / 'free-runner-status.json', {}).get('completed', 0)
            code, state = run_child(dataset, source, args.key_file, reasoning=getattr(args, 'reasoning', 'disabled'))
            if stopped(dataset):
                write_status('stopped', dataset=dataset, reason='STOP requested', child_status=state)
                return 0
            if state.get('completed', 0) > before:
                retries = 0
                recovered_provider(dataset)
            kind = state.get('state')
            if code == 0 and state.get('completed') == state.get('total') and state.get('total', 0) > 0:
                write_status('stage_complete', dataset=dataset, completed=state['completed'])
                break
            if code == 0 and kind == 'request_limit':
                # New invocation retains the exact ledger; next call rechecks account daily quota.
                continue
            if code == 3 and kind == 'provider_unavailable':
                budget, allowed = provider_failure_budget(dataset, getattr(args, 'provider_retry_minutes', 60))
                if not allowed:
                    write_status('stopped_provider_outage', dataset=dataset, provider_budget=budget,
                                 child_status=state, reason='Bounded provider retry window exhausted')
                    return 3
                write_status('waiting_provider', dataset=dataset, resume_at=budget['next_retry_at'],
                             provider_budget=budget, child_status=state)
                continue
            if code == 3 and kind == 'rate_limited':
                reason = state.get('reason', '')
                if 'daily request quota exhausted' in reason:
                    deadline = datetime.fromisoformat(state['suggested_resume_at'])
                    retries = 0
                else:
                    retries += 1
                    if retries > 5:
                        write_status('stopped_rate_limit', dataset=dataset, reason='Five cooldown retries exhausted', child_status=state)
                        return 3
                    delay = min(900, 60 * 2 ** (retries - 1))
                    # Honor explicit provider Retry-After when it is a bounded number.
                    try:
                        delay = max(delay, min(3600, float(reason.split('Retry-After=')[1])))
                    except (ValueError, IndexError):
                        pass
                    deadline = utc() + timedelta(seconds=delay)
                write_status('waiting_quota_or_cooldown', dataset=dataset, resume_at=deadline.isoformat(),
                             cooldown_attempt=retries, child_status=state)
                if not wait_until(deadline, dataset, overall_deadline):
                    write_status('stopped', dataset=dataset, reason='STOP or supervisor lifetime reached')
                    return 0
                continue
            # Includes schema/evidence, source drift, pricing, privacy, and all non-429 errors.
            write_status('stopped', dataset=dataset, child_exit=code, child_status=state,
                         reason='Non-retryable result; no automatic reset, patch rewriting, or model fallback')
            return code or 2
        else:
            write_status('stopped', dataset=dataset, reason='Supervisor lifetime reached')
            return 0
    write_status('complete')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--allow-provider-retention', action='store_true')
    parser.add_argument('--key-file', type=Path, default=Path('.openrouter_key'))
    parser.add_argument('--day-dataset', default='nemotron-day1')
    parser.add_argument('--full-dataset', default='nemotron-full')
    parser.add_argument('--full-source', default='gamejam-full-anchored')
    parser.add_argument('--max-days', type=int, default=30)
    parser.add_argument('--reasoning', choices=['disabled', 'enabled'], default='disabled')
    parser.add_argument('--supervisor-name', default='nemotron-background')
    parser.add_argument('--provider-retry-minutes', type=int, default=60,
                        help='Persistent outage window, 1..60 minutes; retries wait 5–10 minutes')
    args = parser.parse_args()
    if args.run and not args.allow_provider_retention:
        parser.error('--run requires --allow-provider-retention; no corpus request was sent')
    import re
    if any(not re.fullmatch(r'nemotron-[a-zA-Z0-9_-]+', s) for s in (args.day_dataset, args.full_dataset)):
        parser.error('Comparison datasets must start nemotron-')
    if args.day_dataset == args.full_dataset or not re.fullmatch(r'[a-zA-Z0-9_-]+', args.full_source):
        parser.error('Invalid dataset selection')
    if not 1 <= args.max_days <= 60:
        parser.error('--max-days must be 1..60')
    if not 1 <= args.provider_retry_minutes <= 60:
        parser.error('--provider-retry-minutes must be 1..60')
    if not re.fullmatch(r'nemotron-[a-zA-Z0-9_-]+', args.supervisor_name):
        parser.error('Supervisor name must start nemotron-')
    global SUPERVISOR
    SUPERVISOR = DATA / args.supervisor_name
    if not args.run:
        print(json.dumps({'state': 'offline_plan', 'day1_dataset': args.day_dataset,
                          'full_dataset': args.full_dataset, 'full_source': args.full_source,
                          'full_continuation_check': prefix_check(full=args.full_source)}, indent=2))
        return 0
    os.umask(0o077)
    SUPERVISOR.mkdir(parents=True, exist_ok=True)
    with (SUPERVISOR / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A Nemotron background supervisor is already running')
        def stop(signum, frame):
            global STOPPING
            STOPPING = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            return supervise(args)
        except (OSError, ValueError, KeyError) as error:
            write_status('stopped', reason=str(error))
            return 2


if __name__ == '__main__':
    raise SystemExit(main())
