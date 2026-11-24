"""A built-in fallback that works with nothing installed.

C.A.R.L.A. is meant to be a program you open and use. Before you
download a model — or when the model server is not running — there has
to be something that answers. This adapter is that something.

It is deliberately tiny and deliberately honest: it does not pretend to
be an intelligence. It reflects the request back, shows what the
console is capable of, and points at the next step. It is the only
adapter in the box that needs no network, no model file and no
third-party package.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Iterator, Sequence

from ..base import (
    GenerationResult,
    Message,
    ModelAdapter,
    Usage,
    register_adapter,
)

logger = logging.getLogger("psycho_carla.providers.local_fallback")


#: What the console can do, offered when there is no model to answer.
_CAPABILITIES = (
    "chat with an agent",
    "run scheduled jobs",
    "store and search memories",
    "connect channels (Slack, Telegram, Discord and 27 more)",
    "call tools",
)


def _looks_like_greeting(text: str) -> bool:
    return bool(re.match(
        r"^\s*(hi|hey|hello|yo|greetings|good\s+(morning|afternoon|evening))\b",
        text,
        re.IGNORECASE,
    ))


def _looks_like_capability_question(text: str) -> bool:
    return bool(re.search(
        r"\b(what can you do|who are you|what are you|help|how do (i|you)\b"
        r"|what do you do|commands?|capabilit)",
        text,
        re.IGNORECASE,
    ))


def _looks_like_time_question(text: str) -> bool:
    return bool(re.search(r"\b(what time|what'?s the time|the date|today)\b",
                          text, re.IGNORECASE))


@register_adapter
class LocalFallbackAdapter(ModelAdapter):
    """Answers when no model is available.

    Options
    -------
    persona:
        Name the assistant addresses the user by. Defaults to "Psycho".
    """

    name = "local_fallback"
    supports_tools = False
    supports_vision = False
    supports_streaming = True
    supports_json_mode = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.persona = str(
            self.options.get("persona", "Psycho") or "Psycho"
        )

    # ------------------------------------------------------------------
    # The answer
    # ------------------------------------------------------------------

    def _respond(self, prompt: str) -> str:
        text = (prompt or "").strip()

        if not text:
            return (
                "I'm here, {boss}. Ask me something — or download a model "
                "from the Model Manager and I'll use that instead."
                .format(boss=self.persona)
            )

        if _looks_like_time_question(text):
            now = time.strftime("%A %d %B %Y, %H:%M")
            return f"It's {now}, {self.persona}."

        if _looks_like_capability_question(text):
            lines = [
                f"I'm C.A.R.L.A., {self.persona} — and right now I'm "
                f"running without a language model, so I'm limited. "
                f"What I can do:",
            ]
            lines.extend(f"  • {item}" for item in _CAPABILITIES)
            lines.append(
                "\nOpen the Model Manager, download a model, and I'll run it "
                "inside the app — no Ollama needed."
            )
            return "\n".join(lines)

        if _looks_like_greeting(text):
            return (
                "Hello, {boss}. I'm C.A.R.L.A., and I'm running without a "
                "language model at the moment — but the console is live. "
                "Download one from the Model Manager and I'll be a great deal "
                "more useful.".format(boss=self.persona)
            )

        # Anything else: reflect the request honestly, and say what would
        # make a real answer possible.
        return (
            "I'd like to give you a real answer to that, {boss}, but I'm "
            "running without a language model right now.\n\n"
            "You asked: \"{text}\"\n\n"
            "Open the Model Manager, download a model (the small one is "
            "quick), and I'll answer properly — it runs inside C.A.R.L.A., "
            "with no Ollama and no separate server."
            .format(boss=self.persona, text=text)
        )

    # ------------------------------------------------------------------
    # The adapter interface
    # ------------------------------------------------------------------

    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "",
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        prompt = ""
        for message in messages:
            if message.role == "user":
                prompt = message.content
                break

        text = self._respond(prompt)

        # Report a plausible token count so the telemetry and the
        # OpenAI-compatible API behave the way clients expect.
        prompt_tokens = sum(
            len((m.content or "").split()) for m in messages
        )
        completion_tokens = len(text.split())

        return GenerationResult(
            text=text,
            model="local-fallback",
            provider=self.name,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            raw={"fallback": True},
        )

    def stream(
        self,
        messages: Sequence[Message],
        capability_tier: str = "",
        **kwargs: Any,
    ) -> Iterator[str]:
        result = self.generate(messages, capability_tier, **kwargs)
        # Emit in word-sized pieces so the HUD renders it naturally.
        for piece in result.text.split(" "):
            yield piece + " "

    def list_models(self) -> list[dict[str, Any]]:
        return [{
            "id": "local-fallback",
            "name": "C.A.R.L.A. (no model loaded)",
            "provider": self.name,
        }]
