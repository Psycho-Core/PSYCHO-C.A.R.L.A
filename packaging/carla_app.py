"""Entry point for the PyInstaller build.

``desktop.py`` uses package-relative imports, so it cannot be the frozen
entry script itself; this tiny launcher imports it as part of the package.
"""

import multiprocessing

from psycho_carla.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
