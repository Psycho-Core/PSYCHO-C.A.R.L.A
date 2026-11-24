"""
Channels — how C.A.R.L.A. is reached, and how it replies.

A channel is a messaging platform. Each one is a thin adapter that
translates between that platform's protocol and C.A.R.L.A.'s
:class:`~psycho_carla.channels.base.BaseChannel` contract. Everything
above the adapter — session mapping, command routing, agent dispatch —
is shared.

Built-in channels
-----------------

=============== =============================================== =========
Channel        Transport                                  Auth
=============== =============================================== =========
telegram       Bot API long-poll                          token
slack          Socket Mode (WebSocket)                     token + app token
discord        Inbound HTTP endpoint                       token + channel
whatsapp       Meta Cloud API webhook                      token + phone id
signal         signal-cli subprocess                       number
email          SMTP out / IMAP in                         password
matrix         Client-Server API /sync                     password
irc            Raw TCP                                    optional
webhook        Inbound HTTP (JSON)                         optional
webchat        Built-in browser widget                     none
twilio_sms     Twilio Messages REST                        sid + token
mattermost     REST API                                   token
rocketchat     REST API                                   token
zulip          Messages API                               user + token
line           Messaging API                              token
viber          Business Messages API                      token
feishu         Open platform                              token
google_chat    Incoming webhook                           none
sendblue       iMessage/SMS bridge                        api key
bluebubbles    BlueBubbles server                         password
twitter        X API v2                                   bearer token
mastodon       Any instance's REST API                    access token
reddit         OAuth Reddit                               client id + creds
nostr          Relay protocol (WebSocket)                  private key
twitch         IRC over WebSocket                         oauth token
xmpp           XMPP client                                jid + password
imessage_daemon macOS Messages (osascript)                none
gmail          Gmail API                                  oauth token
=============== =============================================== =========

Every adapter is discoverable through the registry, and importing this
package registers them all.

Layer: Pillar 3 (Channels).
"""

from __future__ import annotations

import importlib

from .base import BaseChannel, ChannelHandler, ChannelMessage, ChannelStatus
from .bridge import ChannelBridge
from .registry import (
    ChannelRegistry,
    available_channels,
    create_channel,
    register_channel,
)

#: The modules to import for their registration side effect.
_CHANNEL_MODULES = [
    # Primary adapters
    "telegram",
    "slack",
    "discord",
    "whatsapp",
    "signal",
    "email_channel",
    "matrix",
    "irc",
    "generic",
    "microsoft",
    # REST-based platforms
    "twilio_sms",
    "mattermost",
    "rocketchat",
    "zulip",
    "line",
    "viber",
    "feishu",
    "google_chat",
    "sendblue",
    "bluebubbles",
    "webchat",
    # Social platforms
    "twitter",
    "mastodon",
    "reddit",
    "nostr",
    "twitch",
    "xmpp",
    "imessage_daemon",
    "slack_daemon",
    "gmail",
    # Upstream-compatible aliases
    "aliases",
]

for _mod in _CHANNEL_MODULES:
    try:
        importlib.import_module(f".{_mod}", __name__)
    except ImportError:  # pragma: no cover - a broken optional adapter
        # A single broken adapter must not break the whole package.
        continue


__all__ = [
    "BaseChannel",
    "ChannelBridge",
    "ChannelHandler",
    "ChannelMessage",
    "ChannelRegistry",
    "ChannelStatus",
    "available_channels",
    "create_channel",
    "register_channel",
]
