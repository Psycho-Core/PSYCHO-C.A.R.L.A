"""
Matrix channel.

Matrix is a federated protocol — each homeserver exposes a
Client-Server API. This adapter logs in with the standard
``m.login.password`` flow, then long-polls ``/sync`` for new messages.

Because Matrix is federated, C.A.R.L.A. can be reached from any
Matrix client (Element, FluffyChat, SchildiChat) on any server, and can
itself join a room on a different server.

Setup:
1. Create a Matrix account (or use a dedicated bot account).
2. Note the homeserver, user id and password.

    carla channels add matrix --homeserver https://matrix.org \\
        --user-id @carla:matrix.org --password <password> --room <room-id>
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel

#: Matrix's practical message length.
MAX_MESSAGE_LENGTH = 32768


@register_channel("matrix")
class MatrixChannel(BaseChannel):
    """Matrix Client-Server API adapter."""

    channel_id = "matrix"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._homeserver: str = str(
            self.options.get("homeserver", "https://matrix.org") or
            "https://matrix.org"
        )
        self._user_id: str = str(self.options.get("user_id", "") or "")
        self._password: str = str(self.options.get("password", "") or "")
        self._token: str = str(self.options.get("token", "") or "")
        self._rooms: list[str] = [
            str(r) for r in (self.options.get("rooms") or []) if r
        ]
        if self.options.get("room"):
            self._rooms.append(str(self.options["room"]))
        self._device_id: str = str(self.options.get("device_id", "CARLA") or "CARLA")
        self._sync_token: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._homeserver:
            missing.append("homeserver")
        if not self._token and not (self._user_id and self._password):
            missing.append("token or user_id+password")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Matrix needs a homeserver and credentials."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return

        self.set_status(ChannelStatus.CONNECTING)
        if not self._token:
            if not self._login():
                self.set_status(ChannelStatus.ERROR)
                return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._sync_loop, name="carla-matrix", daemon=True
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

    def _login(self) -> bool:
        try:
            result = request_json(
                build_url(self._homeserver, "/_matrix/client/v3/login"),
                method="POST",
                json_body={
                    "type": "m.login.password",
                    "identifier": {
                        "type": "m.id.user",
                        "user": self._user_id,
                    },
                    "password": self._password,
                    "initial_device_display_name": "C.A.R.L.A.",
                },
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False

        token = (result.get("access_token") if isinstance(result, dict) else "") or ""
        if not token:
            self._last_error = (
                str(result.get("error", "Matrix login failed."))
                if isinstance(result, dict) else "Matrix login failed."
            )
            return False
        self._token = token
        return True

    # -- messaging ---------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        txn_id = str(int(__import__("time").time() * 1000))
        body: Dict[str, Any] = {
            "msgtype": "m.text",
            "body": truncate(content, MAX_MESSAGE_LENGTH),
        }
        reference = (metadata or {}).get("reply_to_message_id") or conversation_id
        if reference:
            body["m.relates_to"] = {"m.in_reply_to": {"event_id": str(reference)}}

        try:
            request_json(
                build_url(
                    self._homeserver,
                    f"/_matrix/client/v3/rooms/{channel}/send/m.room.message/{txn_id}",
                ),
                method="PUT",
                headers={"Authorization": f"Bearer {self._token}"},
                json_body=body,
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return list(self._rooms)

    # -- syncing -----------------------------------------------------------

    def _sync_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._sync()
            except ChannelError as exc:
                self._last_error = str(exc)
                self._stop.wait(5.0)
                continue
            self._handle_sync(data)
            self._stop.wait(0.5)

    def _sync(self) -> dict[str, Any]:
        url = build_url(
            self._homeserver,
            "/_matrix/client/v3/sync",
            {
                "timeout": 30000,
                "since": self._sync_token or None,
            },
        )
        result = request_json(
            url, headers={"Authorization": f"Bearer {self._token}"}, timeout=45.0
        )
        return result if isinstance(result, dict) else {}

    def _handle_sync(self, data: dict[str, Any]) -> None:
        self._sync_token = str(data.get("next_batch", "") or self._sync_token)

        rooms = ((data.get("rooms") or {}).get("join") or {})
        for room_id, room in rooms.items():
            if self._rooms and room_id not in self._rooms:
                continue
            events = ((room.get("timeline") or {}).get("events") or [])
            for event in events:
                self._handle_event(room_id, event)

    def _handle_event(self, room_id: str, event: dict[str, Any]) -> None:
        if event.get("type") != "m.room.message":
            return
        sender = str(event.get("sender", ""))
        if sender == self._user_id:
            return

        content = (event.get("content") or {}).get("body", "")
        if not content or not isinstance(content, str):
            return

        inbound = ChannelMessage(
            channel="matrix",
            sender=sender,
            content=content,
            message_id=str(event.get("event_id", "")),
            conversation_id=room_id,
            metadata={"room_id": room_id, "origin_server_ts":
                      event.get("origin_server_ts")},
        )
        reply = self.deliver(inbound)
        if reply:
            self.send(
                room_id,
                reply,
                metadata={"reply_to_message_id": inbound.message_id},
            )


__all__ = ["MatrixChannel"]
