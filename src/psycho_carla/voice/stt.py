"""
PSYCHO-C.A.R.L.A. — Speech-to-Text (CPU-only) & Wake Word.

STT: `faster-whisper` (CTranslate2, CPU int8 by default) — fast and light.
Wake word: `openWakeWord` — runs ONNX on CPU, tiny footprint, no cloud.
Barge-in: a background mic monitor that can interrupt the current utterance.

Layer: Pillar 3 / Persona.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    remainder: str = ""
    """Words spoken after the wake phrase ("hey carla, what time is it" → "what time is it")."""


def _norm(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9']+", (text or "").lower())


#: Things a speech recogniser commonly writes for "carla".
_NAME_VARIANTS = {"carla", "karla", "carlo", "carlah", "calla", "carler", "carly", "karlo"}
_HEY_VARIANTS = {"hey", "hay", "hi", "okay", "ok", "a", "hey,"}


def match_wake_phrase(text: str, phrase: str = "hey carla") -> tuple[bool, float, str]:
    """Does ``text`` start with the wake phrase? → (matched, score, remainder).

    Tolerant of how recognisers mis-hear it ("hey karla", "hay carlo"), and of
    the phrase being followed by a command. Only the first few words are
    considered so a sentence that merely mentions Carla does not wake her.
    """
    from difflib import SequenceMatcher

    words = _norm(text)
    target = _norm(phrase)
    if not words or not target:
        return False, 0.0, ""
    name = target[-1]
    names = _NAME_VARIANTS if name == "carla" else {name}
    for i in range(min(len(words), 4)):
        w = words[i]
        is_name = w in names or SequenceMatcher(None, w, name).ratio() >= 0.8
        if not is_name:
            continue
        before = words[:i]
        if len(target) == 1 or (before and before[-1] in _HEY_VARIANTS | {target[0]}) or \
                (before and SequenceMatcher(None, before[-1], target[0]).ratio() >= 0.7):
            return True, 1.0 if w == name else 0.85, " ".join(words[i + 1:])
        if len(target) > 1 and i == 0:
            continue
    return False, 0.0, ""


class TranscriptWakeDetector:
    """Wake word by listening for the phrase with the speech recogniser.

    Needs no trained model, so "hey carla" works today. It buffers audio while
    someone is speaking (simple energy gate), and when they pause transcribes
    that short utterance and checks for the phrase. Costs a little CPU only
    when speech is heard — silence is never transcribed.
    """

    def __init__(self, stt: Any, wake_word: str = "hey carla",
                 energy_threshold: float = 0.015, max_seconds: float = 4.0,
                 silence_seconds: float = 0.6) -> None:
        self.stt = stt
        self.wake_word = wake_word
        self.energy_threshold = energy_threshold
        self.max_seconds = max_seconds
        self.silence_seconds = silence_seconds
        self._buf: list[np.ndarray] = []
        self._speech = 0.0
        self._quiet = 0.0
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            return bool(self.stt.available())
        except Exception:
            return False

    def detect(self, audio: np.ndarray, sample_rate: int = 16000) -> WakeEvent:
        if audio is None or len(audio) == 0:
            return WakeEvent()
        secs = len(audio) / float(sample_rate)
        loud = float(np.sqrt(np.mean(np.square(audio.astype(np.float32))))) >= self.energy_threshold
        with self._lock:
            if loud:
                self._buf.append(audio)
                self._speech += secs
                self._quiet = 0.0
            elif self._buf:
                self._buf.append(audio)
                self._quiet += secs
            else:
                return WakeEvent()
            done = self._quiet >= self.silence_seconds or self._speech + self._quiet >= self.max_seconds
            if not done:
                return WakeEvent()
            clip = np.concatenate(self._buf)
            spoke = self._speech
            self._buf, self._speech, self._quiet = [], 0.0, 0.0
        if spoke < 0.25:
            return WakeEvent()
        try:
            text = self.stt.transcribe(clip, sample_rate)
        except Exception:
            return WakeEvent()
        matched, score, rest = match_wake_phrase(text, self.wake_word)
        return WakeEvent(detected=matched, score=score, remainder=rest)


class WakeWordDetector:
    """openWakeWord detector for the configured wake phrase."""

    def __init__(self, wake_word: str = "hey carla", model: str = "") -> None:
        # ``model`` must be a custom-trained "hey carla" model file. There is
        # deliberately no default: openWakeWord's bundled models respond to
        # other phrases, not this one.
        self.wake_word = wake_word
        self.model_name = model
        self._model = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        if not self.model_name:
            return False
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
            key = Path(self.model_name).stem
            raw = prediction.get(key, prediction.get(self.model_name, 0.0)) if isinstance(prediction, dict) else 0.0
            score = float(max(raw.values())) if isinstance(raw, dict) and raw else float(raw or 0.0)
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
