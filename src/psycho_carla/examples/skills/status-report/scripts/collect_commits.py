#!/usr/bin/env python3
"""Collect recent commit subjects for a status report.

Usage:
    python3 collect_commits.py [count] [repo_path]
"""

import subprocess
import sys


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    repo = sys.argv[2] if len(sys.argv) > 2 else "."
    try:
        out = subprocess.run(
            ["git", "-C", repo, "log", f"-{count}", "--pretty=format:%h %s"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if out.returncode != 0:
        print(f"error: {(out.stderr or '').strip()}", file=sys.stderr)
        return 1
    print(out.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
