"""
Signal channel.

Signal's ecosystem has no official bot API — the usual route is
``signal-cli`` (the Rust reference client) running locally, which C.A.R.L.A.
drives as a subprocess. This adapter shells out to it.

That means Signal needs ``signal-cli`` installed and logged in on the
same machine. It is a local-first channel with no cloud dependency.

Setup:
1. Install ``signal-cli``.
2. Log in: ``signal-cli link <your-phone-number>``.
3. Point C.A.R.L.A. at it.

    carla channels add signal --number +1234567890
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .registry import register_channel

#: signal-cli's JSON mode emits one JSON object per line.
_JSON_MODE = "--json"


@register_channel("signal")
class SignalChannel(BaseChannel):
    """Signal adapter driving the ``signal-cli`` reference client."""

    channel_id = "signal"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._number: str = str(self.options.get("number", "") or "")
        self._binary: str = str(
            self.options.get("binary", "signal-cli") or "signal-cli"
        )
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_error: str = ""
        self._peers: set[str] = set()

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._number:
            missing.append("number")
        if shutil.which(self._binary) is None:
            missing.append("signal-cli (not installed)")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self._number:
            self._last_error = "Signal phone number is not configured."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        if shutil.which(self._binary) is None:
            self._last_error = (
                f"'{self._binary}' was not found on PATH. Install signal-cli "
                "and log in with 'signal-cli link <number>'."
            )
            self.set_status(ChannelStatus.UNCONFIGURED)
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._receive_loop, name="carla-signal", daemon=True
        )
        self._thread.start()
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self.set_status(ChannelStatus.DISCONNECTED)

    # -- messaging ---------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        cmd = [
            self._binary,
            "-u", self._number,
            "send",
            "-m", content,
            channel,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._last_error = str(exc)
            return False
        if proc.returncode != 0:
            self._last_error = (proc.stderr or "signal-cli failed").strip()[:300]
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return sorted(self._peers)

    # -- receiving ---------------------------------------------------------

    def _receive_loop(self) -> None:
        cmd = [
            self._binary,
            "-u", self._number,
            "receive",
            "--json",
            "-o",
            "json",
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._last_error = str(exc)
            self.set_status(ChannelStatus.ERROR)
            return

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    envelope = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._handle_envelope(envelope)
        finally:
            try:
                proc.terminate()
            except Exception:
                pass

    def _handle_envelope(self, envelope: dict[str, Any]) -> None:
        # signal-cli's JSON envelope: {"envelope": {"dataMessage": {...}}}
        if envelope.get("type") not in (None, "data", None):
            pass
        inner = envelope.get("envelope") or envelope
        data = inner.get("dataMessage") or inner.get("syncMessage") or {}
        if not data:
            return

        message = data.get("message") or ""
        if not message:
            return

        source = str(inner.get("source") or inner.get("sourceNumber") or "")
        if not source:
            return
        self._peers.add(source)

        inbound = ChannelMessage(
            channel="signal",
            sender=source,
            content=message,
            message_id=str(inner.get("timestamp", "")),
            conversation_id=source,
            metadata={"source": source},
        )
        reply = self.deliver(inbound)
        if reply:
            self.send(source, reply)


__all__ = ["SignalChannel"]
