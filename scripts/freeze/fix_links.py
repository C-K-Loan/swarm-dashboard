#!/usr/bin/env python3
"""Post-build pass over the static demo HTML: base-aware hrefs, no links to pages outside the demo.

Usage: fix_links.py SITE_DIR BASE PAGE[,PAGE...]
Nav entries for absent pages are removed; other links to them lose their href (scanners see no dead link).
JS-rendered links are handled at click time by the frozen script in Layout.astro.
"""
import re,sys
from pathlib import Path
site,base,pages=Path(sys.argv[1]),sys.argv[2],set(sys.argv[3].split(','))|{''}
KEEP={'data','frozen','_astro'}
def seg(path):return path.split('?')[0].split('#')[0].strip('/').split('/')[0]
def fix(m):
 attrs,href=m.group(1) or ' ',m.group(2)
 p=href[len(base)-1:] if href.startswith(base) else href
 s=seg(p)
 if s in KEEP or s in pages:return f'<a{attrs}href="{base}{p.lstrip("/")}"'
 return f'<a{attrs}aria-disabled="true" title="Not part of the static demo"'
for f in site.rglob('*.html'):
 h=f.read_text()
 # Nav entries for absent pages: drop the whole anchor.
 h=re.sub(r'<a href="/[^"]*" class="nav-link" data-nav="([^"]+)">[^<]*</a>',lambda m:m.group(0) if m.group(1) in pages else '',h)
 h=re.sub(r'<a(\s[^>]*?)?href="(/[^"]*)"',fix,h)
 f.write_text(h)
