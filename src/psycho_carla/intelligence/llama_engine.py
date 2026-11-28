"""C.A.R.L.A.'s own llama.cpp engine — no Ollama, no separate app to start.

llama.cpp (``llama-server``) is what runs GGUF models, from a 3B model on
a small PC up to a 32B model on a big GPU. C.A.R.L.A. downloads the
official release build itself, launches it as a hidden child process on
``127.0.0.1`` when you press Start (or send the first message), and stops
it when you press Stop / Free memory or quit. You never run it by hand.

Backends (Windows): ``vulkan`` (AMD / NVIDIA / Intel GPUs), ``cpu``, ``hip``
(AMD, Radeon cards supported by ROCm). Pick one with
``carla engine install --backend vulkan``.

Nothing here was run against a real llama.cpp binary in the development
sandbox (GitHub is unreachable there); the asset-selection, extraction,
argument-building and process-handling logic is unit-tested with fixtures.
"""

from __future__ import annotations

import atexit
import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("psycho_carla.llama_engine")

RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30"
BACKENDS = ("vulkan", "cpu", "hip")

ProgressFn = Callable[[str, float | None], None]


# --------------------------------------------------------------------------- #
# Where things live
# --------------------------------------------------------------------------- #
def engines_dir() -> Path:
    from ..core.config import load_config

    d = Path(load_config().home) / "engines" / "llama.cpp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exe_name() -> str:
    return "llama-server.exe" if platform.system() == "Windows" else "llama-server"


def find_binary(explicit: str = "") -> Path | None:
    """Locate ``llama-server``: explicit path, env var, installed engine, PATH."""
    candidates: list[Path] = []
    for raw in (explicit, os.environ.get("PSYCHO_CARLA_LLAMA_SERVER", "")):
        if raw:
            candidates.append(Path(raw).expanduser())
    for path in candidates:
        if path.is_file():
            return path
    try:
        root = engines_dir()
        found = sorted(root.rglob(_exe_name()), key=lambda p: p.stat().st_mtime, reverse=True)
        if found:
            return found[0]
    except Exception:
        pass
    on_path = shutil.which("llama-server")
    return Path(on_path) if on_path else None


def installed_backend(binary: Path | None) -> str:
    """Guess the backend from the install folder name (we name it)."""
    if binary is None:
        return ""
    low = str(binary).lower()
    for b in BACKENDS:
        if f"-{b}-" in low or f"/{b}/" in low or f"\\{b}\\" in low or f"_{b}" in low:
            return b
    return "unknown"


# --------------------------------------------------------------------------- #
# Installing the engine
# --------------------------------------------------------------------------- #
def pick_asset(
    assets: Sequence[dict[str, Any]], backend: str = "vulkan", system: str = ""
) -> dict[str, Any] | None:
    """Choose the release asset for this OS and backend, or ``None``.

    Matches by name keywords (llama.cpp names builds like
    ``llama-bNNNN-bin-win-vulkan-x64.zip``). CUDA runtime bundles are
    never picked.
    """
    system = (system or platform.system()).lower()
    backend = backend.lower()
    want_os = {"windows": "win", "linux": "ubuntu", "darwin": "macos"}.get(system, system)
    ext = ".zip" if system in ("windows", "darwin") else ".tar.gz"
    best: dict[str, Any] | None = None
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        if not name.endswith((".zip", ".tar.gz")) or "cudart" in name:
            continue
        if want_os not in name or backend not in name:
            continue
        if "x64" not in name and "x86_64" not in name:
            continue
        if backend != "cuda" and "cuda" in name:
            continue
        if best is None or name.endswith(ext):
            best = asset
            if name.endswith(ext):
                break
    return best


def find_release_asset(
    releases: Sequence[dict[str, Any]], backend: str, system: str = ""
) -> tuple[dict[str, Any], dict[str, Any]] | tuple[None, None]:
    """The newest non-draft release that has a build for this OS/backend.

    GitHub's ``/releases/latest`` can point at a marker release with no
    binaries (llama.cpp's currently holds only ``nightly-tag.txt``), so the
    list is scanned newest-first instead.
    """
    for rel in releases:
        if rel.get("draft"):
            continue
        asset = pick_asset(rel.get("assets", []) or [], backend, system)
        if asset is not None:
            return rel, asset
    return None, None


def describe_releases(releases: Any, limit: int = 3) -> str:
    """A short summary of what was seen, for error messages."""
    if not isinstance(releases, list):
        return "unexpected response from GitHub: " + str(releases)[:120]
    parts = []
    for rel in releases[:limit]:
        names = [str(a.get("name", "")) for a in (rel.get("assets") or [])][:4]
        parts.append(f"{rel.get('tag_name', '?')}: {names}")
    return "; ".join(parts) or "none"


def _safe_extract(archive: Path, dest: Path) -> None:
    """Extract a zip, refusing any member that escapes ``dest`` (zip-slip)."""
    dest = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if dest != target and dest not in target.parents:
                raise ValueError(f"unsafe path in archive: {member}")
        zf.extractall(dest)


def install_engine(backend: str = "vulkan", progress: ProgressFn | None = None) -> dict[str, Any]:
    """Download and unpack the official llama.cpp release for ``backend``."""
    progress = progress or (lambda m, p=None: None)
    backend = backend.lower()
    if backend not in BACKENDS:
        return {"ok": False, "message": f"backend must be one of {', '.join(BACKENDS)}"}
    try:
        import httpx

        progress("looking up the latest llama.cpp release", None)
        releases = httpx.get(RELEASES_API, timeout=20.0, follow_redirects=True,
                             headers={"Accept": "application/vnd.github+json",
                                      "User-Agent": "psycho-carla"}).json()
        rel, asset = find_release_asset(releases if isinstance(releases, list) else [], backend)
        if asset is None:
            return {"ok": False, "message": (
                f"no '{backend}' build for this OS found in the {len(releases) if isinstance(releases, list) else 0} "
                f"newest llama.cpp releases ({describe_releases(releases)}). "
                "Download llama-server yourself and set PSYCHO_CARLA_LLAMA_SERVER.")}
        tag = str(rel.get("tag_name", "latest"))
        folder = engines_dir() / f"{tag}-{backend}-"
        folder.mkdir(parents=True, exist_ok=True)
        archive = folder / str(asset["name"])
        progress(f"downloading {asset['name']}", None)
        with httpx.stream("GET", asset["browser_download_url"], follow_redirects=True,
                          timeout=60.0) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0) or 0)
            done = 0
            with archive.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        progress("downloading engine", done * 100.0 / total)
        progress("unpacking", None)
        if archive.suffix == ".zip":
            _safe_extract(archive, folder)
        else:
            import tarfile

            with tarfile.open(archive) as tf:
                tf.extractall(folder, filter="data")
        archive.unlink(missing_ok=True)
        binary = find_binary()
        if binary is None:
            return {"ok": False, "message": "unpacked, but llama-server was not found inside"}
        return {"ok": True, "binary": str(binary), "backend": backend, "release": tag,
                "message": f"installed llama.cpp {tag} ({backend})"}
    except Exception as exc:
        return {"ok": False, "message": f"engine install failed: {exc}"}


# --------------------------------------------------------------------------- #
# Choosing how much to put on the GPU
# --------------------------------------------------------------------------- #
def auto_gpu_layers(
    model_gb: float, vram_gb: float = 0.0, n_layers: int = 64, backend: str = "vulkan"
) -> int:
    """How many layers to offload. 0 = CPU only; 999 = everything.

    VRAM unknown (common on AMD) → assume 12 GB usable, a safe figure for a
    16 GB card once the OS and context are accounted for. Leaves ~15%
    headroom for the KV cache.
    """
    if backend == "cpu":
        return 0
    usable = (vram_gb or 12.0) * 0.85
    if model_gb <= usable:
        return 999
    return max(int(n_layers * usable / max(model_gb, 0.1)), 0)


def build_args(
    binary: Path, model: Path, port: int, *, ctx: int = 4096, threads: int = 0,
    gpu_layers: int = 0,
) -> list[str]:
    args = [str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-c", str(ctx), "-ngl", str(gpu_layers)]
    if threads > 0:
        args += ["-t", str(threads)]
    return args


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# --------------------------------------------------------------------------- #
# The managed server
# --------------------------------------------------------------------------- #
class LlamaServer:
    """One llama-server child process, started and stopped by C.A.R.L.A."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self.model_path = ""
        self.port = 0
        self.started_at = 0.0
        self.gpu_layers = 0

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1" if self.port else ""

    def start(
        self, model_path: str, *, ctx: int = 4096, threads: int = 0,
        device: str = "auto", gpu_layers: int | str = "auto", binary: str = "",
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        model = Path(model_path)
        if not model.is_file():
            return {"ok": False, "message": f"model file not found: {model}"}
        exe = find_binary(binary)
        if exe is None:
            return {"ok": False, "message": (
                "the llama.cpp engine is not installed — click 'Install engine' in "
                "the Model Manager (or: carla engine install)")}
        with self._lock:
            if self.running and self.model_path == str(model):
                return {"ok": True, "already_running": True, "base_url": self.base_url,
                        "message": "already running"}
            self._stop_locked()
            backend = installed_backend(exe)
            if device == "cpu" or backend == "cpu":
                ngl = 0
            elif isinstance(gpu_layers, int) and gpu_layers >= 0:
                ngl = gpu_layers
            else:
                size_gb = model.stat().st_size / 1e9
                ngl = auto_gpu_layers(size_gb, _vram_gb(), backend=backend or "vulkan")
            port = _free_port()
            cmd = build_args(exe, model, port, ctx=ctx, threads=threads, gpu_layers=ngl)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags,
                )
            except OSError as exc:
                return {"ok": False, "message": f"could not start llama-server: {exc}"}
            self.port, self.model_path, self.gpu_layers = port, str(model), ngl
            ok, detail = self._wait_ready(timeout)
            if not ok:
                self._stop_locked()
                return {"ok": False, "message": detail}
            self.started_at = time.time()
            return {"ok": True, "base_url": self.base_url, "gpu_layers": ngl,
                    "backend": backend, "message": f"{model.name} is running"}

    def _wait_ready(self, timeout: float) -> tuple[bool, str]:
        import httpx

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                err = ""
                try:
                    err = (self._proc.stderr.read() or b"").decode("utf-8", "replace")[-400:]
                except Exception:
                    pass
                return False, f"llama-server exited while loading the model. {err}".strip()
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2.0).status_code == 200:
                    return True, "ready"
            except Exception:
                pass
            time.sleep(0.5)
        return False, "llama-server did not become ready in time (model too large for memory?)"

    def stop(self) -> bool:
        with self._lock:
            was = self.running
            self._stop_locked()
            return was

    def _stop_locked(self) -> None:
        proc, self._proc = self._proc, None
        self.model_path, self.port, self.started_at = "", 0, 0.0
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            logger.exception("could not stop llama-server")

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "model_path": self.model_path,
                "base_url": self.base_url, "gpu_layers": self.gpu_layers}


def _vram_gb() -> float:
    try:
        from ..core.energy import hardware_summary

        return float(getattr(hardware_summary(), "vram_gb", 0.0) or 0.0)
    except Exception:
        return 0.0


server = LlamaServer()
atexit.register(server.stop)


def engine_status() -> dict[str, Any]:
    from . import gguf_inprocess

    exe = find_binary()
    ok, detail = gguf_inprocess.available()
    return {"inprocess": {"available": ok, "detail": detail,
                          "gpu_offload": gguf_inprocess.gpu_offload_supported() if ok else False,
                          **gguf_inprocess.engine.status()},
            "installed": exe is not None, "binary": str(exe) if exe else "",
            "backend": installed_backend(exe), "backends": list(BACKENDS),
            "server": server.status()}
