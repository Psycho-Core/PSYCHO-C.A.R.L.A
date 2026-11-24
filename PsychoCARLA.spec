# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — PSYCHO-C.A.R.L.A. (Windows 11).

Build (from the repo root):

    pyinstaller PsychoCARLA.spec

Output:  dist/PsychoCARLA/PsychoCARLA.exe

Notes
-----
* ``onedir``, not ``onefile``: the voice stack (torch, faster-whisper,
  onnxruntime) is far too large and slow to unpack on every launch.
* The core application never imports torch — only ``voice/stt.py`` does.
  Set ``CARLA_BUILD_VOICE=1`` to include the voice stack; otherwise it is
  excluded and the bundle stays small.
* ``CARLA_BUILD_NATIVE=1`` includes the in-process CPU engine
  (onnxruntime + transformers). This is what lets the app run a model
  with no Ollama and no external server. It is on by default because
  it is central to the product — the voice stack is what stays opt-in,
  since it drags in torch.
* Models are NEVER bundled. They live in ``%USERPROFILE%\\.psycho_carla\\models``
  and are downloaded at runtime.
"""

import os

# ---------------------------------------------------------------------------
# Voice stack is opt-in: it drags in torch + CTranslate2 + onnxruntime.
# ---------------------------------------------------------------------------
WITH_VOICE = os.environ.get("CARLA_BUILD_VOICE", "0") not in ("0", "", "false")

# ---------------------------------------------------------------------------
# The in-process CPU engine. On by default — it is the zero-setup path,
# and without it the app can only talk to an engine the user installs
# separately.
# ---------------------------------------------------------------------------
WITH_NATIVE = os.environ.get("CARLA_BUILD_NATIVE", "1") not in ("0", "", "false")

block_cipher = None

# ---------------------------------------------------------------------------
# Excluded modules — keeps the bundle lean and avoids PyInstaller pulling in
# the entire scientific stack that happens to be present in the build env.
# ---------------------------------------------------------------------------
EXCLUDES = [
    # Test / dev tooling
    "pytest", "_pytest", "nose", "IPython", "IPython.core", "jupyter",
    "jupyter_client", "notebook", "nbconvert", "nbformat", "ipykernel",
    "sphinx", "mkdocs",

    # Scientific stack — only needed by the optional voice extras, and
    # PyInstaller's hooks for these are enormous.
    "matplotlib", "matplotlib.pyplot", "pandas", "scipy", "numpy.f2py",
    "IPython.display", "sympy", "networkx",

    # Qt / GUI toolkits we do not use (we use pywebview)
    "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter",

    # Servers we do not ship
    "tornado", "aiohttp",

    # Unused web frameworks
    "django", "flask", "bottle", "cherrypy",

    # Cloud SDKs that would bloat the bundle if a transitive dep pulls them
    "google.cloud", "azure", "boto3", "botocore",

    # Build-time only
    "setuptools._distutils", "pkg_resources",
]

if not WITH_VOICE:
    EXCLUDES += [
        "torch", "torchvision", "torchaudio",
        "faster_whisper", "ctranslate2", "kokoro", "misaki", "phonemizer",
        "openwakeword", "soundfile", "librosa",
    ]

if not WITH_NATIVE:
    EXCLUDES += [
        "transformers", "onnxruntime", "onnxruntime_gpu",
    ]

# ---------------------------------------------------------------------------
# Data files that must ship inside the bundle.
# ---------------------------------------------------------------------------
datas = [
    ("src/psycho_carla/server/web", "psycho_carla/server/web"),
    ("src/psycho_carla/templates", "psycho_carla/templates"),
    ("src/psycho_carla/recipes", "psycho_carla/recipes"),
    ("src/psycho_carla/examples", "psycho_carla/examples"),
]

# pywebview ships its own backends; on Windows we want EdgeChromium (WebView2).
hiddenimports = [
    "psycho_carla",
    "psycho_carla.server.app",
    "psycho_carla.server.api.hud",
    "psycho_carla.server.api.openai_compat",
    "psycho_carla.server.api.channels",
    "psycho_carla.providers.adapters",
    "psycho_carla.providers.adapters.native_onnx",
    "psycho_carla.channels",
    "psycho_carla.channels.slack_daemon",
    "psycho_carla.agents.builtin",
    "psycho_carla.cli.main",
    "psycho_carla.desktop",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "websockets",
    "webview.platforms.edgechromium",
]

if WITH_NATIVE:
    hiddenimports += [
        "onnxruntime",
        "onnxruntime.capi._pybind_state",
        "transformers",
        "huggingface_hub",
    ]

a = Analysis(
    ["src/psycho_carla/desktop.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PsychoCARLA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app — no console window
    disable_windowed_traceback=False,
    icon=None,              # supply an .ico for the Windows shell
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PsychoCARLA",
)
