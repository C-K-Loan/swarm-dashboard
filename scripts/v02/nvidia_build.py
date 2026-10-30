#!/usr/bin/env python3
"""NVIDIA build.nvidia.com route for Nemotron 3 Ultra (CKL option B, 2026-10-30, $0 developer key).

Drop-in for openrouter_free's request surface (MODEL, PROVIDER, ZERO_PRICES, DEFAULT_KEY, api, audit_response,
preflight, GuardError, RateLimited, TransientProviderError), so an unchanged consumer runs on it through
nvidia_route.py. OpenRouter-only body fields are translated: provider is dropped, reasoning off becomes
chat_template_kwargs.enable_thinking=false. Stops on 429 and on any billing/credit error; never retries or
falls back. The key file is read in-process and never printed.
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

API = 'https://integrate.api.nvidia.com/v1'
MODEL = 'nvidia/nemotron-3-ultra-550b-a55b'
PROVIDER = 'nvidia'
DEFAULT_KEY = Path.home() / '.config/nvidia/build.env'
ZERO_PRICES = {'prompt': 0, 'completion': 0, 'request': 0, 'image': 0}  # kept for consumers; NVIDIA sends no prices
# Non-streamed long completions hang at the NVIDIA gateway (2026-10-30: 10+ min, no bytes); streamed ones
# answer in ~1 s first token, so chat completions are streamed and reassembled under a hard deadline.
READ_TIMEOUT = 60
DEADLINE = 300


class GuardError(RuntimeError):
    pass


class RateLimited(GuardError):
    pass


class TransientProviderError(GuardError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def key_value(raw):
    """The key from build.env text ('NVIDIA_API_KEY=...', optional export/quotes) or a bare key."""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].strip()
        if line.startswith('NVIDIA_API_KEY='):
            return line.split('=', 1)[1].strip().strip('"\'')
    return raw.strip()


def zero(value):
    try:
        return not isinstance(value, bool) and Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def translate(payload):
    body = {k: v for k, v in payload.items() if k not in ('provider', 'reasoning', 'usage')}
    if body.get('model') != MODEL:
        raise GuardError('Request model differs from pinned NVIDIA model')
    if payload.get('reasoning', {}).get('enabled') is False:
        body['chat_template_kwargs'] = {**body.get('chat_template_kwargs', {}), 'enable_thinking': False}
    return body


def audit_response(response):
    if response.get('error'):
        err = response['error'] if isinstance(response['error'], dict) else {'message': str(response['error'])}
        detail = str(err.get('code') or err.get('type') or '') + ' ' + str(err.get('message') or '')[:200]
        if '429' in detail or 'rate' in detail.lower() or 'too many' in detail.lower():
            raise RateLimited('NVIDIA stream error (rate): ' + detail)
        if str(err.get('code')) in ('502', '503', '504'):
            raise TransientProviderError('NVIDIA transient stream error: ' + detail)
        raise GuardError('NVIDIA returned an error: ' + detail + '; no retry or fallback')
    if response.get('model') != MODEL:
        raise GuardError('Response model differs from pinned NVIDIA model')
    usage = response.get('usage') or {}
    def check_costs(obj):
        for key, value in obj.items():
            if isinstance(value, dict):
                check_costs(value)
            elif ('cost' in key.lower() or 'credit' in key.lower()) and not zero(value):
                raise GuardError('Response reports a nonzero cost/credit field; refusing to continue')
    check_costs(usage)
    return usage


def api(path, key=None, payload=None):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key_value(key)
    chat = payload is not None and path == '/chat/completions'
    if chat:
        payload = {**translate(payload), 'stream': True, 'stream_options': {'include_usage': True}}
        headers['Accept'] = 'text/event-stream'
    request = urllib.request.Request(API + path, headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT if chat else 120) as response:
            data = assemble(response) if chat else json.load(response)
    except (TimeoutError, OSError) as error:
        if isinstance(error, urllib.error.HTTPError):
            raise_http(error)
        raise TransientProviderError('NVIDIA transient: stream stalled or timed out (' + type(error).__name__ + ')') from None
    if chat:
        data.setdefault('provider', PROVIDER)
    return data


def raise_http(error):
    # Never interpolate request headers, credentials, or private prompts.
    if error.code == 429:
        raise RateLimited('NVIDIA HTTP 429; stop. Retry-After=' + str(error.headers.get('Retry-After', 'unspecified'))) from None
    if error.code in (402, 403):
        raise GuardError('NVIDIA HTTP ' + str(error.code) + ' (credits/limit or access); stop') from None
    if error.code in (502, 503, 504):
        raise TransientProviderError('NVIDIA transient HTTP ' + str(error.code) + '; no fallback') from None
    raise GuardError('NVIDIA HTTP ' + str(error.code) + '; no automatic retry or fallback') from None


def assemble(lines):
    """One chat.completion dict from an SSE stream; TransientProviderError past DEADLINE."""
    start, parts, out = time.time(), [], {'choices': [{'index': 0, 'message': {'role': 'assistant'}, 'finish_reason': None}]}
    for raw in lines:
        if time.time() - start > DEADLINE:
            raise TransientProviderError('NVIDIA transient: stream exceeded ' + str(DEADLINE) + ' s')
        line = raw.decode('utf-8', 'replace').strip()
        if not line.startswith('data:'):
            continue
        chunk = line[5:].strip()
        if chunk == '[DONE]':
            break
        event = json.loads(chunk)
        if event.get('error'):
            out['error'] = event['error']
            break
        for key in ('id', 'model', 'created', 'system_fingerprint'):
            if event.get(key) is not None:
                out[key] = event[key]
        if event.get('usage'):
            out['usage'] = event['usage']
        for choice in event.get('choices') or []:
            parts.append((choice.get('delta') or {}).get('content') or '')
            if choice.get('finish_reason'):
                out['choices'][0]['finish_reason'] = choice['finish_reason']
    out['choices'][0]['message']['content'] = ''.join(parts)
    out['object'] = 'chat.completion'
    return out


def preflight(key, reasoning=False):
    if not key_value(key):
        raise GuardError('NVIDIA key file is empty')
    if not any(m.get('id') == MODEL for m in api('/models').get('data', [])):
        raise GuardError('Pinned NVIDIA model is unavailable')
    return {'checked_at': now(), 'model': MODEL, 'provider': PROVIDER, 'api': API,
            'billing': 'build.nvidia.com developer key; no prices or quota exposed by the API'}
