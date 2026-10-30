#!/usr/bin/env python3
"""Autonomous $0 cooking of free OpenRouter models over the full Day1 source.

Each model is its own dataset and an independent causal rollout from an empty
graph; turns stay sequential within a model. Lanes run in parallel, one
supervisor per lane, one child process per model (the pipeline binds its
dataset at import). Free routes only: pinned model and provider, zero prices,
no fallback, cost audit on every response. Strict validation is unchanged;
only the recorded deterministic repairs from the Haiku runner are applied.

  free_cooker.py plan                       # offline: lanes, datasets, progress
  free_cooker.py supervise --lane A --run   # long-running lane supervisor
  free_cooker.py cook --model-key X --run   # one model until done/stalled/cooldown
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / 'data/v02'
SUPERVISOR = DATA_ROOT / 'free-cooker'
sys.path.insert(0, str(Path(__file__).resolve().parent))

PROMPT_CONTRACT_VERSION = 'free-cooker-current-turn-evidence-v1'
MIN_REQUEST_SPACING = 3.2
MAX_CORRECTIONS = 4
# First attempt is greedy; corrections sample, since a temperature-0 resend of the same
# candidate and report returns the same answer (turn-0010: 4 identical retries).
CORRECTION_TEMPERATURE = 0.4
# Append-only: a running dataset may gain repairs (logged as an amendment), never lose or reorder them.
REPAIRS = ['repoint exact quote to its unique current message', 'restore source quote marks when the match is unique',
           'restore Markdown bold delimiters (Luna policy)', 'split concatenated exact sentences (Luna policy)',
           'restore missing actor: prefix of a unique known actor ID',
           'split an over-long verbatim quote into consecutive exact pieces of at most 300 characters',
           'restore stripped Markdown emphasis/code marks (* and backtick) when the match is unique',
           'restore source letter case when the quote matches exactly once ignoring case',
           'drop an existing-task update whose every quote is verbatim earlier-turn text and absent from the current turn',
           'split a quote elided with ... into its pieces when every piece is exact text of one current message',
           'elided pieces may differ from the source only in whitespace runs; the unique exact source span is restored',
           'split an over-long non-verbatim quote at clause ends when every piece is exact text, in order, of one current message',
           'split a quote around one unmarked gap of at most 3 skipped source words into two exact pieces of at least 3 words',
           'JSON syntax only: swap a closing bracket that mismatches its opener (} vs ]) when the result then parses',
           'over-long quote that joins separate passages: segment into maximal exact ordered word-boundary pieces (>=3 words each, nothing dropped)',
           'drop an existing-task update whose every quote is absent from the source and copied from the update itself',
           'drop an existing-task update whose every quote joins at most 3 exact earlier-turn passages (>=3 words each) and is absent from the current turn',
           'restore stripped Markdown marks and curly quote marks together when the match is unique',
           'drop an existing-task update whose every quote is absent from the current turn and contains an exact earlier-turn run of at least 8 words',
           'restore stripped Markdown marks when the quote also collapsed source whitespace runs and the match is unique',
           'elided pieces may also differ from the source by dropped * and backtick marks; the unique source span is restored',
           'drop a delegates_to relationship whose task_ref names a decision of the same patch, not a task (the decision is kept)',
           'drop a depends_on/is_sub_task relationship with a known actor ID as an endpoint (both tasks and other edges are kept)']

# CKL-approved free routes (2026-10-29). Order inside a lane is the cooking order.
MODELS = {
    'nemotron-ultra': {'model': 'nvidia/nemotron-3-ultra-550b-a55b:free', 'provider': 'nvidia', 'dataset': 'nemotron-day1', 'lane': 'A'},
    'nemotron-super-vl': {'model': 'nvidia/nemotron-3.5-super-vl:free', 'provider': 'nvidia', 'dataset': 'nemotron35-super-vl-day1', 'lane': 'A'},
    'nemotron-lightning': {'model': 'nvidia/nemotron-3.5-lightning:free', 'provider': 'nvidia/nvfp4', 'dataset': 'nemotron35-lightning-day1', 'lane': 'A'},
    'nemotron-nano-reasoning': {'model': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free', 'provider': 'nvidia', 'dataset': 'nemotron3-nano-reasoning-day1', 'lane': 'A', 'reasoning': True},
    'gemma-31b': {'model': 'google/gemma-4-31b-it:free', 'provider': 'google-ai-studio', 'dataset': 'gemma4-31b-day1', 'lane': 'B'},
    'gemma-26b': {'model': 'google/gemma-4-26b-a4b-it:free', 'provider': 'google-ai-studio', 'dataset': 'gemma4-26b-day1', 'lane': 'B'},
    # v0.3-lean context (lean_context.py), fresh datasets from turn 1; never mixed with v0.2-full rows.
    'nemotron-ultra-lean': {'model': 'nvidia/nemotron-3-ultra-550b-a55b:free', 'provider': 'nvidia', 'dataset': 'nemotron-ultra-lean-day1', 'lane': 'A', 'context': 'lean'},
    'gemma-31b-lean': {'model': 'google/gemma-4-31b-it:free', 'provider': 'google-ai-studio', 'dataset': 'gemma4-31b-lean-day1', 'lane': 'A', 'context': 'lean'},
}
# CKL 2026-10-29: one lane, strictly sequential. Dropped models keep their data and STOP files.
LANES = {'A': ['nemotron-ultra-lean', 'gemma-31b-lean']}
DROPPED = ['nemotron-ultra', 'gemma-31b', 'nemotron-super-vl', 'nemotron-lightning', 'nemotron-nano-reasoning', 'gemma-26b']


def contract(spec):
    import lean_context as L
    return L.VERSION if spec.get('context') == 'lean' else PROMPT_CONTRACT_VERSION
STALL_RECHECK = timedelta(minutes=30)
STALL_GRACE = timedelta(hours=6)

# Child exit codes, read by the lane supervisor.
EXIT_DONE, EXIT_STALLED, EXIT_GUARD, EXIT_COOLDOWN, EXIT_STOP = 0, 10, 11, 20, 30


class GuardError(RuntimeError):
    pass


class Cooldown(RuntimeError):
    def __init__(self, reason, until):
        super().__init__(reason)
        self.until = until


def utc():
    return datetime.now(timezone.utc)


def now():
    return utc().isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def atomic(path, value):
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')
    os.replace(temp, path)


def next_utc_midnight(at=None):
    at = at or utc()
    return (at + timedelta(days=1)).replace(hour=0, minute=2, second=0, microsecond=0)


# ---------------------------------------------------------------- free-route guards

def audit_response(response, spec):
    """openrouter_free.audit_response, parametrized by the pinned model/provider."""
    import openrouter_free as o
    if response.get('error'):
        usage = response.get('usage')
        if usage is not None and (not isinstance(usage, dict) or not o.zero(usage.get('cost'))):
            raise GuardError('Provider error included missing/nonzero usage cost; refusing retry')
        code = response['error'].get('code')
        if code in (429,):
            raise Cooldown('Upstream rate limit (in-body 429)', utc() + timedelta(minutes=2))
        if code in (502, 503, 504):
            raise Cooldown('Upstream provider HTTP ' + str(code), utc() + timedelta(minutes=5))
        raise GuardError('Provider returned an error; raw response saved locally')
    if response.get('model') != spec['model']:
        raise GuardError('Response model differs from pinned free model')
    if (response.get('provider') or '').lower() != provider_name(spec).lower():
        raise GuardError('Response provider differs from pinned provider')
    usage = response.get('usage', {})
    if not o.zero(usage.get('cost')):
        raise GuardError('Usage cost is missing or nonzero; refusing to continue')
    pending = [usage]
    while pending:
        for key, value in pending.pop().items():
            if isinstance(value, dict):
                pending.append(value)
            elif 'cost' in key.lower() and not o.zero(value):
                raise GuardError('Response includes an unknown/nonzero cost field')
    return usage


def provider_name(spec):
    # Endpoint tags can carry a quantization suffix; responses name the provider only.
    return spec.get('provider_name') or {'nvidia': 'Nvidia', 'nvidia/nvfp4': 'Nvidia', 'google-ai-studio': 'Google AI Studio'}.get(spec['provider'], spec['provider'])


def api(path, key=None, payload=None):
    import openrouter_free as o
    try:
        return o.api(path, key, payload)
    except o.RateLimited as error:
        raise Cooldown(str(error), utc() + timedelta(minutes=2)) from None
    except o.TransientProviderError as error:
        raise Cooldown(str(error), utc() + timedelta(minutes=5)) from None
    except o.GuardError as error:
        if re.search(r'HTTP (404|408|500)\b', str(error)):
            raise Cooldown(str(error), utc() + timedelta(minutes=15)) from None
        raise GuardError(str(error)) from None


def preflight(key, spec):
    import openrouter_free as o
    model = next((x for x in api('/models')['data'] if x['id'] == spec['model']), None)
    if model is None or not spec['model'].endswith(':free'):
        raise Cooldown('Pinned free model is not listed right now', utc() + timedelta(minutes=30))
    o.require_free(model.get('pricing'))
    endpoints = api('/models/' + spec['model'] + '/endpoints')['data']['endpoints']
    candidates = [x for x in endpoints if x.get('tag') == spec['provider'] and x.get('model_id', spec['model'].split(':')[0]) in (spec['model'], spec['model'].split(':')[0])]
    if not candidates:
        raise Cooldown('Pinned free provider endpoint is not listed right now', utc() + timedelta(minutes=30))
    for endpoint in candidates:
        o.require_free(endpoint.get('pricing'))
    quota = api('/key', key)['data'].get('free_model_daily_requests')
    if not isinstance(quota, dict) or not isinstance(quota.get('remaining'), (int, float)):
        raise GuardError('No reliable free request quota is available')
    if quota['remaining'] < 1:
        raise Cooldown('Shared free daily request quota exhausted; waiting for UTC reset', next_utc_midnight())
    return {'checked_at': now(), 'model': spec['model'], 'provider': spec['provider'],
            'pricing': model['pricing'], 'endpoint_status': [x.get('status') for x in candidates],
            'free_model_daily_requests': quota}


# ---------------------------------------------------------------- prompt and validation

def system_prompt(p, spec=None):
    import openrouter_free as o
    import claude_haiku_day1 as h
    import lean_context as L
    lean = L.LEAN_RULES if (spec or {}).get('context') == 'lean' else ''
    return ((p.DATA / 'extract-turn.txt').read_text() + o.EVIDENCE_RULES + h.HAIKU_RULES + lean +
            '\nReturn JSON only, no prose, no code fence, matching this schema:\n' +
            json.dumps(p.SCHEMA, separators=(',', ':')))


def length_hints(correction):
    """Plain-language restatement of maxLength errors; models tend to ignore the JSON report."""
    hints = []
    for raw in re.findall(r'\{[^{}]*maxLength[^{}]*\}', correction.replace('\\"', '"')):
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        path = '.'.join(str(x) for x in item.get('path', []))
        hints.append('The quote at ' + path + ' is ' + str(item.get('actual_length')) + ' characters; the limit is ' +
                     str(item.get('constraint')) + '. Cite a shorter exact substring, or split it into two evidence items.')
    return hints


def make_request(spec, system, context, candidate=None, correction=None):
    user = json.dumps(context, ensure_ascii=False, separators=(',', ':'))
    user += '\nONLY these current-turn message IDs may be cited as evidence: ' + json.dumps([m['id'] for m in context['current_turn_prose']])
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    if correction:
        if candidate:
            messages.append({'role': 'assistant', 'content': candidate})
        text = 'The previous candidate failed strict local validation: ' + correction
        if '"enum"' in correction:
            text += '\nAn enum value was rejected: choose exactly one listed constraint value; map the source wording onto it instead of copying the source word.'
        for hint in length_hints(correction):
            text += '\n' + hint
        text += '\nReturn the corrected COMPLETE JSON patch. Fix every listed error; remove any change you cannot support with an exact current-turn quote. Keep valid facts and IDs.'
        messages.append({'role': 'user', 'content': text})
    reasoning = {'enabled': True, 'max_tokens': 4096, 'exclude': True} if spec.get('reasoning') else {'enabled': False}
    return {'model': spec['model'], 'messages': messages, 'temperature': CORRECTION_TEMPERATURE if correction else 0,
            'max_tokens': 16384 if spec.get('reasoning') else 8192, 'reasoning': reasoning,
            'provider': {'only': [spec['provider']], 'allow_fallbacks': False, 'data_collection': 'allow',
                         'max_price': {'prompt': 0, 'completion': 0, 'request': 0, 'image': 0}}}


def candidate_text(response):
    choice = (response.get('choices') or [{}])[0]
    return choice.get('message', {}).get('content'), choice.get('finish_reason')


def restore_actor_prefixes(patch, graph):
    """Restore a dropped 'actor:' prefix only where 'actor:'+ref is a known actor and the
    bare ref is neither an actor nor a task. IDs are never guessed beyond that literal prefix."""
    actors, tasks, repairs = set(graph.get('actors', {})), set(graph.get('tasks', {})), []
    def fix(obj, key, where):
        value = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(value, str) and value not in actors and value not in tasks and ('actor:' + value) in actors:
            obj[key] = 'actor:' + value
            repairs.append({'path': where, 'original_ref': value, 'restored_ref': obj[key], 'method': 'restored missing actor: prefix of a unique known actor ID'})
    for i, rel in enumerate(patch.get('relationships') or []):
        if not isinstance(rel, dict):
            continue
        if rel.get('type') in ('works_on', 'reviews', 'delegates_to'):
            fix(rel, 'from_ref', ['relationships', i, 'from_ref'])
        if rel.get('type') == 'delegates_to':
            fix(rel, 'to_ref', ['relationships', i, 'to_ref'])
    for i, d in enumerate(patch.get('decisions') or []):
        fix(d, 'made_by_actor_id', ['decisions', i, 'made_by_actor_id'])
    for i, task in enumerate(patch.get('tasks') or []):
        blocker = (task.get('changes') or {}).get('blocker') if isinstance(task, dict) else None
        fix(blocker, 'waiting_on_actor', ['tasks', i, 'changes', 'blocker', 'waiting_on_actor'])
    return repairs


MARKUP = '*`'


def squeeze(text, keep):
    """Drop kept indices that continue a whitespace run, so every run reads as one space."""
    out = []
    for i in keep:
        if not (text[i].isspace() and out and text[out[-1]].isspace()):
            out.append(i)
    return out


def restore_markup(patch, turn, fold=None, ws=False):
    """Restore the exact source span when a quote differs only by dropped '*' or '`' marks
    and matches exactly one place in the current turn. Words are never changed.
    fold: optional one-to-one character map applied to both sides before matching.
    ws: whitespace runs on both sides compare as one space."""
    import claude_haiku_day1 as h
    plain = {}
    for m in turn['messages']:
        keep = [i for i, c in enumerate(m['text']) if c not in MARKUP]
        keep = squeeze(m['text'], keep) if ws else keep
        flat = ''.join(' ' if ws and m['text'][i].isspace() else m['text'][i] for i in keep)
        plain[m['id']] = (flat.translate(fold or {}), keep, m['text'])
    sources = {m['id']: m['text'] for m in turn['messages']}
    repairs = []
    for group in h.evidence_groups(patch):
        for e in group:
            q, mid = (e.get('quote'), e.get('message_id')) if isinstance(e, dict) else (None, None)
            if not isinstance(q, str) or not q or (mid in sources and q in sources[mid]):
                continue
            needle = ''.join(c for c in q if c not in MARKUP).strip().translate(fold or {})
            needle = ' '.join(needle.split()) if ws else needle
            hits = [(k, text.find(needle)) for k, (text, _, _) in plain.items() if needle and text.count(needle) == 1]
            if len(hits) != 1 or sum(text.count(needle) for text, _, _ in plain.values()) != 1:
                continue
            k, lo = hits[0]
            _, keep, original = plain[k]
            restored = original[keep[lo]:keep[lo + len(needle) - 1] + 1]
            repairs.append({'message_id': k, 'original_message_id': mid, 'original_quote': q, 'restored_quote': restored,
                            'method': 'unique exact text with Markdown emphasis/code marks restored' + (' and quote marks' if fold else '') + (' across whitespace runs' if ws else '')})
            e['message_id'], e['quote'] = k, restored
    return repairs


def restore_markup_typography(patch, turn):
    """restore_markup with curly vs straight quote marks folded (turn-0908: ’ and `code` both lost)."""
    import claude_haiku_day1 as h
    return restore_markup(patch, turn, fold=h.TYPOGRAPHY)


def restore_markup_whitespace(patch, turn):
    """restore_markup with whitespace runs compared as one space (turn-1314: ** lost and a blank line collapsed)."""
    return restore_markup(patch, turn, ws=True)


def restore_case(patch, turn):
    """Restore the exact source span when a quote differs only in letter case and matches
    exactly one place in the current turn. Skipped where lowercasing changes length."""
    import claude_haiku_day1 as h
    sources = {m['id']: m['text'] for m in turn['messages']}
    lower = {k: v.lower() for k, v in sources.items() if len(v.lower()) == len(v)}
    repairs = []
    for group in h.evidence_groups(patch):
        for e in group:
            q, mid = (e.get('quote'), e.get('message_id')) if isinstance(e, dict) else (None, None)
            if not isinstance(q, str) or not q or (mid in sources and q in sources[mid]) or len(q.lower()) != len(q):
                continue
            needle = q.lower()
            if sum(v.count(needle) for v in lower.values()) != 1:
                continue
            k = next(k for k, v in lower.items() if needle in v)
            lo = lower[k].find(needle)
            restored = sources[k][lo:lo + len(q)]
            repairs.append({'message_id': k, 'original_message_id': mid, 'original_quote': q, 'restored_quote': restored,
                            'method': 'unique exact text with source letter case restored'})
            e['message_id'], e['quote'] = k, restored
    return repairs


def earlier_text(p, turn):
    """Text of every turn before this one in the frozen source (empty when unavailable)."""
    path = Path(getattr(p, 'DATA', '.')) / 'turns.json'
    if not path.exists() or 'ordinal' not in turn:
        return []
    return [m['text'] for t in json.loads(path.read_text()) if t.get('ordinal', 0) < turn['ordinal'] for m in t['messages']]


def drop_stale_task_updates(patch, turn, earlier):
    """Drop an update to an EXISTING task when every evidence quote is absent from the current
    turn but verbatim in an earlier turn: re-citing old text supports no change this turn.
    New tasks, decisions and relationships are never dropped."""
    current = [m['text'] for m in turn['messages']]
    def stale(e):
        q = e.get('quote') if isinstance(e, dict) else None
        return isinstance(q, str) and q and not any(q in t for t in current) and any(q in t for t in earlier)
    repairs, keep = [], []
    for i, task in enumerate(patch.get('tasks') or []):
        ev = task.get('evidence') if isinstance(task, dict) else None
        if isinstance(task, dict) and task.get('resolution') == 'existing' and isinstance(ev, list) and ev and all(stale(e) for e in ev):
            repairs.append({'dropped_task_update': task.get('ref'), 'index': i, 'quotes': [e['quote'] for e in ev],
                            'method': 'existing-task update cited only earlier-turn text'})
            continue
        keep.append(task)
    if repairs:
        patch['tasks'] = keep
    return repairs


ELISION = re.compile(r'\s*(?:\.\.\.|\u2026)\s*')


def exact_span(piece, text):
    """The piece itself if it is in text, else the unique source span equal to it up to
    whitespace runs; None when absent or ambiguous."""
    if piece in text:
        return piece
    words = piece.split()
    if not words:
        return None
    hits = list(re.finditer(r'\s+'.join(re.escape(w) for w in words), text))
    return hits[0].group(0) if len(hits) == 1 else None


def markup_span(piece, text):
    """exact_span, else the unique source span equal to the piece up to dropped '*'/'`' marks
    and whitespace runs; None when absent or ambiguous."""
    span = exact_span(piece, text)
    if span is not None:
        return span
    words = [w for w in (''.join(c for c in w if c not in MARKUP) for w in piece.split()) if w]
    if not words:
        return None
    m = '[*`]*'
    hits = list(re.finditer((m + r'\s+' + m).join(m.join(re.escape(c) for c in w) for w in words), text))
    return hits[0].group(0) if len(hits) == 1 else None


def split_elided_quotes(patch, turn, span=exact_span):
    """A quote shortened with '...' becomes one evidence item per elided-around piece, only
    when every piece is exact text of the same current message. Words are never changed."""
    import claude_haiku_day1 as h
    sources = {m['id']: m['text'] for m in turn['messages']}
    repairs = []
    for group in h.evidence_groups(patch):
        at = 0
        while at < len(group):
            e = group[at]
            q, mid = (e.get('quote'), e.get('message_id')) if isinstance(e, dict) else (None, None)
            if not isinstance(q, str) or (mid in sources and q in sources[mid]):
                at += 1; continue
            pieces = [x for x in ELISION.split(q) if x.strip()]
            found = {k: [span(x, text) for x in pieces] for k, text in sources.items()} if len(pieces) > 1 else {}
            homes = [k for k, spans in found.items() if all(spans)]
            if len(homes) != 1:
                at += 1; continue
            pieces = found[homes[0]]
            group[at:at + 1] = [{**e, 'message_id': homes[0], 'quote': x} for x in pieces]
            repairs.append({'message_id': homes[0], 'original_quote': q, 'pieces': pieces, 'method': 'split elided quote into exact pieces' + ('' if span is exact_span else ' (markup restored)')})
            at += len(pieces)
    return repairs


CLAUSE = re.compile(r'(?<=[.;:!?)])\s+|;\s+|,\s+')


def ordered_spans(pieces, text):
    """Exact source spans for pieces appearing in this order in text, else None."""
    out, at = [], 0
    for x in pieces:
        span = exact_span(x, text[at:])
        if span is None:
            return None
        at += text[at:].find(span) + len(span)
        out.append(span)
    return out


def segment_exact(q, text, min_words=3, max_pieces=6):
    """Greedy longest exact word-prefix pieces, each later in text than the last; every word
    of q is kept. None unless that yields 2..max_pieces pieces of >= min_words words."""
    words, pieces, at = q.split(), [], 0
    while words:
        for k in range(len(words), min_words - 1, -1):
            span = exact_span(' '.join(words[:k]), text[at:])
            if span:
                break
        else:
            return None
        pieces.append(span); at += text[at:].find(span) + len(span); words = words[k:]
        if len(pieces) > max_pieces:
            return None
    return pieces if len(pieces) > 1 else None


def split_unmarked_omissions(patch, turn, limit=300):
    """An over-long quote that silently skips source words is split at clause ends; it is kept
    only when every piece (trailing punctuation trimmed if needed) is exact text, in order."""
    import claude_haiku_day1 as h
    sources = {m['id']: m['text'] for m in turn['messages']}
    repairs = []
    for group in h.evidence_groups(patch):
        at = 0
        while at < len(group):
            e = group[at]
            q, mid = (e.get('quote'), e.get('message_id')) if isinstance(e, dict) else (None, None)
            if not isinstance(q, str) or len(q) <= limit or any(q in t for t in sources.values()):
                at += 1; continue
            raw = [x.strip() for x in CLAUSE.split(q) if x.strip()]
            pieces = [x if any(exact_span(x, t) for t in sources.values()) else x.rstrip('.,;:)') for x in raw]
            homes = [k for k, t in sources.items() if len(pieces) > 1 and all(pieces) and ordered_spans(pieces, t)]
            if not homes:  # a join of separate exact passages: segment at word boundaries instead
                segs = {k: segment_exact(q, t) for k, t in sources.items()}
                homes = [k for k, v in segs.items() if v]
                if len(homes) == 1:
                    pieces = segs[homes[0]]
            if len(homes) != 1:
                at += 1; continue
            pieces = ordered_spans(pieces, sources[homes[0]])
            group[at:at + 1] = [{**e, 'message_id': homes[0], 'quote': x} for x in pieces]
            repairs.append({'message_id': homes[0], 'original_quote': q, 'pieces': pieces,
                            'method': 'split over-long quote with unmarked omission into exact ordered pieces'})
            at += len(pieces)
    return repairs


def split_small_gap(patch, turn, max_gap=3, min_words=3):
    """A quote that skips a few source words (e.g. a dropped 'the') becomes two exact pieces, only
    when one split point gives both halves exact, in order, in one message, with a short gap."""
    import claude_haiku_day1 as h
    sources = {m['id']: m['text'] for m in turn['messages']}
    repairs = []
    for group in h.evidence_groups(patch):
        for at, e in enumerate(list(group)):
            q = e.get('quote') if isinstance(e, dict) else None
            if not isinstance(q, str) or any(q in t for t in sources.values()):
                continue
            words, found = q.split(), []
            for k in range(min_words, len(words) - min_words + 1):
                a, b = ' '.join(words[:k]), ' '.join(words[k:])
                for mid, text in sources.items():
                    spans = ordered_spans([a, b], text)
                    if spans:
                        lo = text.find(spans[0]) + len(spans[0])
                        gap = text[lo:text.find(spans[1], lo)].split()
                        if 0 < len(gap) <= max_gap:
                            found.append((mid, spans, gap))
            if len(found) != 1:
                continue
            mid, spans, gap = found[0]
            i = group.index(e)
            group[i:i + 1] = [{**e, 'message_id': mid, 'quote': x} for x in spans]
            repairs.append({'message_id': mid, 'original_quote': q, 'pieces': spans, 'skipped_source_words': gap,
                            'method': 'split quote around one short unmarked gap into two exact pieces'})
    return repairs


def earlier_pieces(q, texts, min_words=3, max_pieces=3):
    """Greedy longest exact word-prefix pieces of q, each found in some earlier text; None if not coverable."""
    words, pieces = q.split(), []
    while words:
        for k in range(len(words), min_words - 1, -1):
            if any(exact_span(' '.join(words[:k]), t) for t in texts):
                break
        else:
            return None
        pieces.append(' '.join(words[:k])); words = words[k:]
        if len(pieces) > max_pieces:
            return None
    return pieces


def drop_joined_stale_task_updates(patch, turn, earlier):
    """Like drop_stale_task_updates, but a quote may join up to 3 exact earlier-turn passages."""
    current = [m['text'] for m in turn['messages']]
    def stale(e):
        q = e.get('quote') if isinstance(e, dict) else None
        return isinstance(q, str) and q and not any(q in t for t in current) and earlier_pieces(q, earlier) is not None
    repairs, keep = [], []
    for i, task in enumerate(patch.get('tasks') or []):
        ev = task.get('evidence') if isinstance(task, dict) else None
        if isinstance(task, dict) and task.get('resolution') == 'existing' and isinstance(ev, list) and ev and all(stale(e) for e in ev):
            repairs.append({'dropped_task_update': task.get('ref'), 'index': i, 'quotes': [e['quote'] for e in ev],
                            'method': 'existing-task update cited only joined earlier-turn passages'})
            continue
        keep.append(task)
    if repairs:
        patch['tasks'] = keep
    return repairs


def drop_decision_task_ref_relationships(patch):
    """Drop a delegates_to edge whose task_ref is a decision ref of this patch (turn-1496: a
    delegation pointed at new:d1, a decision). The decision itself is kept untouched."""
    decisions = {d.get('ref') for d in patch.get('decisions') or [] if isinstance(d, dict)}
    tasks = {t.get('ref') for t in patch.get('tasks') or [] if isinstance(t, dict)}
    repairs, keep = [], []
    for rel in patch.get('relationships') or []:
        ref = rel.get('task_ref') if isinstance(rel, dict) else None
        if isinstance(rel, dict) and rel.get('type') == 'delegates_to' and ref in decisions and ref not in tasks:
            repairs.append({'dropped_relationship': [rel.get('type'), rel.get('from_ref'), rel.get('to_ref')], 'task_ref': ref,
                            'method': 'delegates_to task_ref named a decision, not a task'})
            continue
        keep.append(rel)
    if repairs:
        patch['relationships'] = keep
    return repairs


def drop_actor_endpoint_task_edges(patch, graph):
    """Drop a task-to-task edge (depends_on, is_sub_task) that has an actor as an endpoint
    (turn-1519: new:t1 depends_on an actor). Tasks and other edges are kept untouched."""
    actors = set(graph.get('actors') or {})
    repairs, keep = [], []
    for rel in patch.get('relationships') or []:
        if isinstance(rel, dict) and rel.get('type') in ('depends_on', 'is_sub_task') and (rel.get('from_ref') in actors or rel.get('to_ref') in actors):
            repairs.append({'dropped_relationship': [rel.get('type'), rel.get('from_ref'), rel.get('to_ref')],
                            'method': 'task-to-task edge had an actor endpoint'})
            continue
        keep.append(rel)
    if repairs:
        patch['relationships'] = keep
    return repairs


ANCHOR_WORDS = 8


def drop_anchored_stale_task_updates(patch, turn, earlier):
    """Drop an update to an EXISTING task when every quote is absent from the current turn and
    each contains an exact run of ANCHOR_WORDS+ words from an earlier turn (turn-1127: a garbled
    re-citation of turn 1116). New tasks, decisions and relationships are never dropped."""
    current = [m['text'] for m in turn['messages']]
    def anchored(e):
        q = e.get('quote') if isinstance(e, dict) else None
        if not isinstance(q, str) or not q or any(q in t for t in current):
            return False
        w = q.split()
        runs = [' '.join(w[i:i + ANCHOR_WORDS]) for i in range(len(w) - ANCHOR_WORDS + 1)]
        return any(r in t for r in runs for t in earlier) and not any(r in t for r in runs for t in current)
    repairs, keep = [], []
    for i, task in enumerate(patch.get('tasks') or []):
        ev = task.get('evidence') if isinstance(task, dict) else None
        if isinstance(task, dict) and task.get('resolution') == 'existing' and isinstance(ev, list) and ev and all(anchored(e) for e in ev):
            repairs.append({'dropped_task_update': task.get('ref'), 'index': i, 'quotes': [e['quote'] for e in ev],
                            'method': 'existing-task update re-cited earlier-turn text (8+ word exact runs)'})
            continue
        keep.append(task)
    if repairs:
        patch['tasks'] = keep
    return repairs


def drop_self_quoted_task_updates(patch, turn):
    """Drop an update to an EXISTING task when every evidence quote is absent from the current turn
    and is the model's own wording copied from that update: nothing in the source supports it."""
    current = [m['text'] for m in turn['messages']]
    repairs, keep = [], []
    for i, task in enumerate(patch.get('tasks') or []):
        ev = task.get('evidence') if isinstance(task, dict) else None
        own = json.dumps({k: v for k, v in task.items() if k != 'evidence'}, ensure_ascii=False) if isinstance(task, dict) else ''
        quotes = [e.get('quote') for e in ev if isinstance(e, dict)] if isinstance(ev, list) else []
        if (task.get('resolution') == 'existing' and quotes and len(quotes) == len(ev) and
                all(isinstance(q, str) and q and not any(q in t for t in current) and q in own for q in quotes)):
            repairs.append({'dropped_task_update': task.get('ref'), 'index': i, 'quotes': quotes,
                            'method': 'existing-task update cited only its own wording'})
            continue
        keep.append(task)
    if repairs:
        patch['tasks'] = keep
    return repairs


def split_long_quotes(patch, turn, limit=300):
    """Split a verbatim quote longer than the schema limit at sentence or line ends into
    consecutive pieces that are each still an exact substring of the same message."""
    import claude_haiku_day1 as h
    sources = {m['id']: m['text'] for m in turn['messages']}
    repairs = []
    for group in h.evidence_groups(patch):
        for e in list(group):
            q, mid = (e.get('quote'), e.get('message_id')) if isinstance(e, dict) else (None, None)
            if not isinstance(q, str) or len(q) <= limit or mid not in sources or q not in sources[mid]:
                continue
            pieces, rest = [], q
            while len(rest) > limit:
                cut = max(rest.rfind(sep, 0, limit) for sep in ('. ', '\n', '; ', ': ', ', ', ' '))
                if cut <= 0:
                    pieces = None
                    break
                pieces.append(rest[:cut + 1].strip())
                rest = rest[cut + 1:]
            if pieces is None:
                continue
            pieces.append(rest.strip())
            pieces = [x for x in pieces if x]
            if not all(len(x) <= limit and x in sources[mid] for x in pieces):
                continue
            at = group.index(e)
            group[at:at + 1] = [{**e, 'quote': x} for x in pieces]
            repairs.append({'message_id': mid, 'original_quote_length': len(q), 'pieces': len(pieces), 'method': 'split over-long verbatim quote into consecutive exact pieces'})
    return repairs


def swap_mismatched_closers(text):
    """Swap '}' for ']' (or back) where it closes the other bracket type; strings are skipped.
    Returns (text, [offsets]); content is never changed."""
    out, stack, swaps, in_str, esc = list(text or ''), [], [], False, False
    for i, c in enumerate(out):
        if in_str:
            esc, in_str = (not esc and c == '\\'), (in_str if esc or c != '"' else False)
            continue
        if c == '"':
            in_str = True
        elif c in '{[':
            stack.append(c)
        elif c in '}]' and stack:
            want = '}' if stack.pop() == '{' else ']'
            if c != want:
                out[i] = want; swaps.append(i)
    return ''.join(out), swaps


def validate(p, text, turn, graph, finish_reason='stop', aliases=None):
    import claude_haiku_day1 as h
    import lean_context as L
    if finish_reason not in ('stop', None):
        raise ValueError('Incomplete or refused response (finish_reason=' + str(finish_reason) + ')')
    syntax_repairs = []
    try:
        patch = h.parse_patch(text)
    except ValueError:
        fixed, swaps = swap_mismatched_closers(text)
        if not swaps:
            raise
        patch = h.parse_patch(fixed)  # still invalid -> the original error path via ValueError
        syntax_repairs = [{'json_closer_swaps': swaps, 'method': 'swap mismatched JSON closing bracket'}]
    if aliases is not None:
        patch = L.decode_patch(patch, aliases)  # unknown alias raises ValueError -> correction loop
    prefix_repairs = restore_actor_prefixes(patch, graph) + restore_markup(patch, turn) + restore_markup_typography(patch, turn) + restore_markup_whitespace(patch, turn) + restore_case(patch, turn) + split_elided_quotes(patch, turn) + split_elided_quotes(patch, turn, span=markup_span) + split_unmarked_omissions(patch, turn) + split_small_gap(patch, turn) + split_long_quotes(patch, turn) + drop_stale_task_updates(patch, turn, earlier_text(p, turn)) + drop_self_quoted_task_updates(patch, turn) + drop_joined_stale_task_updates(patch, turn, earlier_text(p, turn)) + drop_anchored_stale_task_updates(patch, turn, earlier_text(p, turn)) + drop_decision_task_ref_relationships(patch) + drop_actor_endpoint_task_edges(patch, graph)
    try:
        fixed, repairs = h.deterministic_repairs(patch, turn)
    except (KeyError, TypeError, AttributeError):
        fixed, repairs = patch, []
    p.check(fixed, turn, graph)
    return fixed, syntax_repairs + prefix_repairs + repairs


def feedback(p, text, turn, graph, error, aliases=None):
    import openrouter_free as o
    import claude_haiku_day1 as h
    import lean_context as L
    if aliases is None:
        return o.complete_feedback(p, text, turn, graph, error)
    try:  # judge the decoded patch, then show the model its own aliases again
        text = json.dumps(L.decode_patch(h.parse_patch(text), aliases), ensure_ascii=False)
    except (ValueError, TypeError, AttributeError):
        pass
    return aliases.encode_text(o.complete_feedback(p, text, turn, graph, error))


# ---------------------------------------------------------------- dataset

def prepare(p, spec, source):
    d = p.DATA
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    frozen = {'turns.json': source / 'turns.json', 'actors.json': source / 'actors.json',
              'extract-turn.txt': ROOT / 'prompts/v02/extract-turn.txt',
              'turn-patch.schema.json': ROOT / 'schemas/v02/turn-patch.schema.json'}
    for name, origin in frozen.items():
        target = d / name
        if target.exists():
            if sha(target) != sha(origin):
                raise GuardError('Frozen source/schema/prompt drift: ' + name)
        else:
            shutil.copyfile(origin, target)
    manifest = d / 'manifest.json'
    if not manifest.exists():
        value = json.loads((source / 'manifest.json').read_text())
        value.update(dataset_id=d.name, teacher_model=spec['model'], teacher_engine='openrouter-free',
                     label_status='machine_draft', comparison_source=source.name,
                     comparison_mode='independent causal rollout from empty task graph',
                     source_sha256=sha(frozen['turns.json']), prompt_contract_version=contract(spec))
        atomic(manifest, value)
    if read_json(manifest).get('teacher_model') != spec['model']:
        raise GuardError('Existing dataset has a different teacher')


def freeze_config(p, spec, system):
    """Per-dataset frozen config. An existing ledger written under an older runner
    is continued only with an explicit, logged contract amendment."""
    value = {'model': spec['model'], 'provider': spec['provider'], 'temperature': 0, 'correction_temperature': CORRECTION_TEMPERATURE,
             'reasoning': bool(spec.get('reasoning')), 'max_corrections': MAX_CORRECTIONS,
             'prompt_contract_version': contract(spec), 'system_prompt_sha256': sha_text(system),
             'deterministic_repairs': REPAIRS}
    path = p.DATA / 'free-cooker-config.json'
    current = read_json(path)
    if current is None:
        rows = p.ledger()
        value['started_at_turn_index'] = len(rows)
        if rows:
            value['continues_earlier_contract'] = read_json(p.DATA / 'inference-config.json', {}).get('prompt_contract_version')
        atomic(path, value)
        return value
    base = {k: v for k, v in current.items() if k not in ('started_at_turn_index', 'continues_earlier_contract', 'amendments')}
    if base != value:
        amendable = ('deterministic_repairs', 'correction_temperature')
        old_repairs = base.get('deterministic_repairs', [])
        same_otherwise = {k: v for k, v in base.items() if k not in amendable} == {k: v for k, v in value.items() if k not in amendable}
        if not same_otherwise or value['deterministic_repairs'][:len(old_repairs)] != old_repairs:
            raise GuardError('Frozen free-cooker configuration drift; use a separate dataset')
        change = {'at': now(), 'from_turn_index': len(p.ledger())}
        if value['deterministic_repairs'] != old_repairs:
            change['added_repairs'] = value['deterministic_repairs'][len(old_repairs):]
        if base.get('correction_temperature') != value['correction_temperature']:
            change['correction_temperature'] = {'from': base.get('correction_temperature', 0), 'to': value['correction_temperature']}
        current['amendments'] = current.get('amendments', []) + [change]
        current.update({k: value[k] for k in amendable})
        atomic(path, current)
    return current


def jobs_for(job_dir, turn_id):
    return sorted(job_dir.glob(turn_id + '-*'))


def request_digest(request):
    return sha_text(json.dumps(request, sort_keys=True, ensure_ascii=False))


def sent_digests(jobs, greedy_only=False):
    out = set()
    for job in jobs:
        request = read_json(job / 'request.json')
        response = read_json(job / 'response.json')
        if request and response and not response.get('error') and (not greedy_only or request.get('temperature', 0) == 0):
            out.add(request_digest(request))
    return out


def canonical(text):
    try:
        import claude_haiku_day1 as h
        return json.dumps(h.parse_patch(text), sort_keys=True, ensure_ascii=False)
    except (ValueError, TypeError):
        return (text or '').strip()


def previous_candidate_match(jobs, current_job, text):
    """Name an earlier job whose answer is identical to this one (after JSON canonicalization)."""
    mine = canonical(text)
    for job in jobs:
        if job == current_job:
            continue
        response = read_json(job / 'response.json')
        if response and not response.get('error') and canonical(candidate_text(response)[0]) == mine:
            return job.name
    return None


def attempts_for(job_dir, turn_id, version=PROMPT_CONTRACT_VERSION):
    """Only jobs under the current contract count toward this turn's bound; older jobs
    stay on disk and can still be reused if they now validate."""
    import openrouter_free as o
    jobs = sorted(job_dir.glob(turn_id + '-*'))
    current = [j for j in jobs if read_json(j / 'contract.json', {}).get('prompt_contract_version') == version]
    return jobs, sum(o.consumes_candidate_attempt(j) for j in current)


def last_answered_job(jobs):
    for job in reversed(jobs):
        response = read_json(job / 'response.json')
        if response and not response.get('error'):
            return job
    return None


def reuse_paid_candidate(p, jobs, turn, graph, spec, aliases=None):
    for job in reversed(jobs):
        response = read_json(job / 'response.json')
        if not response or response.get('error') or response.get('model') != spec['model']:
            continue
        text, finish = candidate_text(response)
        try:
            patch, repairs = validate(p, text, turn, graph, finish, aliases)
        except (ValueError, KeyError, TypeError, IndexError, p.jsonschema.ValidationError):
            continue
        return job, patch, repairs
    return None


def write_status(p, spec, state, **fields):
    if state != 'generating':
        p.publish()  # publish rewrites status.json, so it must run before an exit status
    rows = p.ledger()
    value = {'state': state, 'completed': len(rows), 'total': len(p.read('turns.json')), 'model': spec['model'],
             'provider': spec['provider'], 'updated_at': now(), 'usd_ceiling': 0, 'runner': 'free_cooker', **fields}
    atomic(p.DATA / 'free-runner-status.json', value)
    atomic(p.DATA / 'status.json', value)
    print(json.dumps({k: value[k] for k in ('state', 'completed', 'total')} | {'dataset': p.DATA.name, 'reason': fields.get('reason')}), flush=True)
    return value


def append_row(p, rows, row):
    with (p.DATA / '.writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if len(p.ledger()) != len(rows):
            raise GuardError('Ledger changed while request was in flight')
        with (p.DATA / 'predictions.jsonl').open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n'); stream.flush(); os.fsync(stream.fileno())
    rows.append(row)


# ---------------------------------------------------------------- one model

def cook(p, spec, key, args):
    prepare(p, spec, DATA_ROOT / args.source)
    import lean_context as L
    lean = spec.get('context') == 'lean'
    version = contract(spec)
    system = system_prompt(p, spec)
    config = freeze_config(p, spec, system)
    job_dir = p.DATA / 'openrouter-jobs'
    job_dir.mkdir(exist_ok=True)
    rows = p.ledger()
    turns = p.read('turns.json')
    if len(rows) > len(turns):
        raise GuardError('Ledger is longer than frozen source')
    graph = p.initial()
    for turn, row in zip(turns, rows):
        if row['turn_id'] != turn['id'] or row['model'] != spec['model']:
            raise GuardError('Unexpected ledger order or teacher')
        p.check(row['patch'], turn, graph)
        graph, _ = p.apply(graph, row['patch'], turn)
    last_request = 0.0
    requests = 0
    with (p.DATA / '.free-cooker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        p.publish(rows)
        write_status(p, spec, 'generating', reason='child started')
        for turn in turns[len(rows):]:
            context, aliases = p.context(turn, graph, rows), None
            if lean:
                context, aliases = L.build(turn, graph, context['project_description'])
            jobs, used = attempts_for(job_dir, turn['id'], version)
            reused = reuse_paid_candidate(p, jobs, turn, graph, spec, aliases)
            accepted = None
            if reused:
                job, patch, repairs = reused
                accepted = (job, patch, repairs, 'reused_paid_candidate')
            candidate = correction = None
            answered = last_answered_job(jobs) if not accepted else None
            if answered:  # resume from the newest real answer; provider-error jobs carry no candidate
                candidate, _ = candidate_text(read_json(answered / 'response.json'))
                err = read_json(answered / 'validation-error.json')
                correction = err.get('report') or err.get('error') if err else None
                if aliases is not None and correction:
                    correction = aliases.encode_text(correction)
            attempt = used
            while not accepted:
                if attempt >= 1 + MAX_CORRECTIONS:
                    write_status(p, spec, 'stalled_validation', reason='Turn exhausted ' + str(1 + MAX_CORRECTIONS) + ' bounded attempts: ' + turn['id'], blocked_turn=turn['id'])
                    return EXIT_STALLED
                if (p.DATA / 'STOP').exists() or (SUPERVISOR / 'STOP').exists():
                    write_status(p, spec, 'stopped', reason='STOP file')
                    return EXIT_STOP
                if args.max_requests and requests >= args.max_requests:
                    write_status(p, spec, 'request_limit', reason='Invocation request cap reached')
                    return EXIT_STOP
                write_status(p, spec, 'generating', current_turn=turn['id'], attempt=attempt + 1, requests_this_run=requests)
                check = preflight(key, spec)  # every request rechecks $0 route and shared quota
                atomic(p.DATA / 'free-preflight.json', check)
                request = make_request(spec, system, context, candidate, correction)
                if request['temperature'] == 0 and request_digest(request) in sent_digests(jobs_for(job_dir, turn['id']), greedy_only=True):
                    raise GuardError('Refusing a byte-identical temperature-0 resend for ' + turn['id'])
                job = job_dir / (turn['id'] + '-' + str(time.time_ns()))
                job.mkdir(parents=True)
                atomic(job / 'request.json', request)
                atomic(job / 'contract.json', {'prompt_contract_version': version, 'runner': 'free_cooker', 'attempt': attempt + 1})
                time.sleep(max(0, MIN_REQUEST_SPACING - (time.monotonic() - last_request)))
                last_request = time.monotonic()
                requests += 1
                try:
                    response = api('/chat/completions', key, request)
                except Cooldown as error:
                    atomic(job / 'provider-error.json', {'at': now(), 'error': str(error), 'error_kind': 'provider_unavailable', 'usd_ceiling': 0})
                    raise
                atomic(job / 'response.json', response)
                usage = audit_response(response, spec)  # Cooldown on upstream error envelopes; no attempt consumed
                attempt += 1
                text, finish = candidate_text(response)
                repeat_of = previous_candidate_match(jobs_for(job_dir, turn['id']), job, text)
                receipt = {'turn_id': turn['id'], 'attempt': attempt, 'generated_at': now(), 'generation_id': response.get('id'),
                           'temperature': request['temperature'], 'repeat_of_job': repeat_of,
                           'model': spec['model'], 'provider': response.get('provider'), 'usage': usage,
                           'request_sha256': sha(job / 'request.json'), 'job': str(job.relative_to(p.DATA)),
                           'prompt_contract_version': version, 'runner_sha256': sha(__file__)}
                with (p.DATA / 'free-usage.jsonl').open('a') as stream:
                    stream.write(json.dumps(receipt) + '\n'); stream.flush(); os.fsync(stream.fileno())
                try:
                    patch, repairs = validate(p, text, turn, graph, finish, aliases)
                except (ValueError, KeyError, TypeError, IndexError, p.jsonschema.ValidationError) as error:
                    candidate = text
                    correction = feedback(p, text, turn, graph, error, aliases)
                    atomic(job / 'validation-error.json', {'error': str(error)[:1600], 'report': correction})
                    print(json.dumps({'retry_validation': turn['id'], 'attempt': attempt, 'temperature': request['temperature'], 'repeat_of_job': repeat_of, 'error': str(error)[:200]}), flush=True)
                    continue
                accepted = (job, patch, repairs, 'new_request')
            job, patch, repairs, how = accepted
            row = {'turn_id': turn['id'], 'model': spec['model'], 'annotator_kind': 'LLM', 'label_status': 'machine_draft',
                   'generated_at': now(), 'input': context, 'patch': patch,
                   'inference_provenance': {'job': str(job.relative_to(p.DATA)), 'how': how, 'evidence_repairs': repairs,
                                            'prompt_contract_version': config['prompt_contract_version'],
                                            'provider': spec['provider'], 'runner_sha256': sha(__file__), 'usd': 0}}
            if lean:
                row['inference_provenance'].update(L.provenance())
            append_row(p, rows, row)
            graph, _ = p.apply(graph, patch, turn)
            p.publish(rows)
            write_status(p, spec, 'generating', requests_this_run=requests)
            if len(rows) % MILESTONE_EVERY == 0:
                notify('[free-cooker] MILESTONE ' + spec['dataset'] + ' reached ' + str(len(rows)) + ' accepted turns. Compute req/turn + days-to-finish and report to master.')
        p.publish(rows)
        write_status(p, spec, 'generated', requests_this_run=requests)
        return EXIT_DONE


def cook_main(args):
    spec = MODELS[args.model_key]
    os.umask(0o077)
    os.environ['CCT_V02_DATASET'] = spec['dataset']
    import pipeline as p
    if not args.run:
        prepare(p, spec, DATA_ROOT / args.source)
        print(json.dumps({'prepared': spec['dataset'], 'completed': len(p.ledger())}))
        return EXIT_DONE
    key = args.key_file.read_text().strip()
    try:
        return cook(p, spec, key, args)
    except Cooldown as error:
        write_status(p, spec, 'cooldown', reason=str(error)[:300], cooldown_until=error.until.isoformat())
        return EXIT_COOLDOWN
    except GuardError as error:
        write_status(p, spec, 'guard_stopped', reason=str(error)[:300])
        return EXIT_GUARD


# ---------------------------------------------------------------- lane supervisor

def supervisor_state(lane):
    return read_json(SUPERVISOR / ('lane-' + lane + '.json'), {'lane': lane, 'models': {}})


def pick_model(lane, state, at=None):
    """Strictly sequential: the first model in lane order that is not finished. A model
    cooling down, or stalled within its repair grace window, is waited for, never skipped."""
    at = at or utc()
    for key in LANES[lane]:
        info = state['models'].get(key, {})
        if info.get('state') in ('generated', 'stopped'):
            continue
        if info.get('state') == 'guard_stopped':
            return None, at + timedelta(hours=1)  # a safety guard needs the agent; never skip ahead
        if info.get('state') == 'stalled_validation':
            stalled = datetime.fromisoformat(info['stalled_at'])
            if at >= stalled + STALL_GRACE:
                continue
            recheck = datetime.fromisoformat(info.get('cooldown_until') or info['stalled_at'])
            return (key, None) if at >= recheck else (None, recheck)
        until = info.get('cooldown_until')
        if until and datetime.fromisoformat(until) > at:
            return None, datetime.fromisoformat(until)
        return key, None
    return None, None


def send_notification(target, text):
    """Detached supervisors must carry a sender even without CCT/tmux context."""
    try:
        subprocess.run(['cct', 'send', '--from', 'swarm-dashboard-free-cooker', '--', target, text],
                       timeout=120, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


# Standing rule (CKL via master, 2026-10-29): the cooker agent owns stalls and milestones;
# the manager hears only results, which the agent sends itself.
SELF = ('swarm-dashboard-free-cooker',)
MILESTONE_EVERY = 200


def notify(text, targets=SELF):
    """Best-effort ping; never blocks the lane. Defaults to the cooker agent only."""
    with (SUPERVISOR / 'notifications.jsonl').open('a') as stream:
        stream.write(json.dumps({'at': now(), 'text': text}) + '\n')
    for target in targets:
        send_notification(target, text)


def supervise_main(args):
    SUPERVISOR.mkdir(parents=True, exist_ok=True)
    lock = (SUPERVISOR / ('lane-' + args.lane + '.lock')).open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = supervisor_state(args.lane)
    state.update(pid=os.getpid(), started_at=now())
    while True:
        if (SUPERVISOR / 'STOP').exists():
            state.update(state='stopped', updated_at=now()); atomic(SUPERVISOR / ('lane-' + args.lane + '.json'), state)
            return 0
        key, wait = pick_model(args.lane, state)
        if key is None and wait is None:
            state.update(state='lane_finished', updated_at=now()); atomic(SUPERVISOR / ('lane-' + args.lane + '.json'), state)
            return 0
        if key is None:
            state.update(state='waiting', waiting_until=wait.isoformat(), updated_at=now()); atomic(SUPERVISOR / ('lane-' + args.lane + '.json'), state)
            while utc() < wait and not (SUPERVISOR / 'STOP').exists():
                time.sleep(min(30, max(1, (wait - utc()).total_seconds())))
            continue
        spec = MODELS[key]
        state.update(state='cooking', current=key, updated_at=now()); atomic(SUPERVISOR / ('lane-' + args.lane + '.json'), state)
        command = [sys.executable, str(Path(__file__).resolve()), 'cook', '--model-key', key, '--source', args.source, '--key-file', str(args.key_file), '--run']
        with (SUPERVISOR / (key + '.log')).open('a') as log:
            code = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
        status = read_json(DATA_ROOT / spec['dataset'] / 'free-runner-status.json', {})
        info = state['models'].setdefault(key, {})
        info.update(exit_code=code, completed=status.get('completed'), reason=status.get('reason'), updated_at=now())
        if code == EXIT_DONE:
            info['state'] = 'generated'
        elif code == EXIT_STALLED:
            first = info.get('state') != 'stalled_validation' or info.get('blocked_turn') != status.get('blocked_turn')
            if first:
                info.update(stalled_at=now(), blocked_turn=status.get('blocked_turn'))
                notify('[free-cooker] ' + key + ' STALLED at ' + str(status.get('blocked_turn')) + ' (' + str(status.get('completed')) +
                       ' accepted). Lane paused; rechecks saved candidates every 30 min (no requests) for 6h, then moves to the next model. Diagnose + add a recorded repair.')
            # Rechecking only revalidates saved candidates; an exhausted turn makes no new request.
            info.update(state='stalled_validation', cooldown_until=(utc() + STALL_RECHECK).isoformat())
        elif code == EXIT_COOLDOWN:
            # Back off longer while an outage persists; reset once the model makes progress.
            streak = info.get('cooldown_streak', 0) + 1 if info.get('last_completed') == status.get('completed') else 1
            base = datetime.fromisoformat(status.get('cooldown_until') or now())
            until = max(base, utc() + timedelta(minutes=min(60, 5 * 2 ** min(streak - 1, 4))))
            if 'quota' in (status.get('reason') or '').lower():
                until = base
            info.update(state='cooldown', cooldown_until=until.isoformat(), cooldown_streak=streak)
        elif code == EXIT_STOP:
            # A lane-wide STOP is a pause (restart resumes the same model); a per-dataset STOP parks it.
            info['state'] = 'paused' if (SUPERVISOR / 'STOP').exists() else 'stopped'
            if (SUPERVISOR / 'STOP').exists():
                state.update(state='stopped', updated_at=now()); atomic(SUPERVISOR / ('lane-' + args.lane + '.json'), state)
                return 0
        else:
            # A guard stop holds the lane until the agent clears it; crashes retry, then park.
            info['state'] = 'guard_stopped' if code == EXIT_GUARD else 'crashed'
            if code == EXIT_GUARD:
                notify('[free-cooker] ' + key + ' GUARD STOP (' + str(status.get('completed')) + ' accepted): ' + str(status.get('reason')) +
                       '. Lane held, zero requests. Fix, clear the state in lane-' + args.lane + '.json, relaunch.')
            if code != EXIT_GUARD:
                info['cooldown_until'] = (utc() + timedelta(minutes=30)).isoformat()
                info['crash_count'] = info.get('crash_count', 0) + 1
                if info['crash_count'] < 3:
                    info['state'] = 'cooldown'
        info['last_completed'] = status.get('completed')
        with (SUPERVISOR / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps({'at': now(), 'lane': args.lane, 'model': key, **info}) + '\n')
        atomic(SUPERVISOR / ('lane-' + args.lane + '.json'), state)


def progress(key):
    """Accepted turns plus request accounting for the free-cooker contract only."""
    spec = MODELS[key]
    d = DATA_ROOT / spec['dataset']
    rows = [json.loads(x) for x in (d / 'predictions.jsonl').read_text().splitlines()] if (d / 'predictions.jsonl').exists() else []
    ours = [r for r in rows if r.get('inference_provenance', {}).get('prompt_contract_version') == contract(spec)]
    jobs = [j for j in (d / 'openrouter-jobs').glob('*') if read_json(j / 'contract.json', {}).get('prompt_contract_version') == contract(spec)] if (d / 'openrouter-jobs').exists() else []
    answered = sum((j / 'response.json').exists() and not read_json(j / 'response.json', {}).get('error') for j in jobs)
    return {'key': key, 'model': spec['model'], 'dataset': spec['dataset'], 'accepted': len(rows), 'total': 1902,
            'status': read_json(d / 'status.json', {}).get('state'), 'accepted_this_contract': len(ours),
            'requests_this_contract': len(jobs), 'answered_requests': answered,
            'requests_per_accepted_turn': round(len(jobs) / len(ours), 2) if ours else None}


def plan_main(args):
    out = {}
    for lane, keys in LANES.items():
        out[lane] = []
        for key in keys:
            spec = MODELS[key]
            d = DATA_ROOT / spec['dataset']
            out[lane].append(progress(key))
    out['dropped'] = [progress(key) for key in DROPPED]
    print(json.dumps(out, indent=2))
    return 0


def main(argv=None):
    import openrouter_free as o
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('plan')
    for name in ('cook', 'supervise'):
        s = sub.add_parser(name)
        s.add_argument('--source', default='day1')
        s.add_argument('--key-file', type=Path, default=o.DEFAULT_KEY)
        s.add_argument('--run', action='store_true', help='send corpus context to the approved free routes')
    sub.choices['cook'].add_argument('--model-key', choices=sorted(MODELS), required=True)
    sub.choices['cook'].add_argument('--max-requests', type=int, default=0, help='0 = unbounded (free routes only)')
    sub.choices['supervise'].add_argument('--lane', choices=sorted(LANES), required=True)
    args = ap.parse_args(argv)
    if getattr(args, 'source', 'day1') != 'day1':
        ap.error('Only the frozen Day1 source is approved')
    if args.command == 'plan':
        return plan_main(args)
    if args.command == 'cook':
        return cook_main(args)
    if not args.run:
        ap.error('supervise requires --run')
    return supervise_main(args)


if __name__ == '__main__':
    sys.exit(main())
