"""Tests for the built-in fallback responder.

The fallback is what makes C.A.R.L.A. a program rather than a
collection of scripts: it answers even when no model is installed and
no model server is running.
"""

from __future__ import annotations


def _adapter():
    from psycho_carla.providers.adapters.local_fallback import (
        LocalFallbackAdapter,
    )

    return LocalFallbackAdapter()


def _ask(text: str) -> str:
    from psycho_carla.providers.base import Message

    return _adapter().generate(
        [Message(role="user", content=text)]
    ).text


# ----------------------------------------------------------------------
# It answers
# ----------------------------------------------------------------------

def test_it_answers_a_greeting():
    answer = _ask("Hello")

    assert answer
    assert "Psycho" in answer
    assert "C.A.R.L.A." in answer


def test_it_lists_what_it_can_do():
    answer = _ask("What can you do?")

    assert "Model Manager" in answer
    assert "memories" in answer
    assert "channels" in answer


def test_it_tells_the_time():
    answer = _ask("What time is it?")

    assert "Psycho" in answer
    # A time, not just a refusal.
    assert any(ch.isdigit() for ch in answer)


def test_it_is_honest_about_not_having_a_model():
    answer = _ask("Explain quantum entanglement in detail")

    assert "without a language model" in answer
    assert "quantum entanglement" in answer
    assert "Model Manager" in answer


def test_it_handles_an_empty_question():
    answer = _ask("")

    assert answer
    assert "Psycho" in answer


def test_it_uses_the_persona_it_is_given():
    from psycho_carla.providers.base import Message
    from psycho_carla.providers.adapters.local_fallback import (
        LocalFallbackAdapter,
    )

    adapter = LocalFallbackAdapter({"persona": "Sir"})
    answer = adapter.generate([Message(role="user", content="Hello")]).text

    assert "Sir" in answer
    assert "Boss" not in answer


# ----------------------------------------------------------------------
# It behaves like an adapter
# ----------------------------------------------------------------------

def test_it_reports_token_usage():
    from psycho_carla.providers.base import Message

    result = _adapter().generate(
        [Message(role="user", content="Hello there")]
    )

    assert result.usage.prompt_tokens > 0
    assert result.usage.completion_tokens > 0
    assert result.usage.total_tokens == (
        result.usage.prompt_tokens + result.usage.completion_tokens
    )


def test_it_streams():
    from psycho_carla.providers.base import Message

    chunks = list(
        _adapter().stream([Message(role="user", content="Hello")])
    )

    assert chunks
    assert "".join(chunks).strip()


def test_it_is_registered():
    from psycho_carla.providers.base import get_adapter_class

    assert get_adapter_class("local_fallback") is not None


# ----------------------------------------------------------------------
# The router uses it
# ----------------------------------------------------------------------

def test_the_router_falls_back_when_the_provider_is_down(tmp_path):
    """With no model server running, the console still answers."""
    from psycho_carla.core.config import load_config
    from psycho_carla.providers.base import Message
    from psycho_carla.providers.router import ModelRouter

    config = load_config()
    tier = config.intelligence.tiers["fast_tier"]
    tier.provider = "ollama"
    # Nothing listens on this port.
    tier.base_url = "http://127.0.0.1:1"
    tier.extra = {"base_url": "http://127.0.0.1:1"}

    router = ModelRouter(config)
    result = router.generate(
        [Message(role="user", content="Hello")], capability_tier="fast_tier"
    )

    assert result.text
    assert "Psycho" in result.text
    assert result.provider == "local_fallback"


def test_the_fallback_can_be_switched_off(monkeypatch):
    """A user who wants the raw error can have it."""
    from psycho_carla.core.config import load_config
    from psycho_carla.providers.base import Message
    from psycho_carla.providers.router import ModelRouter

    monkeypatch.setenv("PSYCHO_CARLA_NO_FALLBACK", "1")

    config = load_config()
    tier = config.intelligence.tiers["fast_tier"]
    tier.provider = "ollama"
    tier.base_url = "http://127.0.0.1:1"
    tier.extra = {"base_url": "http://127.0.0.1:1"}

    router = ModelRouter(config)
    result = router.generate(
        [Message(role="user", content="Hello")], capability_tier="fast_tier"
    )

    # The adapter reports its own error, so the result comes back empty
    # rather than falling back.
    assert not (result.text or "").strip()
    assert result.error
