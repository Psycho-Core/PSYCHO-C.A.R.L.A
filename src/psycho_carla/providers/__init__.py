"""Pillars 1 & 2: model routing and provider adapters."""

from .base import (
    GenerationResult, Message, ModelAdapter, StreamChunk, ToolCall, Usage,
    available_adapters, build_adapter, get_adapter_class, register_adapter,
)
from .router import ModelRouter, TierResolution

__all__ = [
    "GenerationResult", "Message", "ModelAdapter", "StreamChunk", "ToolCall", "Usage",
    "available_adapters", "build_adapter", "get_adapter_class", "register_adapter",
    "ModelRouter", "TierResolution",
]
