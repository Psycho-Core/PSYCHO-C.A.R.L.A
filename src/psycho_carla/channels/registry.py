"""
Channel registry.

Adapters register themselves at import time via the
:func:`register_channel` decorator, and the registry discovers them
without any central list to keep in sync. This mirrors how upstream
OpenJarvis does it, and it means adding a channel is a single new file —
no edits to a master list.

Usage::

    from psycho_carla.channels import register_channel

    @register_channel("telegram")
    class TelegramChannel(BaseChannel):
        ...
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Type

from .base import BaseChannel, ChannelStatus


class ChannelRegistry:
    """Registry of channel adapter classes, keyed by channel id."""

    _channels: dict[str, Type[BaseChannel]] = {}

    @classmethod
    def register(
        cls, channel_id: str
    ) -> Callable[[Type[BaseChannel]], Type[BaseChannel]]:
        """Class decorator registering an adapter under ``channel_id``."""

        def _decorate(klass: Type[BaseChannel]) -> Type[BaseChannel]:
            klass.channel_id = channel_id
            cls._channels[channel_id] = klass
            return klass

        return _decorate

    @classmethod
    def get(cls, channel_id: str) -> Type[BaseChannel] | None:
        """The adapter class for a channel id, or None."""
        return cls._channels.get(channel_id)

    @classmethod
    def all(cls) -> dict[str, Type[BaseChannel]]:
        """Every registered adapter class."""
        return dict(cls._channels)

    @classmethod
    def ids(cls) -> list[str]:
        """Every registered channel id, sorted."""
        return sorted(cls._channels)

    @classmethod
    def create(
        cls, channel_id: str, options: dict[str, Any] | None = None
    ) -> BaseChannel | None:
        """Instantiate a registered adapter."""
        klass = cls._channels.get(channel_id)
        if klass is None:
            return None
        return klass(options or {})

    @classmethod
    def unregister(cls, channel_id: str) -> bool:
        """Remove an adapter. Mainly for tests."""
        return cls._channels.pop(channel_id, None) is not None

    @classmethod
    def clear(cls) -> None:
        """Remove every adapter. Mainly for tests."""
        cls._channels.clear()

    @classmethod
    def __iter__(cls) -> Iterator[tuple[str, Type[BaseChannel]]]:
        return iter(sorted(cls._channels.items()))


def register_channel(channel_id: str) -> Callable[[Type[BaseChannel]], Type[BaseChannel]]:
    """Register a channel adapter. See :mod:`psycho_carla.channels.registry`."""
    return ChannelRegistry.register(channel_id)


def available_channels() -> list[str]:
    """Every registered channel id."""
    return ChannelRegistry.ids()


def create_channel(
    channel_id: str, options: dict[str, Any] | None = None
) -> BaseChannel | None:
    """Instantiate a registered channel adapter by id."""
    return ChannelRegistry.create(channel_id, options)


__all__ = [
    "ChannelRegistry",
    "ChannelStatus",
    "available_channels",
    "create_channel",
    "register_channel",
]
