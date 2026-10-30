#!/usr/bin/env python3
"""Counts-only heuristic scan; never prints matches or changes source data."""
import argparse
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

PATTERNS = {
    'credential_like': re.compile(r'\b(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9_.-]{20,}'),
    'credential_assignment_like': re.compile(r'(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\s*[=:]\s*["\']?[A-Za-z0-9_./+-]{12,}'),
    'email_like': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    # Explicit leading + avoids most source timestamps, IDs and port numbers.
    'international_phone_like': re.compile(r'(?<!\w)\+[1-9]\d{0,2}[ (.-]*(?:\d[ ).-]*){7,12}\b'),
}
URL = re.compile(r'https?://[^\s<>"\']+')


def private_url(url):
    host = (urlsplit(url).hostname or '').lower()
    if host in ('localhost',) or host.endswith(('.localhost', '.local', '.internal', '.ts.net')):
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_private or address.is_loopback or address in ipaddress.ip_network('100.64.0.0/10')
    except ValueError:
        return False


def scan(path):
    turns = json.loads(path.read_text())
    counts = {name: {'matches': 0, 'turns': 0} for name in [*PATTERNS, 'private_url_like']}
    flagged = 0
    for turn in turns:
        text = '\n'.join(m['text'] for m in turn['messages'])
        found = {name: len(pattern.findall(text)) for name, pattern in PATTERNS.items()}
        found['private_url_like'] = sum(private_url(url) for url in URL.findall(text))
        flagged += any(found.values())
        for name, count in found.items():
            counts[name]['matches'] += count
            counts[name]['turns'] += count > 0
    return {'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'turns_scanned': len(turns),
            'turns_with_any_match': flagged, 'categories': counts,
            'limitations': 'Heuristics only; false positives and missed sensitive data are possible. No matched values, turn IDs or source text emitted. No source was modified.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('turns', type=Path)
    args = parser.parse_args()
    print(json.dumps(scan(args.turns), indent=2))
