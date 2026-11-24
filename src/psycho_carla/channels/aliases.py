"""
Upstream-compatible channel aliases.

OpenJarvis registers several channels under two names — a canonical one
and a variant with a ``_channel`` suffix — and ships a ``slack_daemon``
that runs Socket Mode as a supervised background service rather than an
in-process adapter.

This module provides the aliases, so an existing upstream configuration
keeps working unchanged, and the daemon, which is the Slack adapter
managed by the C.A.R.L.A. daemon supervisor.
"""

from __future__ import annotations


from .irc import IRCChannel
from .line import LineChannel
from .mastodon import MastodonChannel
from .matrix import MatrixChannel
from .microsoft import MessengerChannel
from .nostr import NostrChannel
from .reddit import RedditChannel
from .registry import register_channel
from .rocketchat import RocketChatChannel
from .signal import SignalChannel
from .twilio_sms import TwilioSMSChannel
from .twitch import TwitchChannel
from .twitter import TwitterChannel
from .viber import ViberChannel
from .xmpp import XMPPChannel
from .zulip import ZulipChannel


@register_channel("irc_channel")
class IrcAlias(IRCChannel):
    """Upstream alias for :class:`~psycho_carla.channels.irc.IRCChannel`."""

    channel_id = "irc_channel"


@register_channel("signal_channel")
class SignalAlias(SignalChannel):
    """Upstream alias for :class:`~psycho_carla.channels.signal.SignalChannel`."""

    channel_id = "signal_channel"


@register_channel("matrix_channel")
class MatrixAlias(MatrixChannel):
    """Upstream alias for :class:`~psycho_carla.channels.matrix.MatrixChannel`."""

    channel_id = "matrix_channel"


@register_channel("line_channel")
class LineAlias(LineChannel):
    """Upstream alias for :class:`~psycho_carla.channels.line.LineChannel`."""

    channel_id = "line_channel"


@register_channel("mastodon_channel")
class MastodonAlias(MastodonChannel):
    """Upstream alias for :class:`~psycho_carla.channels.mastodon.MastodonChannel`."""

    channel_id = "mastodon_channel"


@register_channel("nostr_channel")
class NostrAlias(NostrChannel):
    """Upstream alias for :class:`~psycho_carla.channels.nostr.NostrChannel`."""

    channel_id = "nostr_channel"


@register_channel("reddit_channel")
class RedditAlias(RedditChannel):
    """Upstream alias for :class:`~psycho_carla.channels.reddit.RedditChannel`."""

    channel_id = "reddit_channel"


@register_channel("rocketchat_channel")
class RocketChatAlias(RocketChatChannel):
    """Upstream alias for :class:`~psycho_carla.channels.rocketchat.RocketChatChannel`."""

    channel_id = "rocketchat_channel"


@register_channel("zulip_channel")
class ZulipAlias(ZulipChannel):
    """Upstream alias for :class:`~psycho_carla.channels.zulip.ZulipChannel`."""

    channel_id = "zulip_channel"


@register_channel("twitch_channel")
class TwitchAlias(TwitchChannel):
    """Upstream alias for :class:`~psycho_carla.channels.twitch.TwitchChannel`."""

    channel_id = "twitch_channel"


@register_channel("twitter_channel")
class TwitterAlias(TwitterChannel):
    """Upstream alias for :class:`~psycho_carla.channels.twitter.TwitterChannel`."""

    channel_id = "twitter_channel"


@register_channel("viber_channel")
class ViberAlias(ViberChannel):
    """Upstream alias for :class:`~psycho_carla.channels.viber.ViberChannel`."""

    channel_id = "viber_channel"


@register_channel("xmpp_channel")
class XMPPAlias(XMPPChannel):
    """Upstream alias for :class:`~psycho_carla.channels.xmpp.XMPPChannel`."""

    channel_id = "xmpp_channel"


@register_channel("messenger")
class MessengerAlias(MessengerChannel):
    """Upstream's canonical registration key for Facebook Messenger.

    Upstream registers ``messenger``; ``messenger_channel`` is the
    module filename, not the registry key.
    """

    channel_id = "messenger"


@register_channel("twilio")
class TwilioAlias(TwilioSMSChannel):
    """Upstream's canonical registration key for Twilio SMS.

    Upstream registers ``twilio``; ``twilio_sms`` is the module filename,
    not the registry key.
    """

    channel_id = "twilio"


__all__ = [
    "IrcAlias",
    "LineAlias",
    "MastodonAlias",
    "MatrixAlias",
    "MessengerAlias",
    "NostrAlias",
    "RedditAlias",
    "RocketChatAlias",
    "SignalAlias",
    "TwilioAlias",
    "TwitchAlias",
    "TwitterAlias",
    "ViberAlias",
    "XMPPAlias",
    "ZulipAlias",
]
