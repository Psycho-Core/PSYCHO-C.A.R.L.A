"""
PSYCHO-C.A.R.L.A. — Speech-to-Text (CPU-only) & Wake Word.

STT: `faster-whisper` (CTranslate2, CPU int8 by default) — fast and light.
Wake word: `openWakeWord` — runs ONNX on CPU, tiny footprint, no cloud.
Barge-in: a background mic monitor that can interrupt the current utterance.

Layer: Pillar 3 / Persona.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np


# --------------------------------------------------------------------------- #
# STT
# --------------------------------------------------------------------------- #
class STT:
    """Base class for speech-to-text backends."""

    name = "base"

    def __init__(
        self,
        model: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | Path | None = None,
    ) -> None:
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.download_root = str(download_root) if download_root else None
        self._model = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return False

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        return ""

    def transcribe_file(self, path: str | Path) -> str:
        return ""


class WhisperSTT(STT):
    """faster-whisper transcription on CPU."""

    name = "faster-whisper"

    def __init__(
        self,
        model: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | Path | None = None,
    ) -> None:
        super().__init__(model, device, compute_type, download_root)

    def available(self) -> bool:
        try:
            import faster_whisper  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        from faster_whisper import WhisperModel  # type: ignore

        kwargs: dict[str, Any] = {
            "device": self.device,
            "compute_type": self.compute_type,
        }
        if self.download_root:
            Path(self.download_root).mkdir(parents=True, exist_ok=True)
            kwargs["download_root"] = self.download_root
        self._model = WhisperModel(self.model, **kwargs)
        return self._model

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe a float32 mono numpy array."""
        if audio is None or len(audio) == 0:
            return ""
        try:
            model = self._ensure_model()
        except Exception:
            return ""
        try:
            with self._lock:
                segments, _info = model.transcribe(
                    audio,
                    language="en",
                    beam_size=1,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                text = "".join(seg.text for seg in segments)
            return text.strip()
        except Exception:
            return ""

    def transcribe_file(self, path: str | Path) -> str:
        try:
            import soundfile as sf  # type: ignore

            audio, sr = sf.read(str(path), dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            return self.transcribe(audio, sr)
        except Exception:
            return ""


class WhisperOpenAI(STT):
    """Fallback using the `openai-whisper` package (slower, torch-based)."""

    name = "whisper"

    def __init__(self, model: str = "base.en", device: str = "cpu",
                 compute_type: str = "int8", download_root: str | Path | None = None) -> None:
        super().__init__(model, device, compute_type, download_root)

    def available(self) -> bool:
        try:
            import whisper  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def _ensure_model(self) -> Any:
        if self._model is None:
            import whisper  # type: ignore

            self._model = whisper.load_model(self.model, device=self.device)
        return self._model


class SilentSTT(STT):
    """No-op STT."""

    name = "silent"

    def available(self) -> bool:
        return True

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        return ""


# --------------------------------------------------------------------------- #
# Microphone capture
# --------------------------------------------------------------------------- #
class Microphone:
    """Continuously captures mono 16 kHz audio frames from the default device."""

    def __init__(self, sample_rate: int = 16000, frame_size: int = 1024,
                 device: int | None = None) -> None:
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        self.device = device
        self._stream = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            import sounddevice  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def start(self) -> bool:
        with self._lock:
            if self._stream is not None:
                return True
            try:
                import sounddevice as sd  # type: ignore

                self._stream = sd.RawInputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.frame_size,
                    dtype="int16",
                    channels=1,
                    device=self.device,
                )
                self._stream.start()
                return True
            except Exception:
                self._stream = None
                return False

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def read(self, frames: int = 16000) -> np.ndarray:
        """Read approximately `frames` samples as float32 in [-1, 1]."""
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        collected = bytearray()
        try:
            while len(collected) < frames * 2:
                data, overflow = self._stream.read(self.frame_size)
                collected.extend(data)
        except Exception:
            return np.zeros(0, dtype=np.float32)
        if not collected:
            return np.zeros(0, dtype=np.float32)
        audio = np.frombuffer(bytes(collected), dtype=np.int16).astype(np.float32) / 32768.0
        return audio

    def record_until_silence(
        self, silence_seconds: float = 1.2, max_seconds: float = 15.0,
        energy_threshold: float = 0.012,
    ) -> np.ndarray:
        """Record until a stretch of silence is detected."""
        chunk = int(self.sample_rate * 0.1)
        frames: list[np.ndarray] = []
        silent_chunks = 0
        needed_silent = max(1, int(silence_seconds / 0.1))
        max_chunks = int(max_seconds / 0.1)
        for _ in range(max_chunks):
            block = self.read(chunk)
            if len(block) == 0:
                break
            frames.append(block)
            rms = float(np.sqrt(np.mean(np.square(block))))
            if rms < energy_threshold:
                silent_chunks += 1
                if silent_chunks >= needed_silent:
                    break
            else:
                silent_chunks = 0
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames)


# --------------------------------------------------------------------------- #
# Wake word
# --------------------------------------------------------------------------- #
@dataclass
class WakeEvent:
    detected: bool = False
    score: float = 0.0
    timestamp: float = field(default_factory=time.time)


class WakeWordDetector:
    """openWakeWord detector for the configured wake phrase."""

    def __init__(self, wake_word: str = "hey carla", model: str = "hey_jarvis") -> None:
        self.wake_word = wake_word
        self.model_name = model
        self._model = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            import openwakeword  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def _ensure_model(self) -> Any:
        if self._model is None:
            from openwakeword.model import Model  # type: ignore

            self._model = Model(wakeword_models=[self.model_name], inference_framework="onnx")
        return self._model

    def detect(self, audio: np.ndarray, sample_rate: int = 16000) -> WakeEvent:
        """Run detection over a chunk of audio."""
        try:
            model = self._ensure_model()
        except Exception:
            return WakeEvent(detected=False, score=0.0)
        if audio is None or len(audio) < sample_rate // 4:
            return WakeEvent(detected=False, score=0.0)
        try:
            with self._lock:
                prediction = model.predict(audio)
            scores = prediction[self.model_name] if isinstance(prediction, dict) else {}
            score = float(max(scores.values())) if scores else 0.0
            return WakeEvent(detected=score >= 0.5, score=score)
        except Exception:
            return WakeEvent(detected=False, score=0.0)


# --------------------------------------------------------------------------- #
# Barge-in
# --------------------------------------------------------------------------- #
class BargeIn:
    """Interrupts the current utterance when the user starts speaking.

    Runs a background monitor thread. When speech energy is detected while
    C.A.R.L.A. is speaking, it signals the voice system to stop playback.
    """

    def __init__(self, mic: Microphone, threshold: float = 0.02) -> None:
        self.mic = mic
        self.threshold = threshold
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._speaking = threading.Event()
        self._interrupt = threading.Event()
        self._enabled = True

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def mark_speaking(self, speaking: bool) -> None:
        if speaking:
            self._speaking.set()
        else:
            self._speaking.clear()
            self._interrupt.clear()

    def interrupted(self) -> bool:
        return self._interrupt.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="carla-bargein",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(0.25):
            if not self._enabled or not self._speaking.is_set():
                continue
            try:
                audio = self.mic.read(16000)
            except Exception:
                continue
            if len(audio) == 0:
                continue
            rms = float(np.sqrt(np.mean(np.square(audio))))
            if rms > self.threshold:
                self._interrupt.set()
                self.mark_speaking(False)


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def build_stt(engine: str = "faster-whisper", model: str = "base.en",
              device: str = "cpu", compute_type: str = "int8",
              download_root: str | Path | None = None) -> WhisperSTT:
    engine = (engine or "faster-whisper").lower()
    if engine in ("faster-whisper", "faster_whisper"):
        backend = WhisperSTT(model, device, compute_type, download_root)
        if backend.available():
            return backend
    elif engine == "whisper":
        backend = WhisperOpenAI(model, device, compute_type, download_root)
        if backend.available():
            return backend
    elif engine == "silent":
        return SilentSTT(model, device, compute_type, download_root)
    return SilentSTT(model, device, compute_type, download_root)
