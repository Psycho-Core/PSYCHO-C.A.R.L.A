"""
PSYCHO-C.A.R.L.A. — Voice System.

Ties TTS, STT, wake-word detection and barge-in together, and enforces the
**dual-channel rule**: C.A.R.L.A. speaks a concise summary while the raw
text (code, diffs, tables) is routed to the UI untouched.

Layer: Pillar 3 / Persona.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.config import CarlaConfig
from ..core.persona import ChannelPayload, Persona
from .stt import BargeIn, Microphone, WakeWordDetector, build_stt
from .tts import TTSEngine, build_tts, configure_espeak_environment


@dataclass
class VoiceState:
    """The live voice state, surfaced in the HUD."""

    enabled: bool = True
    listening: bool = False
    speaking: bool = False
    waiting_for_wake: bool = False
    last_transcript: str = ""
    last_spoken: str = ""
    tts_backend: str = ""
    stt_backend: str = ""
    wake_enabled: bool = False
    barge_in: bool = False
    mic_available: bool = False
    tts_available: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "listening": self.listening,
            "speaking": self.speaking,
            "waiting_for_wake": self.waiting_for_wake,
            "last_transcript": self.last_transcript[:500],
            "last_spoken": self.last_spoken[:500],
            "tts_backend": self.tts_backend,
            "stt_backend": self.stt_backend,
            "wake_enabled": self.wake_enabled,
            "barge_in": self.barge_in,
            "mic_available": self.mic_available,
            "tts_available": self.tts_available,
            "errors": self.errors[-5:],
        }


class VoiceSystem:
    """The C.A.R.L.A. voice pipeline."""

    def __init__(self, config: CarlaConfig, persona: Persona | None = None) -> None:
        # Must run before Kokoro builds its G2P pipeline — it may re-exec
        # the process so the dynamic loader can find espeak-ng.
        configure_espeak_environment()
        self.config = config
        self.persona = persona or Persona(config.persona)
        self.state = VoiceState()
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []

        vc = config.voice
        self.tts: TTSEngine = build_tts(
            engine=vc.tts_engine,
            voice=vc.tts_voice or vc.voice_id,
            model=vc.tts_model,
            speed=vc.tts_speed,
            device=vc.tts_device,
            model_path=getattr(vc, "tts_model_path", "") or "",
        )
        self.stt = build_stt(
            engine=vc.stt_engine,
            model=vc.stt_model,
            device=vc.stt_device,
            compute_type=vc.stt_compute_type,
            download_root=config.models_dir,
        )
        self.mic = Microphone()
        self.wake = WakeWordDetector(vc.wake_word)
        self.barge_in = BargeIn(self.mic)

        self.state.tts_backend = self.tts.name
        self.state.stt_backend = self.stt.name
        self.state.wake_enabled = vc.wake_word_enabled
        self.state.barge_in = vc.barge_in
        self.state.mic_available = self.mic.available()
        self.state.tts_available = getattr(self.tts, "available", lambda: False)()
        self.state.enabled = vc.enabled

        self._running = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self.config.voice.barge_in and self.state.mic_available:
            self.barge_in.start()
        if self.config.voice.wake_word_enabled:
            self._thread = threading.Thread(target=self._wake_loop, name="carla-voice",
                                            daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.barge_in.stop()
        self.mic.stop()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._running = False

    def on(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, data)
            except Exception:
                continue

    # ------------------------------------------------------------------ #
    # Speaking
    # ------------------------------------------------------------------ #
    def speak(self, text: str, *, interruptible: bool = True) -> dict[str, Any]:
        """Speak a concise summary via CPU TTS."""
        vc = self.config.voice
        if not vc.enabled or not text or not text.strip():
            return {"spoken": False, "reason": "disabled_or_empty"}

        if len(text.strip()) < vc.speak_min_chars:
            return {"spoken": False, "reason": "too_short"}

        with self._lock:
            self.state.speaking = True
        self._emit("voice_state", {"speaking": True, "text": text[:400]})

        if interruptible:
            self.barge_in.mark_speaking(True)

        try:
            result = self.tts.speak(text)
        except Exception as exc:
            with self._lock:
                self.state.speaking = False
                self.state.errors.append(f"{type(exc).__name__}: {exc}")
            self._emit("voice_state", {"speaking": False})
            return {"spoken": False, "reason": str(exc)}

        interrupted = interruptible and self.barge_in.interrupted()
        with self._lock:
            self.state.speaking = False
            self.state.last_spoken = text
        self.barge_in.mark_speaking(False)
        self._emit("voice_state", {"speaking": False, "text": text[:400]})

        return {
            "spoken": bool(result.ok),
            "backend": result.backend,
            "interrupted": interrupted,
            "error": result.error,
            "duration_ms": round(result.duration_ms, 2),
        }

    def speak_payload(self, payload: ChannelPayload) -> dict[str, Any]:
        """Speak only the speech channel of a split payload."""
        if not self.config.voice.channel_split:
            return self.speak(payload.display or payload.speech)
        return self.speak(payload.speech)

    # ------------------------------------------------------------------ #
    # Listening
    # ------------------------------------------------------------------ #
    def listen_once(self, timeout: float = 0.0) -> str:
        """Record until silence, transcribe, and return the text."""
        vc = self.config.voice
        if not vc.enabled:
            return ""
        if not self.state.mic_available:
            self.state.errors.append("microphone unavailable (install sounddevice)")
            return ""

        self.mic.start()
        with self._lock:
            self.state.listening = True
        self._emit("voice_state", {"listening": True})
        try:
            audio = self.mic.record_until_silence(
                silence_seconds=1.2,
                max_seconds=timeout or vc.listen_timeout,
            )
            text = self.stt.transcribe(audio, self.mic.sample_rate)
        except Exception as exc:
            self.state.errors.append(f"{type(exc).__name__}: {exc}")
            text = ""
        finally:
            with self._lock:
                self.state.listening = False
                self.state.last_transcript = text
            self._emit("voice_state", {"listening": False, "transcript": text[:400]})
        return text

    def _wake_loop(self) -> None:
        """Continuously watch for the wake word."""
        if not self.state.mic_available:
            return
        if not self.wake.available():
            self.state.errors.append("openWakeWord not installed")
            self.state.wake_enabled = False
            return

        self.mic.start()
        with self._lock:
            self.state.waiting_for_wake = True
        self._emit("voice_state", {"waiting_for_wake": True})

        chunk = 1280
        while not self._stop.is_set():
            try:
                audio = self.mic.read(chunk)
            except Exception:
                time.sleep(0.2)
                continue
            if len(audio) == 0:
                time.sleep(0.1)
                continue
            event = self.wake.detect(audio, self.mic.sample_rate)
            if event.detected:
                self._emit("wake_word", {"score": event.score,
                                         "phrase": self.config.voice.wake_word})
                # Listen for the command that follows the wake word.
                threading.Thread(target=self._command_after_wake, daemon=True).start()

    def _command_after_wake(self) -> None:
        time.sleep(0.35)  # small gap so we don't transcribe the wake word itself
        text = self.listen_once()
        if text:
            self._emit("voice_command", {"text": text})

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def status(self) -> dict[str, Any]:
        data = self.state.to_dict()
        data["wake_word"] = self.config.voice.wake_word
        data["tts_voice"] = self.config.voice.tts_voice
        data["channel_split"] = self.config.voice.channel_split
        return data
