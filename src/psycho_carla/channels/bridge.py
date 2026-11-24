"""
The Channel Bridge.

This is the piece that turns an inbound message into an C.A.R.L.A.
response, and it is entirely platform-agnostic. A channel adapter's only
job is to deliver a :class:`~psycho_carla.channels.base.ChannelMessage`;
everything that happens afterwards is the same on Slack, Telegram,
email and IRC.

The bridge handles:

* **Session mapping** — the same person keeps the same conversation
  across messages, so C.A.R.L.A. remembers context.
* **Command routing** — ``/help``, ``/agents``, ``/agent <name>``,
  ``/session``, ``/clear``, ``/notify``.
* **Agent dispatch** — everything else goes to the active agent.
* **Delivery** — the reply is sent back to the same conversation.

Layer: Pillar 3 (Channels).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus

logger = logging.getLogger("psycho_carla.channels")

#: The longest inbound message we will process.
DEFAULT_MAX_LENGTH = 8000

#: Session ids are namespaced by channel so two platforms cannot collide.
_SESSION_PREFIX = "chan"

HELP_TEXT = """C.A.R.L.A. at your service, {user}.

/help        — this message
/agents      — the agents available
/agent <name> — switch to an agent for this conversation
/sessions    — the conversations on this console
/session     — show the current session
/more        — fetch the rest of my last long reply
/notify <ch> — send background notifications to a channel
/clear       — start a fresh conversation
/status      — show the console status

Anything else is sent to the agent."""

AGENT_PROMPT = (
    "The user sent you a message on {channel}. "
    "Reply conversationally and concisely — this is a chat app, not a "
    "terminal. Do not use markdown tables or code fences unless the user "
    "explicitly asks for code."
)


class ChannelBridge:
    """Routes inbound channel messages to the runtime.

    The bridge implements the same shape as :class:`BaseChannel`, so it
    can be handed to an adapter as its message handler, and it can also
    send outbound messages on behalf of the runtime.
    """

    #: Minimum seconds between two notifications of the same event type.
    _NOTIFICATION_COOLDOWN = 300.0

    #: Replies longer than this are held for "/more".
    _PENDING_THRESHOLD = 3000

    def __init__(
        self,
        channels: Dict[str, BaseChannel],
        runtime: Any = None,
        session_store: Any = None,
        *,
        max_length: int = DEFAULT_MAX_LENGTH,
    ) -> None:
        self._channels = dict(channels)
        self._runtime = runtime
        self._session_store = session_store
        self._max_length = max_length
        self._default_agent = ""
        self._agent_overrides: Dict[str, str] = {}
        self._name_cache: str = ""
        self._handlers: Dict[str, Callable[[ChannelMessage], Optional[str]]] = {}
        self._notification_timestamps: Dict[str, float] = {}

        for channel in self._channels.values():
            channel.on_message(self.handle_incoming)

    @property
    def _user(self) -> str:
        """What C.A.R.L.A. calls the user ([persona] user_name, else Psycho)."""
        if self._name_cache:
            return self._name_cache
        persona = getattr(self._runtime, "persona", None)
        name = getattr(persona, "boss", "") if persona is not None else ""
        self._name_cache = name or "Psycho"
        return self._name_cache

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def channels(self) -> Dict[str, BaseChannel]:
        return dict(self._channels)

    def add_channel(self, channel: BaseChannel) -> None:
        """Attach a channel and wire up its handler."""
        self._channels[channel.channel_id] = channel
        channel.on_message(self.handle_incoming)

    def remove_channel(self, channel_id: str) -> bool:
        """Detach a channel."""
        return self._channels.pop(channel_id, None) is not None

    def set_runtime(self, runtime: Any) -> None:
        """Attach the runtime the bridge dispatches to."""
        self._runtime = runtime

    def set_default_agent(self, agent: str) -> None:
        """The agent used when a conversation has no override."""
        self._default_agent = agent

    def set_handler(
        self, channel_id: str, handler: Callable[[ChannelMessage], Optional[str]]
    ) -> None:
        """Override what a specific channel does with inbound messages.

        Useful for channels where you want a dedicated behaviour — a
        moderation channel that never reaches the agent, say.
        """
        self._handlers[channel_id] = handler

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect every channel."""
        for channel in self._channels.values():
            try:
                channel.connect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Channel %s failed to connect: %s",
                               channel.channel_id, exc)

    def disconnect(self) -> None:
        """Disconnect every channel."""
        for channel in self._channels.values():
            try:
                channel.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Channel %s failed to disconnect: %s",
                               channel.channel_id, exc)

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    def list_channels(self) -> List[str]:
        """Every destination across every channel."""
        result: List[str] = []
        for channel in self._channels.values():
            result.extend(channel.list_channels())
        return result

    def status(self) -> ChannelStatus:
        """The aggregate status across every channel."""
        statuses = [ch.status() for ch in self._channels.values()]
        if not statuses:
            return ChannelStatus.DISCONNECTED
        if any(s == ChannelStatus.CONNECTED for s in statuses):
            return ChannelStatus.CONNECTED
        if any(s == ChannelStatus.CONNECTING for s in statuses):
            return ChannelStatus.CONNECTING
        if all(s == ChannelStatus.ERROR for s in statuses):
            return ChannelStatus.ERROR
        return ChannelStatus.DISCONNECTED

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        """Send a message to a destination on whichever channel owns it."""
        for adapter in self._channels.values():
            if channel in adapter.list_channels():
                return adapter.send(
                    channel,
                    content,
                    conversation_id=conversation_id,
                    metadata=metadata,
                )
        logger.warning("No adapter found for channel %s", channel)
        return False

    def broadcast(self, content: str, *, metadata: Dict[str, Any] | None = None
                  ) -> int:
        """Send a message to every known destination. Returns the count."""
        sent = 0
        for adapter in self._channels.values():
            for destination in adapter.list_channels():
                if adapter.send(destination, content, metadata=metadata):
                    sent += 1
        return sent

    def describe(self) -> List[Dict[str, Any]]:
        """A status summary of every channel, for the HUD and the CLI."""
        return [adapter.describe() for adapter in self._channels.values()]

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def handle_incoming(self, message: ChannelMessage) -> Optional[str]:
        """Process one inbound message. Returns the reply text, if any."""
        handler = self._handlers.get(message.channel)
        if handler is not None:
            try:
                return handler(message)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Channel handler for %s failed: %s",
                               message.channel, exc)
                return f"Something went wrong on my end, {self._user}."

        content = message.content.strip()
        if not content:
            return None
        if len(content) > self._max_length:
            content = content[: self._max_length]

        session_id = self._session_for(message)

        # Commands.
        if content.startswith("/"):
            result = self._handle_command(message, content, session_id)
            if result is not None:
                return result

        return self._handle_chat(message, content, session_id)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def _session_for(self, message: ChannelMessage) -> str:
        """The session id for this conversation.

        Reuses an existing session when the platform lets us map one,
        otherwise derives a stable id from the channel and sender.
        """
        if self._session_store is not None:
            try:
                existing = self._session_store.find_by_channel(
                    message.channel, message.sender
                )
                if existing is not None:
                    session_id = getattr(existing, "id", "") or \
                        getattr(existing, "session_id", "")
                    if session_id:
                        return session_id
            except Exception as exc:  # noqa: BLE001
                logger.debug("Session lookup failed: %s", exc)

        session_id = f"{_SESSION_PREFIX}:{message.channel}:{message.sender}"
        if self._session_store is not None:
            try:
                # link_channel only updates an existing session, so the
                # session has to exist first.
                if self._session_store.get(session_id) is None:
                    self._session_store.create(
                        session_id,
                        metadata={
                            "channel": message.channel,
                            "sender": message.sender,
                        },
                    )
                self._session_store.link_channel(
                    session_id, message.channel, message.sender
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Session link failed: %s", exc)
        return session_id

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _handle_command(
        self,
        message: ChannelMessage,
        content: str,
        session_id: str,
    ) -> Optional[str]:
        parts = content.split(None, 1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command == "/help":
            return HELP_TEXT.format(user=self._user)

        if command == "/agents":
            return self._handle_agents_list()

        if command == "/agent":
            return self._handle_agent_switch(message, argument)

        if command == "/session":
            return f"Session {session_id}, {self._user}."

        if command == "/sessions":
            return self._handle_sessions()

        if command == "/more":
            return self._handle_more(session_id)

        if command == "/notify":
            return self._handle_notify(session_id, argument)

        if command == "/clear":
            return self._handle_clear(session_id)

        if command == "/status":
            return self._handle_status()

        # Not a command we know — treat it as ordinary chat.
        return None

    def _handle_sessions(self) -> str:
        """List the conversations this console knows about."""
        if self._session_store is None:
            return f"I am not keeping sessions, {self._user}."
        try:
            sessions = self._session_store.list_sessions()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not list sessions: %s", exc)
            return f"I could not reach my sessions just then, {self._user}."
        if not sessions:
            return f"No conversations yet, {self._user}."
        lines = []
        for session in sessions[:20]:
            if isinstance(session, dict):
                session_id = str(
                    session.get("session_id", "") or session.get("id", "")
                )
            else:
                session_id = (
                    getattr(session, "id", "")
                    or getattr(session, "session_id", "")
                )
            if session_id:
                lines.append(f"  {session_id}")
        if not lines:
            return f"No conversations yet, {self._user}."
        return "Conversations:\n" + "\n".join(lines)

    def _handle_more(self, session_id: str) -> str:
        """Return the pending full response, if one was held back."""
        if self._session_store is None:
            return "No pending response."
        try:
            pending = self._session_store.get_pending_response(session_id)
            if pending:
                self._session_store.clear_pending_response(session_id)
                return pending
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read the pending response: %s", exc)
        return "No pending response."

    def _handle_notify(self, session_id: str, argument: str) -> str:
        """Set the channel this conversation wants notifications on."""
        if self._session_store is None:
            return f"Notifications are not available, {self._user}."
        channel = argument.strip()
        try:
            if not channel:
                self._session_store.set_notification_preference(session_id, "")
                return f"Notifications are off, {self._user}."
            self._session_store.set_notification_preference(session_id, channel)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not set the notification preference: %s", exc)
            return f"I could not set that, {self._user}."
        if channel not in self._channels:
            return (
                f"Noted — but I have no '{channel}' channel configured, {self._user}."
            )
        return f"Notifications will be sent to {channel}."

    def _handle_agents_list(self) -> str:
        if self._runtime is None:
            return f"The console is not running, {self._user}."
        try:
            agents = self._runtime.list_agents()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not list agents: %s", exc)
            return f"I could not reach my agents just then, {self._user}."

        if not agents:
            return f"No agents are loaded, {self._user}."

        names = []
        for agent in agents:
            if isinstance(agent, dict):
                name = str(agent.get("name", ""))
                description = str(agent.get("description", "") or "")
            else:
                name = str(getattr(agent, "name", ""))
                description = str(getattr(agent, "description", "") or "")
            if not name:
                continue
            names.append(f"• {name} — {description}" if description else f"• {name}")
        return "Agents available:\n" + "\n".join(names)

    def _handle_agent_switch(self, message: ChannelMessage, argument: str) -> str:
        if not argument:
            return f"Which agent, {self._user}?"
        agent = argument.strip().lower()
        if self._runtime is not None:
            try:
                names = {
                    str(a.get("name", "")).lower()
                    if isinstance(a, dict)
                    else str(getattr(a, "name", "")).lower()
                    for a in self._runtime.list_agents()
                }
                if names and agent not in names:
                    return f"I have no agent called '{argument}', {self._user}."
            except Exception:  # noqa: BLE001
                pass
        self._agent_overrides[message.sender] = agent
        return f"Switched to {agent}, {self._user}."

    def _handle_clear(self, session_id: str) -> str:
        if self._session_store is not None:
            try:
                self._session_store.delete(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not clear session: %s", exc)
        return f"Fresh conversation, {self._user}."

    def _handle_status(self) -> str:
        if self._runtime is None:
            return f"The console is not running, {self._user}."
        try:
            status = self._runtime.status()
        except Exception:  # noqa: BLE001
            return f"I could not read the console status, {self._user}."

        if isinstance(status, dict):
            parts = []
            for key in ("agent", "tier", "model", "uptime"):
                value = status.get(key)
                if value:
                    parts.append(f"{key}: {value}")
            if parts:
                return "Console status — " + "; ".join(parts)
        return f"The console is running, {self._user}."

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def _handle_chat(
        self,
        message: ChannelMessage,
        content: str,
        session_id: str,
    ) -> Optional[str]:
        if self._runtime is None:
            return f"The console is not running, {self._user}."

        agent = self._agent_overrides.get(message.sender, "")
        if not agent:
            agent = self._default_agent

        prompt = AGENT_PROMPT.format(channel=message.channel)

        try:
            result = self._runtime.ask(
                prompt,
                agent=agent,
                session_id=session_id,
                history=self._history(session_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Channel dispatch failed: %s", exc)
            return f"Something went wrong on my end, {self._user}."

        if isinstance(result, dict):
            text = str(result.get("text") or result.get("speech") or "")
        else:
            text = str(result or "")

        self._remember(session_id, content, text)

        # A long reply may be truncated by the platform. Hold the full
        # text so "/more" can fetch it.
        reply = text.strip()
        if reply and len(reply) > self._PENDING_THRESHOLD:
            try:
                if self._session_store is not None:
                    self._session_store.set_pending_response(
                        session_id, reply
                    )
            except Exception:  # noqa: BLE001
                logger.debug("Could not store the pending response")
        return reply or None

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def notify(self, event_type: str, message: str) -> int:
        """Push a notification to every session that asked for one.

        The channel is whichever the session set with ``/notify``. The
        full text is also held as the session's pending response, so
        ``/more`` can fetch it if the platform truncated it.

        Returns the number of notifications delivered.
        """
        if self._session_store is None or not message:
            return 0

        # Rate limit: one notification per event type per five minutes.
        now = time.time()
        last = self._notification_timestamps.get(event_type, 0.0)
        if now - last < self._NOTIFICATION_COOLDOWN:
            return 0
        self._notification_timestamps[event_type] = now

        try:
            targets = self._session_store.get_notification_targets()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read notification targets: %s", exc)
            return 0

        delivered = 0
        for target in targets:
            channel_id = str(
                target.get("preferred_notification_channel", "") or ""
            )
            sender_id = str(target.get("sender_id", "") or "")
            if not channel_id:
                continue
            channel = self._channels.get(channel_id)
            if channel is None:
                logger.warning(
                    "No adapter for notification channel %s", channel_id
                )
                continue
            try:
                channel.send(sender_id, message)
                delivered += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to send notification to %s", channel_id
                )
            session_id = str(target.get("session_id", "") or "")
            if session_id:
                try:
                    self._session_store.set_pending_response(
                        session_id, message
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("Could not store the pending response")
        return delivered

    def _history(self, session_id: str) -> list[dict[str, str]]:
        """The recent conversation, for context."""
        if self._session_store is None:
            return []
        try:
            session = self._session_store.get(session_id)
            if session is None:
                return []
            messages = list(getattr(session, "messages", []) or [])
        except Exception:  # noqa: BLE001
            return []

        history = []
        for entry in messages[-12:]:
            role = getattr(entry, "role", "user")
            content = getattr(entry, "content", "")
            if content:
                history.append({"role": role, "content": content})
        return history

    def _remember(self, session_id: str, prompt: str, reply: str) -> None:
        """Record the exchange in the session store."""
        if self._session_store is None:
            return
        try:
            self._session_store.add_message(
                session_id, "user", prompt, channel="channel"
            )
            if reply:
                self._session_store.add_message(
                    session_id, "assistant", reply, channel="channel"
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not record exchange: %s", exc)


__all__ = ["ChannelBridge", "DEFAULT_MAX_LENGTH", "HELP_TEXT"]
