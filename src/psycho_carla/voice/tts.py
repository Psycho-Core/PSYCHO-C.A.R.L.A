"""
PSYCHO-C.A.R.L.A. — Text-to-Speech (CPU-only).

Two CPU-only backends:

  * **Kokoro** — `kokoro` pip package, 82M params, runs on CPU in real time.
    Voice ids are like `af_alloy`, `af_heart`, `am_michael`. We map the
    configured alias (e.g. `irish_female`) onto a concrete Kokoro voice id.
  * **Piper** — `piper-tts` (ONNX) — fast, CPU-friendly, voice models are
    standalone `.onnx` files.

Both are optional. If neither is installed the system degrades gracefully to
"silent" mode: text still flows to the UI, nothing is spoken.

Layer: Pillar 3 / Persona.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def ensure_espeak_library() -> bool:
    """Make sure the espeak-ng shared library is discoverable.

    Kokoro's G2P falls back to espeak-ng for out-of-dictionary words, which
    is how "C.A.R.L.A." gets pronounced correctly. The `phonemizer`
    package only auto-detects the library on macOS and Windows — on Linux
    it must be pointed at explicitly, otherwise Kokoro silently drops
    unknown words.

    Sets `PHONEMIZER_ESPEAK_LIBRARY`, the espeak data path, and calls
    `EspeakWrapper.set_library()` so the override applies before any
    backend is constructed.

    Returns True if the library is available afterwards.
    """
    try:
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
    except Exception:
        return False

    if getattr(EspeakWrapper, "_ESPEAK_LIBRARY", None):
        return True

    local = Path.home() / ".local" / "espeak"
    data_dir = local / "espeak-ng-data"

    candidates: list[str] = []
    env_lib = os.environ.get("ESPEAK_LIBRARY") or os.environ.get(
        "PHONEMIZER_ESPEAK_LIBRARY"
    )
    if env_lib:
        candidates.append(env_lib)
    for name in ("libespeak-ng.so.1", "libespeak-ng.so", "libespeak.so.1"):
        candidates.append(str(local / name))
        candidates.append(name)
    # Windows default install location.
    candidates.append(r"C:\Program Files\eSpeak NG\libespeak-ng.dll")

    for cand in candidates:
        if not cand:
            continue
        try:
            import ctypes

            if os.path.sep in cand and not Path(cand).exists():
                continue
            ctypes.CDLL(cand)
        except Exception:
            continue
        try:
            EspeakWrapper.set_library(cand)
        except Exception:
            continue
        # The env var is what phonemizer's own lookup consults, and it must
        # be set before any EspeakBackend is instantiated.
        os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = cand
        if data_dir.is_dir():
            os.environ["ESPEAK_DATA_PATH"] = str(data_dir)
            try:
                EspeakWrapper.set_data_path(str(data_dir))
            except Exception:
                pass
        # Verify it actually loads — a stale or ABI-mismatched .so will
        # raise here rather than later inside synthesis.
        try:
            import phonemizer

            phonemizer.backend.EspeakBackend(
                language="en-gb", preserve_punctuation=True, with_stress=True
            )
            return True
        except Exception:
            os.environ.pop("PHONEMIZER_ESPEAK_LIBRARY", None)
            continue
    return False


def espeak_library_paths() -> tuple[str, str, str]:
    """Return (library, data_dir, lib_dir) for a local espeak-ng install.

    Looks in the standard user-local location first, then the Windows
    default install path. Returns empty strings when nothing is found.
    """
    if os.name == "nt":
        win_lib = Path(r"C:\Program Files\eSpeak NG\libespeak-ng.dll")
        if win_lib.exists():
            return str(win_lib), "", str(win_lib.parent)
        return "", "", ""

    local = Path.home() / ".local" / "espeak"
    lib = local / "libespeak-ng.so.1"
    data = local / "espeak-ng-data"
    if lib.exists() and data.is_dir():
        return str(lib), str(data), str(local)
    return "", "", ""


def configure_espeak_environment() -> None:
    """Point the process at a locally-installed espeak-ng, if present.

    Safe to call at any time: it only sets environment variables and
    preloads the shared objects it can. It never re-execs the process —
    doing that mid-run would restart the test suite or the server.
    """
    lib, data, lib_dir = espeak_library_paths()
    if not lib:
        return

    if data:
        os.environ.setdefault("ESPEAK_DATA_PATH", data)
    os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", lib)

    # Preload what we can. The dynamic loader resolves a dependency by
    # SONAME and will not match an already-loaded object loaded from a
    # different path, so this only helps when LD_LIBRARY_PATH already
    # covers the directory — but it costs nothing and covers the common
    # case where the library is on the system path.
    try:
        import ctypes

        ctypes.CDLL(lib)
    except Exception:
        pass


def maybe_reexec_with_espeak() -> None:
    """Re-exec the process once with espeak-ng's directory on the loader path.

    `ctypes` resolves shared-library dependencies through the dynamic
    loader, which reads `LD_LIBRARY_PATH` once at process start — setting
    it later has no effect, and preloading by absolute path does not help
    because the loader matches dependencies by SONAME, not by path.

    So for a self-contained espeak install the process must be restarted
    with the library directory in `LD_LIBRARY_PATH`.

    This is ONLY safe to call from a process entrypoint (the CLI, the
    server launcher) — never from library code, where it would restart
    whatever was running. It is a no-op on Windows (where the DLL is
    installed on PATH) and a no-op when the library already resolves.
    """
    if os.name == "nt":
        return
    if os.environ.get("_CARLA_ESPEAK_REEXEC"):
        return

    lib, data, lib_dir = espeak_library_paths()
    if not lib:
        return

    # Already resolvable? Then there is nothing to do.
    try:
        import ctypes

        ctypes.CDLL(lib)
        return
    except Exception:
        pass

    env = dict(os.environ)
    parts = [lib_dir, str(Path(lib_dir) / "pulseaudio")]
    prev = env.get("LD_LIBRARY_PATH", "")
    if prev:
        parts.append(prev)
    env["LD_LIBRARY_PATH"] = os.pathsep.join(parts)
    if data:
        env["ESPEAK_DATA_PATH"] = data
    env["PHONEMIZER_ESPEAK_LIBRARY"] = lib
    env["_CARLA_ESPEAK_REEXEC"] = "1"

    try:
        orig = getattr(sys, "orig_argv", None)
        argv = list(orig) if orig else [sys.executable, *sys.argv]
        argv[0] = sys.executable
        os.execve(sys.executable, argv, env)
    except Exception:
        # Re-exec failed — carry on without it. The espeak fallback will
        # simply be disabled and out-of-dictionary words get skipped.
        pass

# Alias -> concrete voice id for each backend.
#
# NOTE ON IRISH: neither Piper nor Kokoro ships an Irish (en_IE / ga_IE)
# voice. Verified against the full upstream manifests — Piper's voices.json
# lists 182 voices and contains no en_IE entry; Kokoro-82M ships American,
# British, Japanese, Mandarin, Spanish, French, Hindi and Italian only.
# The `irish_*` aliases therefore resolve to the closest available British
# voices. They are kept as stable config-facing names so a future Irish
# voice model can be dropped in without changing anyone's TOML.
KOKORO_VOICE_MAP: dict[str, str] = {
    # Closest-to-Irish (British English) — recommended for the persona.
    "irish_female": "bf_emma",
    "irish_male": "bm_george",
    # American English.
    "female": "af_heart",
    "male": "am_michael",
    "default": "bf_emma",
    # Explicit voice ids pass straight through.
    "af_heart": "af_heart",
    "am_michael": "am_michael",
    "af_alloy": "af_alloy",
    "bf_emma": "bf_emma",
    "bm_george": "bm_george",
    "bm_lewis": "bm_lewis",
    "bf_alice": "bf_alice",
}

PIPER_VOICE_MAP: dict[str, str] = {
    # Closest-to-Irish (British English).
    "irish_female": "en_GB-alba-medium",
    "irish_male": "en_GB-alan-medium",
    # American English.
    "female": "en_US-amy-medium",
    "male": "en_US-ryan-medium",
    "default": "en_GB-alba-medium",
}


@dataclass
class TTSResult:
    ok: bool = False
    path: str = ""
    error: str = ""
    duration_ms: float = 0.0
    backend: str = ""


class TTSEngine:
    """Base class for CPU text-to-speech backends."""

    name = "base"

    def __init__(self, voice: str = "", model: str = "", speed: float = 1.0,
                 device: str = "cpu") -> None:
        self.voice = voice
        self.model = model
        self.speed = speed
        self.device = device
        self._lock = threading.Lock()

    def available(self) -> bool:
        raise NotImplementedError

    def synthesize(self, text: str, out_path: str | Path) -> TTSResult:
        raise NotImplementedError

    def speak(self, text: str) -> TTSResult:
        """Synthesize to a temp file and play it."""
        if not text or not text.strip():
            return TTSResult(ok=False, error="empty text", backend=self.name)
        tmp = Path(tempfile.gettempdir()) / f"carla_tts_{os.getpid()}_{threading.get_ident()}.wav"
        result = self.synthesize(text, tmp)
        if result.ok:
            result = self._play(tmp, result)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
        return result

    def _play(self, path: Path, result: TTSResult) -> TTSResult:
        if os.name == "nt":
            try:
                import winsound

                with wave.open(str(path), "rb") as wf:
                    winsound.PlaySound(
                        path, winsound.SND_FILENAME | winsound.SND_NODEFAULT
                    )
                return result
            except Exception as exc:
                result.error = f"playback failed: {exc}"
                return result
        for player in ("aplay", "afplay", "paplay"):
            exe = shutil.which(player)
            if exe:
                try:
                    subprocess.run(
                        [exe, str(path)],
                        check=True, capture_output=True, timeout=300,
                    )
                    return result
                except Exception as exc:
                    result.error = f"playback failed: {exc}"
                    return result
        result.error = "no audio player found (install alsa-utils / use Windows)"
        return result


# --------------------------------------------------------------------------- #
# Kokoro
# --------------------------------------------------------------------------- #
class KokoroTTS(TTSEngine):
    """Kokoro TTS — 82M params, CPU real-time."""

    name = "kokoro"

    def __init__(self, voice: str = "irish_female", model: str = "",
                 speed: float = 1.0, device: str = "cpu",
                 model_path: str = "") -> None:
        super().__init__(voice, model, speed, device)
        self.voice_id = KOKORO_VOICE_MAP.get(voice, voice or "af_heart")
        self.model_path = model_path
        self._pipeline = None
        self._model = None

    def available(self) -> bool:
        try:
            import kokoro  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def _lang_code(self) -> str:
        """Pick the G2P language pack from the voice id prefix.

        Kokoro ships `af_*`/`am_*` (American), `bf_*`/`bm_*` (British),
        `jf_*`/`jm_*` (Japanese) and so on. The two-letter prefix maps
        directly onto Kokoro's lang_code ('a' => American, 'b' => British).
        """
        prefix = self.voice_id[:2].lower()
        return {"af": "a", "am": "a", "bf": "b", "bm": "b",
                "jf": "j", "jm": "j", "zf": "z", "zm": "z"}.get(prefix, "a")

    def _ensure_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        from kokoro import KModel, KPipeline  # type: ignore

        # Point phonemizer at the espeak-ng library before Kokoro builds its
        # G2P, otherwise out-of-dictionary words (e.g. "C.A.R.L.A.") are
        # dropped from the phoneme stream.
        ensure_espeak_library()

        # Load the 82M model ONCE and share it across pipeline instances.
        # Constructing a second KPipeline loads a second copy of the model,
        # which the OS will OOM-kill (exit 137) on low-RAM machines.
        if self.model_path and Path(self.model_path).exists():
            # A local checkpoint avoids re-downloading ~315 MB and lets the
            # user place the model on a fast local disk.
            self._model = KModel(repo_id=self.model_path).to(self.device or "cpu").eval()
        else:
            self._model = KModel().to(self.device or "cpu").eval()
        self._pipeline = KPipeline(lang_code=self._lang_code(), model=self._model)
        return self._pipeline

    def synthesize(self, text: str, out_path: str | Path) -> TTSResult:
        try:
            import numpy as np
        except Exception:
            return TTSResult(ok=False, error="numpy not installed", backend=self.name)
        try:
            pipeline = self._ensure_pipeline()
        except Exception as exc:
            return TTSResult(ok=False, error=f"kokoro init failed: {exc}", backend=self.name)

        chunks: list[Any] = []
        try:
            for _gs, _ps, audio in pipeline(text, voice=self.voice_id, speed=self.speed):
                chunks.append(audio)
        except Exception as exc:
            return TTSResult(ok=False, error=f"kokoro synthesis failed: {exc}",
                             backend=self.name)

        if not chunks:
            return TTSResult(ok=False, error="no audio produced", backend=self.name)

        audio = np.concatenate(chunks)
        sr = 24000
        try:
            import soundfile as sf  # type: ignore

            sf.write(str(out_path), audio, sr)
        except Exception:
            try:
                import scipy.io.wavfile as wavfile  # type: ignore

                wavfile.write(str(out_path), sr, (np.clip(audio, -1, 1) * 32767).astype(np.int16))
            except Exception:
                # Pure-python WAV writer fallback.
                pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
                with wave.open(str(out_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(pcm)
        return TTSResult(ok=True, path=str(out_path), backend=self.name)


# --------------------------------------------------------------------------- #
# Piper
# --------------------------------------------------------------------------- #
class PiperTTS(TTSEngine):
    """Piper TTS — ONNX, very fast on CPU."""

    name = "piper"

    def __init__(self, voice: str = "irish_female", model: str = "",
                 speed: float = 1.0, device: str = "cpu") -> None:
        super().__init__(voice, model, speed, device)
        self.voice_id = self.model or PIPER_VOICE_MAP.get(voice, voice or "en_IE-jenny-medium")

    def available(self) -> bool:
        if shutil.which("piper"):
            return True
        try:
            import piper  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def _resolve_model(self) -> str | None:
        if self.model and Path(self.model).exists():
            return self.model
        roots = [
            Path(os.environ.get("PIPER_VOICES", "")) if os.environ.get("PIPER_VOICES") else None,
            Path.home() / ".local" / "share" / "piper-voices",
            Path.home() / ".piper",
            Path.home() / "voices" / "piper",
        ]
        for root in roots:
            if not root or not root.exists():
                continue
            for pattern in (f"{self.voice_id}.onnx", f"{self.voice_id}*.onnx"):
                matches = sorted(root.rglob(pattern))
                if matches:
                    return str(matches[0])
        return None

    def synthesize(self, text: str, out_path: str | Path) -> TTSResult:
        model_path = self._resolve_model()
        if not model_path:
            return TTSResult(
                ok=False,
                error=(
                    f"Piper voice '{self.voice_id}' not found. Download it to "
                    f"~/.local/share/piper-voices/ or set [voice].tts_model."
                ),
                backend=self.name,
            )
        # Piper shells out to the espeak-ng binary for phonemisation.
        ensure_espeak_library()
        env = dict(os.environ)
        espeak_data = Path.home() / ".local" / "espeak" / "espeak-ng-data"
        if espeak_data.is_dir():
            env["ESPEAK_DATA_PATH"] = str(espeak_data)
        local_lib = Path.home() / ".local" / "espeak"
        if local_lib.is_dir():
            prev = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = (
                f"{local_lib}{os.pathsep}{prev}" if prev else str(local_lib)
            )
        try:
            if shutil.which("piper"):
                proc = subprocess.run(
                    ["piper", "--model", model_path, "--output_file", str(out_path)],
                    input=text.encode("utf-8"),
                    capture_output=True, timeout=300, env=env,
                )
                if proc.returncode == 0 and Path(out_path).exists():
                    return TTSResult(ok=True, path=str(out_path), backend=self.name)
                return TTSResult(
                    ok=False,
                    error=(proc.stderr or b"").decode("utf-8", "ignore")[:400],
                    backend=self.name,
                )
            from piper.voice import PiperVoice  # type: ignore

            voice = PiperVoice.load(model_path)
            with wave.open(str(out_path), "wb") as wf:
                voice.synthesize(text, wf)
            return TTSResult(ok=True, path=str(out_path), backend=self.name)
        except Exception as exc:
            return TTSResult(ok=False, error=f"piper failed: {exc}", backend=self.name)


# --------------------------------------------------------------------------- #
# Silent
# --------------------------------------------------------------------------- #
class SilentTTS(TTSEngine):
    """No-op backend — used when TTS is disabled or unavailable."""

    name = "silent"

    def available(self) -> bool:
        return True

    def synthesize(self, text: str, out_path: str | Path) -> TTSResult:
        return TTSResult(ok=True, path="", backend=self.name)


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def build_tts(
    engine: str = "kokoro",
    voice: str = "irish_female",
    model: str = "",
    speed: float = 1.0,
    device: str = "cpu",
    model_path: str = "",
) -> TTSEngine:
    """Instantiate the configured TTS backend, falling back to silent."""
    engine = (engine or "kokoro").lower()
    if engine == "kokoro":
        backend = KokoroTTS(voice, model, speed, device, model_path=model_path)
        if backend.available():
            return backend
    elif engine == "piper":
        backend = PiperTTS(voice, model, speed, device)
        if backend.available():
            return backend
    elif engine == "silent":
        return SilentTTS()
    return SilentTTS()
