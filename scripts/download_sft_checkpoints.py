#!/usr/bin/env python3
"""Download all LoRA checkpoint result_files from the 3 SFT jobs.

Reads data/sft/sft_final.json and fetches each result_file via
GET /v1/files/<id>/content into data/sft/checkpoints/<label>/<filename>.

Nebius sends files with Content-Disposition that includes the original filename.
"""
import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(".")
STATE = ROOT / "data/sft/sft_final.json"
OUT_ROOT = ROOT / "data/sft/checkpoints"
BASE = "https://api.tokenfactory.nebius.com/v1"


def load_env():
    env_file = Path(".env")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)


def fetch_meta(file_id: str, key: str) -> dict:
    r = requests.get(f"{BASE}/files/{file_id}", headers={"Authorization": f"Bearer {key}"}, timeout=60)
    r.raise_for_status()
    return r.json()


class _StripAuthRedirectSession(requests.Session):
    """Strip Authorization header when redirected to a different host (S3)."""
    def rebuild_auth(self, prepared_request, response):
        headers = prepared_request.headers
        if "Authorization" in headers:
            orig_host = requests.utils.urlparse(response.request.url).hostname
            new_host = requests.utils.urlparse(prepared_request.url).hostname
            if orig_host != new_host:
                del headers["Authorization"]


def fetch_content(file_id: str, dest: Path, key: str) -> int:
    sess = _StripAuthRedirectSession()
    r = sess.get(
        f"{BASE}/files/{file_id}/content",
        headers={"Authorization": f"Bearer {key}"},
        stream=True, allow_redirects=True, timeout=600,
    )
    r.raise_for_status()
    n = 0
    with dest.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            if chunk:
                f.write(chunk)
                n += len(chunk)
    return n


def main():
    load_env()
    key = os.environ["NEBIUS_TOKEN_FACTORY_KEY"]
    state = json.loads(STATE.read_text())
    total_bytes = 0
    for j in state["jobs"]:
        label = j["label"]
        out_dir = OUT_ROOT / label
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {label} ({len(j['result_files'])} files) ===", file=sys.stderr)
        for i, fid in enumerate(j["result_files"], 1):
            try:
                meta = fetch_meta(fid, key)
                fname = meta.get("filename") or meta.get("name") or fid
                fname = re.sub(r"[^A-Za-z0-9._-]", "_", fname)
                dest = out_dir / fname
                if dest.exists() and dest.stat().st_size > 0:
                    print(f"  [{i:2d}/18] skip {fname} ({dest.stat().st_size:,} B)", file=sys.stderr)
                    total_bytes += dest.stat().st_size
                    continue
                n = fetch_content(fid, dest, key)
                total_bytes += n
                print(f"  [{i:2d}/18] {fname}  ({n:,} B)", file=sys.stderr)
            except Exception as e:
                print(f"  [{i:2d}/18] FAIL {fid}: {e}", file=sys.stderr)
    print(f"\n[done] total downloaded: {total_bytes / 1e9:.2f} GB", file=sys.stderr)


if __name__ == "__main__":
    main()
