"""
Email channel.

Uses the standard library's ``smtplib`` and ``imaplib`` — no extra
dependencies. Outbound mail goes over SMTP; inbound mail is polled over
IMAP.

This is the one channel that works with essentially any account:
Gmail, Outlook, Fastmail, a company Exchange server, or a plain
Postfix on your own box.

Setup (Gmail example — an app password is required):

    carla channels add email --smtp-host smtp.gmail.com --smtp-port 587 \\
        --imap-host imap.gmail.com --user you@gmail.com --password <app-password>

For Gmail, IMAP must be enabled on the account, and the username is
your full address.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import smtplib
import ssl
import threading
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .registry import register_channel


@register_channel("email_channel")
class EmailChannel(BaseChannel):
    """Email adapter over SMTP and IMAP."""

    channel_id = "email_channel"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._smtp_host: str = str(self.options.get("smtp_host", "") or "")
        self._smtp_port: int = int(self.options.get("smtp_port", 587) or 587)
        self._smtp_use_ssl: bool = bool(self.options.get("smtp_ssl", False))
        self._imap_host: str = str(self.options.get("imap_host", "") or "")
        self._imap_port: int = int(self.options.get("imap_port", 993) or 993)
        self._username: str = str(self.options.get("user", "") or "")
        self._password: str = str(self.options.get("password", "") or "")
        self._folder: str = str(self.options.get("folder", "INBOX") or "INBOX")
        self._poll_interval: float = float(
            self.options.get("poll_interval", 60.0) or 60.0
        )
        self._seen_uids: set[bytes] = set()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._smtp_host:
            missing.append("smtp_host")
        if not self._username:
            missing.append("user")
        if not self._password:
            missing.append("password")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Email needs an SMTP host, user and password."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return

        self.set_status(ChannelStatus.CONNECTING)
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=20) as srv:
                srv.ehlo()
                srv.starttls(context=ssl.create_default_context())
                srv.ehlo()
                srv.login(self._username, self._password)
        except Exception as exc:
            self._last_error = f"SMTP login failed: {exc}"
            self.set_status(ChannelStatus.ERROR)
            return

        self._stop.clear()
        if self._imap_host:
            self._thread = threading.Thread(
                target=self._poll_loop, name="carla-email", daemon=True
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
        if not self._smtp_host or not self._username:
            return False

        message = EmailMessage()
        message["From"] = str(self.options.get("from_address", "") or self._username)
        message["To"] = channel
        message["Subject"] = str(
            (metadata or {}).get("subject", "C.A.R.L.A.") or "C.A.R.L.A."
        )
        message.set_content(content)

        # Threading headers, so replies chain correctly.
        if conversation_id:
            message["In-Reply-To"] = conversation_id
            message["References"] = conversation_id

        try:
            if self._smtp_use_ssl:
                with smtplib.SMTP_SSL(self._smtp_host, self._smtp_port,
                                      timeout=30) as srv:
                    srv.login(self._username, self._password)
                    srv.send_message(message)
            else:
                with smtplib.SMTP(self._smtp_host, self._smtp_port,
                                  timeout=30) as srv:
                    srv.ehlo()
                    srv.starttls(context=ssl.create_default_context())
                    srv.ehlo()
                    srv.login(self._username, self._password)
                    srv.send_message(message)
        except Exception as exc:
            self._last_error = str(exc)
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return []

    # -- polling -----------------------------------------------------------

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                self._last_error = str(exc)
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        try:
            client = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
        except Exception as exc:
            self._last_error = f"IMAP connect failed: {exc}"
            return

        try:
            client.login(self._username, self._password)
            client.select(self._folder)
            status, data = client.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return

            for uid in data[0].split():
                if uid in self._seen_uids:
                    continue
                self._seen_uids.add(uid)
                status, payload = client.fetch(uid, "(RFC822)")
                if status != "OK" or not payload:
                    continue
                raw = payload[0][1]
                parsed = email.message_from_bytes(raw)
                self._handle_message(parsed)
                # Mark as seen so we do not process it twice.
                try:
                    client.store(uid, "+FLAGS", "\\Seen")
                except Exception:
                    pass
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def _handle_message(self, parsed: Any) -> None:
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        content = payload.decode("utf-8", "replace")
                        break
            else:
                content = ""
        else:
            payload = parsed.get_payload(decode=True)
            content = payload.decode("utf-8", "replace") if payload else ""

        content = content.strip()
        if not content:
            return

        sender = email.utils.parseaddr(parsed.get("From", ""))[1]
        subject = parsed.get("Subject", "")

        inbound = ChannelMessage(
            channel="email_channel",
            sender=sender,
            content=content,
            message_id=parsed.get("Message-ID", ""),
            conversation_id=sender,
            metadata={
                "subject": subject,
                "references": parsed.get("References", ""),
            },
        )
        reply = self.deliver(inbound)
        if reply:
            self.send(
                sender,
                reply,
                metadata={"subject": subject or "Re: C.A.R.L.A.",
                          "reply_to": parsed.get("Message-ID", "")},
            )


@register_channel("email")
class EmailAlias(EmailChannel):
    """Short alias for :class:`EmailChannel`."""

    channel_id = "email"


__all__ = ["EmailAlias", "EmailChannel"]
