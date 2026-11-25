#!/usr/bin/env python3
"""Check that every GGUF catalog entry's repo + file really exists on Hugging Face.

Sends one HEAD request per model (no downloads) and prints the size the server
reports. Exit code 1 if any entry is missing. Run it where Hugging Face is reachable.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

from psycho_carla.intelligence.model_catalog import GGUF_MODELS


def check(repo: str, filename: str) -> tuple[bool, str]:
    url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "psycho-carla"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            size = int(resp.headers.get("Content-Length") or resp.headers.get("X-Linked-Size") or 0)
            return True, f"{size / 1e9:.2f} GB"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # network
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    bad = 0
    for m in GGUF_MODELS:
        ok, info = check(m.hf_repo, m.gguf_file)
        bad += 0 if ok else 1
        print(f"{'OK  ' if ok else 'FAIL'} {m.model_id:<38} {m.hf_repo}/{m.gguf_file}  {info}  (catalog says {m.size_gb} GB)")
    print(f"\n{len(GGUF_MODELS) - bad}/{len(GGUF_MODELS)} catalog entries found")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
