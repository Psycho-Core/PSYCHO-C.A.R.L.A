"""
Entry point for ``python -m psycho_carla``.

Running the package as a module is a process entrypoint, so this is the
right place to restart once with espeak-ng on the dynamic loader path
(needed on Linux when espeak-ng is installed in a user-local prefix
rather than the system library path).
"""

from __future__ import annotations

import sys

from .voice.tts import maybe_reexec_with_espeak


def main() -> int:
    maybe_reexec_with_espeak()
    from .cli.main import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
