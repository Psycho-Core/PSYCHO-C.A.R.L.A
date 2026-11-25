"""Bundled model adapters. Importing this package registers every adapter."""

from .afm import AfmAdapter, afm_available
from .cloud import CloudAdapter, GemmaCppAdapter, MultiEngineAdapter
from .engines import (
    ENGINE_DEFAULT_HOSTS,
    ENGINE_SPECS,
    IN_PROCESS_ENGINES,
    OPENAI_COMPAT_ENGINES,
    AppleFmAdapter,
    ExoAdapter,
    LemonadeAdapter,
    LiteLLMAdapter,
    LMStudioAdapter,
    MLXAdapter,
    NIMAdapter,
    NexaAdapter,
    SGLangAdapter,
    UzuAdapter,
    VLLMAdapter,
    normalize_openai_base_url,
)
from .generic_http import GenericHttpAdapter
from .llamacpp import LlamaCppAdapter
from .local_fallback import LocalFallbackAdapter
from .native_gguf import NativeGgufAdapter
from .native_onnx import NativeOnnxAdapter
from .openrouter import OpenRouterAdapter
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter

# Re-exported so callers can ask the adapter package what is available
# without reaching into the registry module.
from ..base import available_adapters, build_adapter, get_adapter_class

__all__ = [
    "NativeGgufAdapter",
    "OpenRouterAdapter",
    "AfmAdapter",
    "AppleFmAdapter",
    "CloudAdapter",
    "GemmaCppAdapter",
    "MultiEngineAdapter",
    "ENGINE_DEFAULT_HOSTS",
    "ENGINE_SPECS",
    "ExoAdapter",
    "GenericHttpAdapter",
    "IN_PROCESS_ENGINES",
    "LemonadeAdapter",
    "LiteLLMAdapter",
    "LMStudioAdapter",
    "LlamaCppAdapter",
    "LocalFallbackAdapter",
    "MLXAdapter",
    "NIMAdapter",
    "NativeOnnxAdapter",
    "NexaAdapter",
    "OPENAI_COMPAT_ENGINES",
    "OllamaAdapter",
    "OpenAICompatAdapter",
    "SGLangAdapter",
    "UzuAdapter",
    "VLLMAdapter",
    "afm_available",
    "available_adapters",
    "build_adapter",
    "get_adapter_class",
    "normalize_openai_base_url",
]
