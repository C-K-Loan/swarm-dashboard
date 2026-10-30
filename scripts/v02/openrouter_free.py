#!/usr/bin/env python3
"""Independent, causal V0.2 Nemotron comparison with a fail-closed $0 guard.

No Codex execution, local subscription quota, paid model fallback, or active
dataset switching. Requests contain only the pipeline's causal turn context.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = 'https://openrouter.ai/api/v1'
MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'
PROVIDER = 'nvidia'
DEFAULT_KEY = Path('.openrouter_key')
ZERO_PRICES = {'prompt': 0, 'completion': 0, 'request': 0, 'image': 0}
PROMPT_CONTRACT_VERSION = 'current-turn-evidence-v2'
EVIDENCE_RULES = '''
Strict evidence boundary: project_description, prior tasks and recent_relevant_turns
are historical identity/state context ONLY. They are NEVER eligible evidence for
this patch. Every evidence message_id must occur in current_turn_prose. Do not
re-emit earlier requests or decisions merely because the project brief contains them.
Copy short contiguous quotations directly from current_turn_prose, including exact
punctuation, Markdown and whitespace. Never flatten line breaks or join sentences.
Use a shorter exact substring if the source passage is longer than 300 characters.
For unsupported changes, omit the change rather than borrowing historical evidence.
Copy actor IDs exactly from relevant_actors_and_relationships.actors; human's ID is
human. Actor names, invented IDs and task IDs are not valid waiting_on_actor values.
'''


class GuardError(RuntimeError):
    pass


class RateLimited(GuardError):
    pass


class TransientProviderError(GuardError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def zero(value):
    try:
        return not isinstance(value, bool) and Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def require_free(pricing):
    # Unknown billing fields are deliberately rejected unless explicitly zero.
    if not isinstance(pricing, dict) or not all(k in pricing for k in ('prompt', 'completion')):
        raise GuardError('Missing prompt/completion prices')
    if any(not zero(v) for v in pricing.values()):
        raise GuardError('A nonzero, unknown, or missing billing price was found')


def audit_response(response):
    if response.get('error'):
        # An upstream-error envelope must not hide a nonzero charge and turn
        # it into a retryable outage. Missing usage on pre-generation errors
        # remains preserved as unknown, under the request's strict $0 ceiling.
        if response.get('usage') is not None:
            usage = response['usage']
            if not isinstance(usage, dict) or not zero(usage.get('cost')):
                raise GuardError('Provider error included missing/nonzero usage cost; refusing retry')
            pending = [usage]
            while pending:
                for key, value in pending.pop().items():
                    if isinstance(value, dict):
                        pending.append(value)
                    elif 'cost' in key.lower() and not zero(value):
                        raise GuardError('Provider error included an unknown/nonzero nested cost; refusing retry')
        if response['error'].get('code') in (502, 503, 504):
            raise TransientProviderError('Upstream provider HTTP ' + str(response['error']['code']) + '; raw response preserved')
        raise GuardError('Provider returned an error; raw response saved locally')
    if response.get('model') != MODEL:
        raise GuardError('Response model differs from pinned free model')
    if response.get('provider', '').lower() != PROVIDER:
        raise GuardError('Response provider differs from pinned provider')
    usage = response.get('usage', {})
    if not zero(usage.get('cost')):
        raise GuardError('Usage cost is missing or nonzero; refusing to continue')
    def check_costs(obj):
        for key, value in obj.items():
            if isinstance(value, dict):
                check_costs(value)
            elif 'cost' in key.lower() and not zero(value):
                raise GuardError('Response includes an unknown/nonzero cost field')
    check_costs(usage)
    return usage


def api(path, key=None, payload=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    request = urllib.request.Request(API + path, headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Never interpolate request headers, credentials, or private prompts.
        if error.code == 429:
            raise RateLimited('HTTP 429; stop and resume after provider cooldown. Retry-After=' +
                              str(error.headers.get('Retry-After', 'unspecified'))) from None
        if error.code in (502, 503, 504):
            raise TransientProviderError('OpenRouter HTTP ' + str(error.code) + '; no model fallback') from None
        raise GuardError('OpenRouter HTTP ' + str(error.code) + '; no automatic retry or fallback') from None


def preflight(key, reasoning=False):
    models = api('/models')['data']
    model = next((x for x in models if x['id'] == MODEL), None)
    if model is None or not MODEL.endswith(':free'):
        raise GuardError('Pinned free model is unavailable')
    require_free(model.get('pricing'))
    endpoints = api('/models/' + MODEL + '/endpoints')['data']['endpoints']
    candidates = [x for x in endpoints if x.get('tag') == PROVIDER and x.get('model_id') == MODEL]
    if not candidates:
        raise GuardError('Pinned free provider endpoint is unavailable')
    for endpoint in candidates:
        require_free(endpoint.get('pricing'))
        if reasoning and 'reasoning' not in endpoint.get('supported_parameters', []):
            raise GuardError('Pinned endpoint does not advertise reasoning support')
    if reasoning and not model.get('reasoning', {}).get('supports_max_tokens'):
        raise GuardError('Model no longer advertises a bounded reasoning token budget')
    account = api('/key', key)['data']
    quota = account.get('free_model_daily_requests')
    if not isinstance(quota, dict) or not isinstance(quota.get('remaining'), (int, float)):
        raise GuardError('No reliable free request quota is available')
    if quota['remaining'] < 1:
        raise RateLimited('Free daily request quota exhausted; resume after UTC reset')
    return {'checked_at': now(), 'model': MODEL, 'provider': PROVIDER,
            'pricing': model['pricing'], 'endpoint_pricing': [x['pricing'] for x in candidates],
            'free_model_daily_requests': quota, 'key_limit_remaining': account.get('limit_remaining'),
            'reasoning_metadata': model.get('reasoning'),
            'endpoint_supported_parameters': [x.get('supported_parameters', []) for x in candidates]}


def make_request(system, context, allow_retention=False, correction=None, candidate=None, inference_config=None):
    user = json.dumps(context, ensure_ascii=False, separators=(',', ':'))
    allowed = [m['id'] for m in context.get('current_turn_prose', [])]
    user += '\nONLY these current-turn message IDs may be cited as evidence: ' + json.dumps(allowed)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    if correction:
        if candidate:
            messages.append({'role': 'assistant', 'content': candidate})
        messages.append({'role': 'user', 'content': 'The previous candidate failed strict local validation: ' + correction +
                         '\nReturn the corrected complete JSON patch. Every evidence quote must be copied verbatim from one current source message and be at most 300 characters. Preserve valid facts and IDs.'})
    configuration = inference_config or {'max_tokens': 8192, 'reasoning': {'enabled': False}}
    return {'model': MODEL, 'messages': messages,
            'temperature': 0, 'max_tokens': configuration['max_tokens'], 'reasoning': configuration['reasoning'],
            'provider': {'only': [PROVIDER], 'allow_fallbacks': False,
                         'data_collection': 'allow' if allow_retention else 'deny',
                         'max_price': dict(ZERO_PRICES)}}


def parse_patch(content):
    if not isinstance(content, str):
        raise ValueError('Model response contained no text')
    content = content.strip()
    if content.startswith('```'):
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError('Expected one JSON object')
    return value


def validation_feedback(error):
    if hasattr(error, 'validator'):
        return json.dumps({'path': list(error.absolute_path), 'validator': error.validator,
                           'constraint': error.validator_value,
                           'actual_length': len(error.instance) if isinstance(error.instance, (str, list, dict)) else None})
    return str(error)[:1600]


def complete_feedback(p, content, turn, graph, error):
    """Report all observable evidence problems without changing the candidate."""
    report = {'first_error': validation_feedback(error),
              'allowed_current_message_ids': [m['id'] for m in turn['messages']], 'errors': []}
    try:
        candidate = parse_patch(content)
    except ValueError:
        return json.dumps(report)
    for issue in p.jsonschema.Draft202012Validator(p.SCHEMA).iter_errors(candidate):
        report['errors'].append({'path': list(issue.absolute_path), 'rule': issue.validator,
                                 'constraint': issue.validator_value})
        if len(report['errors']) >= 12:
            break
    messages = {m['id']: m['text'] for m in turn['messages']}
    groups = [(['actor_evidence'], candidate.get('actor_evidence', []))]
    for key in ('tasks', 'decisions', 'relationships'):
        objects = candidate.get(key, [])
        if not isinstance(objects, list):
            continue
        for index, obj in enumerate(objects):
            if not isinstance(obj, dict):
                continue
            groups.append(([key, index, 'evidence'], obj.get('evidence', [])))
            blocker = obj.get('changes', {}).get('blocker') if isinstance(obj.get('changes', {}), dict) else None
            if isinstance(blocker, dict) and blocker.get('waiting_on_actor') is not None and (not isinstance(blocker['waiting_on_actor'], str) or blocker['waiting_on_actor'] not in graph['actors']):
                report['errors'].append({'path': [key, index, 'changes', 'blocker', 'waiting_on_actor'],
                                         'rule': 'Unknown actor ID: copy an exact catalog actor ID or use null if unsupported',
                                         'invalid_id': blocker['waiting_on_actor']})
    for path, evidence in groups:
        if not isinstance(evidence, list):
            continue
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                continue
            mid, quote = item.get('message_id'), item.get('quote')
            if not isinstance(mid, str) or mid not in messages:
                report['errors'].append({'path': path + [index], 'rule': 'Historical/unknown message is forbidden. Remove this unsupported change or cite current-turn evidence.', 'invalid_message_id': mid})
            elif isinstance(quote, str) and quote not in messages[mid]:
                report['errors'].append({'path': path + [index, 'quote'], 'rule': 'Not an exact contiguous substring. Copy a short source span including exact whitespace and punctuation.', 'message_id': mid})
    report['errors'] = report['errors'][:48]
    return json.dumps(report, ensure_ascii=False)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def consumes_candidate_attempt(job):
    """Confirmed pre-generation outages do not consume model repair attempts.

    Unknown/interrupted requests are conservatively counted. Every request and
    raw error remains on disk; this changes accounting, never removes history.
    """
    response_path = job / 'response.json'
    if response_path.exists():
        response = json.loads(response_path.read_text())
        error = response.get('error')
        has_candidate = any(choice.get('message', {}).get('content') for choice in response.get('choices', []))
        if isinstance(error, dict) and error.get('code') in (429, 502, 503, 504) and not has_candidate:
            return False
        return True
    error_path = job / 'provider-error.json'
    if error_path.exists():
        error = json.loads(error_path.read_text())
        if error.get('error_kind') in ('provider_unavailable', 'rate_limited') or re.search(r'HTTP (429|502|503|504)\b', error.get('error', '')):
            return False
    return (job / 'request.json').exists()


def freeze_config(p, args):
    enabled = getattr(args, 'reasoning', 'disabled') == 'enabled'
    value = {'model': MODEL, 'provider': PROVIDER, 'temperature': 0,
             'max_tokens': 16384 if enabled else 8192,
             'reasoning': {'enabled': True, 'max_tokens': 4096, 'exclude': True} if enabled else {'enabled': False},
             'max_inference_attempts_per_turn': 1 + getattr(args, 'max_corrections', 4),
             'prompt_contract_version': PROMPT_CONTRACT_VERSION,
             'evidence_rules_sha256': hashlib.sha256(EVIDENCE_RULES.encode()).hexdigest()}
    path = p.DATA / 'inference-config.json'
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise GuardError('Frozen inference configuration drift; use a separate experiment dataset')
    else:
        if enabled and p.ledger():
            if any(not row.get('inference_provenance', {}).get('reasoning_config', {}).get('enabled') for row in p.ledger()):
                raise GuardError('Refusing to enable reasoning in an existing non-reasoning ledger')
        p.atomic(path, value)
    return value


def prepare(p, source):
    directory = p.DATA
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    frozen = {'turns.json': source / 'turns.json', 'actors.json': source / 'actors.json',
              'extract-turn.txt': ROOT / 'prompts/v02/extract-turn.txt',
              'turn-patch.schema.json': ROOT / 'schemas/v02/turn-patch.schema.json'}
    for name, origin in frozen.items():
        target = directory / name
        if target.exists():
            if sha(target) != sha(origin):
                raise GuardError('Frozen source/schema/prompt drift: ' + name)
        else:
            shutil.copyfile(origin, target)
    provenance = directory / 'comparison-source.json'
    current = {'source_dataset': source.name, 'model': MODEL,
               'sha256': {name: sha(origin) for name, origin in frozen.items()},
               'pipeline_sha256': sha(ROOT / 'scripts/v02/pipeline.py')}
    if provenance.exists():
        if json.loads(provenance.read_text()) != current:
            raise GuardError('Source or pipeline changed since comparison began')
    else:
        p.atomic(provenance, current)
    manifest = directory / 'manifest.json'
    if not manifest.exists():
        value = json.loads((source / 'manifest.json').read_text())
        value.update(dataset_id=directory.name, teacher_model=MODEL, teacher_engine='openrouter-free',
                     label_status='machine_draft', comparison_source=source.name,
                     comparison_mode='independent causal rollout from empty task graph',
                     source_sha256=current['sha256']['turns.json'])
        p.atomic(manifest, value)
    if json.loads(manifest.read_text()).get('teacher_model') != MODEL:
        raise GuardError('Existing dataset has a different teacher')


def status(p, state, **fields):
    rows = p.ledger()
    result = {'state': state, 'completed': len(rows), 'total': len(p.read('turns.json')),
              'model': MODEL, 'updated_at': now(), 'usd_ceiling': 0, **fields}
    p.atomic(p.DATA / 'free-runner-status.json', result)
    p.atomic(p.DATA / 'status.json', result)
    review = p.read('review.json')
    if review:
        review['status'] = result
        p.atomic(p.DATA / 'review.json', review)
    print(json.dumps(result), flush=True)


def run(p, key, args):
    rows = p.ledger()
    turns = p.read('turns.json')
    if len(rows) > len(turns):
        raise GuardError('Ledger is longer than frozen source')
    graph = p.initial()
    for turn, row in zip(turns, rows):
        if row['turn_id'] != turn['id'] or row['model'] != MODEL:
            raise GuardError('Unexpected ledger order or teacher')
        p.check(row['patch'], turn, graph)
        graph, _ = p.apply(graph, row['patch'], turn)
    prompt = (p.DATA / 'extract-turn.txt').read_text()
    system = prompt + EVIDENCE_RULES + '\nReturn JSON only, matching this schema:\n' + json.dumps(p.SCHEMA, separators=(',', ':'))
    max_attempts = 1 + getattr(args, 'max_corrections', 4)
    config = freeze_config(p, args)
    reasoning = config['reasoning']['enabled']
    requests = 0
    last_request = 0.0
    for turn in turns[len(rows):]:
        if (p.DATA / 'STOP').exists():
            status(p, 'stopped', reason='Local STOP file exists')
            return
        context = p.context(turn, graph, rows)
        correction = None
        candidate = None
        # An explicit manual resume after a validation stop can reuse its exact
        # rejected output as repair context. The supervisor never auto-resumes
        # validation failures. No accepted output is overwritten or recharged.
        previous = sorted((p.DATA / 'openrouter-jobs').glob(turn['id'] + '-*'))
        prior_attempts = sum(consumes_candidate_attempt(job) for job in previous)
        requests_for_turn = len(previous)
        if prior_attempts >= max_attempts:
            raise GuardError('Turn exhausted bounded inference attempts (' + str(max_attempts) + '): ' + turn['id'])
        rejected = [job for job in previous if (job / 'validation-error.json').exists()]
        if rejected:
            failed = json.loads((rejected[-1] / 'response.json').read_text())
            audit_response(failed)
            candidate = failed.get('choices', [{}])[0].get('message', {}).get('content')
            try:
                p.check(parse_patch(candidate), turn, graph)
            except (ValueError, KeyError, IndexError, p.jsonschema.ValidationError) as error:
                correction = complete_feedback(p, candidate, turn, graph, error)
        for attempt in range(prior_attempts, max_attempts):
            if (p.DATA / 'STOP').exists():
                status(p, 'stopped', reason='Local STOP file exists')
                return
            if requests >= args.max_requests:
                status(p, 'request_limit', reason='Invocation request cap reached')
                return
            check = preflight(key, reasoning=reasoning)  # Every inference, including repair, rechecks $0 and quota.
            p.atomic(p.DATA / 'free-preflight.json', check)
            request = make_request(system, context, args.allow_provider_retention, correction, candidate, config)
            job = p.DATA / 'openrouter-jobs' / (turn['id'] + '-' + str(time.time_ns()))
            job.mkdir(parents=True)
            p.atomic(job / 'request.json', request)
            requests_for_turn += 1
            time.sleep(max(0, 3.2 - (time.monotonic() - last_request)))
            last_request = time.monotonic()
            requests += 1
            try:
                response = api('/chat/completions', key, request)
            except (RateLimited, TransientProviderError) as error:
                p.atomic(job / 'provider-error.json', {'at': now(), 'error': str(error), 'model': MODEL,
                          'error_kind': 'provider_unavailable' if isinstance(error, TransientProviderError) else 'rate_limited',
                          'usd_ceiling': 0, 'request_sha256': sha(job / 'request.json')})
                raise
            p.atomic(job / 'response.json', response)
            usage = audit_response(response)
            receipt = {'turn_id': turn['id'], 'attempt': attempt + 1, 'generated_at': now(),
                       'request_number_for_turn': requests_for_turn,
                       'generation_id': response.get('id'), 'model': MODEL,
                       'provider': response.get('provider'), 'usage': usage,
                       'request_sha256': sha(job / 'request.json'), 'job': str(job.relative_to(p.DATA)),
                       'prompt_contract_version': PROMPT_CONTRACT_VERSION,
                       'max_inference_attempts_per_turn': max_attempts,
                       'reasoning_config': config['reasoning'],
                       'inference_config_sha256': sha(p.DATA / 'inference-config.json'),
                       'runner_sha256': sha(Path(__file__))}
            with (p.DATA / 'free-usage.jsonl').open('a') as f:
                f.write(json.dumps(receipt) + '\n'); f.flush(); os.fsync(f.fileno())
            try:
                choice = response['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    raise ValueError('Incomplete or refused response')
                patch = parse_patch(choice['message']['content'])
                p.check(patch, turn, graph)
            except (ValueError, KeyError, IndexError, p.jsonschema.ValidationError) as error:
                candidate = response.get('choices', [{}])[0].get('message', {}).get('content')
                correction = complete_feedback(p, candidate, turn, graph, error)
                p.atomic(job / 'validation-error.json', {'error': correction})
                if attempt == max_attempts - 1:
                    raise GuardError('Turn failed validation after ' + str(max_attempts) + ' bounded attempts: ' + turn['id']) from None
                continue
            row = {'turn_id': turn['id'], 'model': MODEL, 'annotator_kind': 'LLM',
                   'label_status': 'machine_draft', 'generated_at': now(),
                   'input': context, 'patch': patch, 'inference_provenance': receipt}
            with (p.DATA / '.writer.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if len(p.ledger()) != len(rows):
                    raise GuardError('Ledger changed while request was in flight')
                with (p.DATA / 'predictions.jsonl').open('a') as f:
                    f.write(json.dumps(row, ensure_ascii=False) + '\n'); f.flush(); os.fsync(f.fileno())
            rows.append(row)
            graph, _ = p.apply(graph, patch, turn)
            p.publish(rows)
            status(p, 'generated' if len(rows) == len(turns) else 'generating', requests_this_run=requests)
            break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default='nemotron-day1')
    parser.add_argument('--source', default='day1')
    parser.add_argument('--key-file', type=Path, default=DEFAULT_KEY)
    parser.add_argument('--max-requests', type=int, default=990)
    parser.add_argument('--max-corrections', type=int, default=4, help='0..4 corrections per turn; previous requests count toward this bound')
    parser.add_argument('--reasoning', choices=['disabled', 'enabled'], default='disabled')
    parser.add_argument('--run', action='store_true', help='Send causal corpus context; otherwise only prepare/preflight')
    parser.add_argument('--allow-provider-retention', action='store_true',
                        help='Permit provider logging; only after corpus is cleared for NVIDIA trial endpoint terms')
    args = parser.parse_args()
    if not re.fullmatch(r'nemotron-[a-zA-Z0-9_-]+', args.dataset):
        parser.error('Comparison dataset must start nemotron-')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.source) or args.source == args.dataset:
        parser.error('Invalid source dataset')
    if args.max_requests < 1 or args.max_requests > 1000:
        parser.error('--max-requests must be 1..1000')
    if not 0 <= args.max_corrections <= 4:
        parser.error('--max-corrections must be 0..4')
    if args.run and not args.allow_provider_retention:
        parser.error('--run requires explicit --allow-provider-retention after source handling is cleared; no corpus request was sent')
    os.umask(0o077)
    os.environ['CCT_V02_DATASET'] = args.dataset
    import pipeline as p
    p.DATA.mkdir(parents=True, exist_ok=True)
    with (p.DATA / '.free-runner.lock').open('a') as runner_lock:
        try:
            fcntl.flock(runner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another free comparison runner is active')
        prepare(p, ROOT / 'data/v02' / args.source)
        try:
            configuration = freeze_config(p, args)
            manifest = p.read('manifest.json')
            manifest['inference_config'] = configuration
            p.atomic(p.DATA / 'manifest.json', manifest)
            key = os.environ.get('OPENROUTER_API_KEY') or args.key_file.read_text().strip()
            if not key or '\n' in key:
                raise GuardError('Missing or invalid API credential')
            check = preflight(key, reasoning=configuration['reasoning']['enabled'])
            p.atomic(p.DATA / 'free-preflight.json', check)
            p.publish()
            if args.run:
                run(p, key, args)
            else:
                status(p, 'prepared', free_preflight=check,
                       privacy_note='NVIDIA free endpoint logs submissions; private corpus has not been sent by prepare')
        except TransientProviderError as error:
            status(p, 'provider_unavailable', reason=str(error))
            return 3
        except RateLimited as error:
            tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
            status(p, 'rate_limited', reason=str(error), suggested_resume_at=tomorrow.isoformat())
            return 3
        except (GuardError, OSError, ValueError) as error:
            status(p, 'stopped', reason=str(error))
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
