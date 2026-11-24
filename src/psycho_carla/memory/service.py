"""
PSYCHO-C.A.R.L.A. — Memory Service.

Runs alongside a session and distils durable facts out of the conversation
stream, so the agent remembers preferences, decisions and context without
being told to.

Extraction is model-driven when a model is available, with a deterministic
heuristic fallback so the feature still works offline.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .store import Memory, MemoryStore

# Signals that a statement is worth remembering.
_PREFERENCE_PATTERNS = [
    r"\bi (?:prefer|like|love|hate|dislike|always|never)\b",
    r"\bmy (?:name|email|address|timezone|preferred|default|goal|birthday)\b",
    r"\b(?:always|from now on|going forward|remember that)\b",
    r"\bi (?:work|live|use|study|own)\b",
    r"\bcall me\b",
    r"\bdon'?t (?:ever )?(?:use|do)\b",
]
_DECISION_PATTERNS = [
    r"\b(?:we|i) (?:decided|chose|picked|settled on|agreed)\b",
    r"\bthe plan is\b",
    r"\bi'?ll (?:use|go with|switch to)\b",
]
_FACT_PATTERNS = [
    r"\bthe (?:project|repo|server|api|database|stack|framework) (?:is|uses|runs on)\b",
    r"\bversion \d+\.\d+",
    r"\bport \d{2,5}\b",
]

_ALL_PATTERNS = _PREFERENCE_PATTERNS + _DECISION_PATTERNS + _FACT_PATTERNS

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "this", "that", "these",
    "those", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in",
    "on", "at", "for", "with", "it", "its", "i", "you", "we", "they", "he", "she",
    "do", "does", "did", "so", "as", "by", "from", "up", "out", "no", "yes",
}


@dataclass
class ExtractedFact:
    content: str
    kind: str = "fact"
    confidence: float = 0.6
    tags: list[str] = field(default_factory=list)


class MemoryService:
    """Extracts and persists durable facts from conversations."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        enabled: bool = True,
        max_facts: int = 2000,
        extractor: Callable[[Sequence[dict[str, str]]], list[ExtractedFact]] | None = None,
    ) -> None:
        self.store = store
        self.enabled = enabled
        self.max_facts = max_facts
        self._extractor = extractor
        self._lock = threading.Lock()
        self._recent_hashes: set[str] = set()

    # -- configuration ------------------------------------------------------ #
    def set_extractor(self, extractor: Callable[..., list[ExtractedFact]]) -> None:
        self._extractor = extractor

    # -- public API ---------------------------------------------------------- #
    def observe(self, user_message: str, assistant_message: str = "") -> list[Memory]:
        """Inspect a completed exchange and persist anything worth keeping."""
        if not self.enabled:
            return []
        conversation = [
            {"role": "user", "content": user_message},
        ]
        if assistant_message:
            conversation.append({"role": "assistant", "content": assistant_message})

        facts = self._extract(conversation)
        stored: list[Memory] = []
        for fact in facts:
            key = _fingerprint(fact.content)
            with self._lock:
                if key in self._recent_hashes:
                    continue
                self._recent_hashes.add(key)
                if len(self._recent_hashes) > 5000:
                    self._recent_hashes = set(list(self._recent_hashes)[-2000:])
            try:
                mem_id = self.store.store(
                    fact.content, kind=fact.kind, tags=fact.tags,
                    source="conversation",
                )
                mem = self.store.get(mem_id)
                if mem:
                    stored.append(mem)
            except Exception:
                continue
        self._enforce_cap()
        return stored

    def store_explicit(self, content: str, kind: str = "fact",
                       tags: list[str] | None = None) -> Memory | None:
        """Store a memory the user asked for directly."""
        try:
            mem_id = self.store.store(content, kind=kind, tags=tags, source="explicit")
            return self.store.get(mem_id)
        except Exception:
            return None

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.store.search(query, top_k=top_k)]

    # -- extraction ----------------------------------------------------------- #
    def _extract(self, conversation: Sequence[dict[str, str]]) -> list[ExtractedFact]:
        if self._extractor is not None:
            try:
                return self._extractor(conversation)
            except Exception:
                pass
        return self._heuristic_extract(conversation)

    def _heuristic_extract(
        self, conversation: Sequence[dict[str, str]]
    ) -> list[ExtractedFact]:
        facts: list[ExtractedFact] = []
        for turn in conversation:
            if turn.get("role") != "user":
                continue
            for sentence in _split_sentences(turn.get("content", "")):
                lowered = sentence.lower()
                for pattern in _PREFERENCE_PATTERNS:
                    if re.search(pattern, lowered):
                        facts.append(ExtractedFact(sentence, "preference", 0.75))
                        break
                else:
                    for pattern in _DECISION_PATTERNS:
                        if re.search(pattern, lowered):
                            facts.append(ExtractedFact(sentence, "event", 0.7))
                            break
                    else:
                        for pattern in _FACT_PATTERNS:
                            if re.search(pattern, lowered):
                                facts.append(ExtractedFact(sentence, "fact", 0.6))
                                break
        return facts

    def _enforce_cap(self) -> None:
        stats = self.store.stats()
        if stats["memories"] <= self.max_facts:
            return
        excess = stats["memories"] - self.max_facts
        with self.store._lock:  # noqa: SLF001 - internal maintenance
            conn = self.store._connect()  # noqa: SLF001
            rows = conn.execute(
                "SELECT id FROM memories ORDER BY created_at ASC LIMIT ?", (excess,)
            ).fetchall()
            conn.executemany("DELETE FROM memories WHERE id = ?", [(r[0],) for r in rows])
            conn.commit()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    out = []
    for p in parts:
        p = p.strip()
        if len(p) < 12 or len(p) > 400:
            continue
        words = [w for w in re.findall(r"[a-z']+", p.lower()) if w not in _STOPWORDS]
        if len(words) < 3:
            continue
        out.append(p)
    return out


def _fingerprint(text: str) -> str:
    import hashlib

    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.blake2b(norm.encode(), digest_size=12).hexdigest()


def build_model_extractor(
    router: Any, tier: str = "fast_tier"
) -> Callable[[Sequence[dict[str, str]]], list[ExtractedFact]]:
    """Create an extractor that asks the model for structured facts."""
    from ..providers.base import Message

    prompt = (
        "Extract durable facts about the user from the conversation below. "
        "Only include things that will still be true and useful later: "
        "preferences, personal details, decisions, project context. "
        "Skip pleasantries, one-off task instructions, and anything uncertain. "
        "Respond with a JSON array of objects: "
        '{"content": "...", "kind": "preference|fact|event|note", "confidence": 0.0-1.0}. '
        "Return [] if there is nothing worth remembering."
    )

    def _extract(conversation: Sequence[dict[str, str]]) -> list[ExtractedFact]:
        convo = "\n".join(f"{t.get('role')}: {t.get('content')}" for t in conversation)
        messages = [
            Message(role="system", content=prompt),
            Message(role="user", content=convo),
        ]
        result = router.generate(messages, tier, agent="memory_curator")
        if result.error or not result.text:
            return []
        return _parse_extraction(result.text)

    return _extract


def _parse_extraction(text: str) -> list[ExtractedFact]:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        data = json.loads(cleaned)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    facts: list[ExtractedFact] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        try:
            confidence = float(item.get("confidence", 0.6))
        except Exception:
            confidence = 0.6
        facts.append(
            ExtractedFact(
                content=content,
                kind=str(item.get("kind", "fact")),
                confidence=max(0.0, min(1.0, confidence)),
                tags=item.get("tags", []) if isinstance(item.get("tags"), list) else [],
            )
        )
    return facts
