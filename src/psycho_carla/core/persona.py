"""
PSYCHO-C.A.R.L.A. — Persona & Dual-Channel Output Splitter.

The splitter is the critical piece: it decides what C.A.R.L.A. *says* (TTS, CPU)
versus what it *shows* (the holographic UI). Code blocks, diffs, tables, and
long technical payloads never get spoken — they are routed to the UI verbatim.

Layer: Core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import PersonaConfig

# --------------------------------------------------------------------------- #
# Channel payload
# --------------------------------------------------------------------------- #
@dataclass
class ChannelPayload:
    """The result of splitting one model response into two channels."""

    speech: str = ""            # concise, speakable summary -> TTS
    display: str = ""           # full text for the UI (may be identical)
    code_blocks: list[str] = field(default_factory=list)
    is_code_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "speech": self.speech,
            "display": self.display,
            "code_blocks": list(self.code_blocks),
            "is_code_only": self.is_code_only,
        }


# --------------------------------------------------------------------------- #
# Structural detection
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+\-.]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_DIFF_RE = re.compile(r"^(diff --git |index [0-9a-f]{7,}|--- |\+\+\+ |@@ )", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]{1,120}`")
_URL_RE = re.compile(r"https?://\S{6,}")
_TABLE_RE = re.compile(r"^\|.+\|\s*$\n^\|[\s:\-|]+\|\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_JSONISH_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.DOTALL)
_URL_LINE_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*https?://\S+\s*$", re.MULTILINE)


def extract_code_blocks(text: str) -> tuple[str, list[str]]:
    """Split `text` into (text-without-fences, list-of-code-blocks)."""
    blocks: list[str] = []

    def _grab(m: re.Match[str]) -> str:
        blocks.append(m.group(0))
        return ""

    stripped = _FENCE_RE.sub(_grab, text)
    return stripped, blocks


def strip_structural_noise(text: str) -> str:
    """Remove inline code spans, bare URLs, tables and diffs from prose."""
    out = _TABLE_RE.sub("", text)
    out = _DIFF_RE.sub("", out)
    out = _INLINE_CODE_RE.sub(" ", out)
    out = _URL_LINE_RE.sub("", out)
    out = _URL_RE.sub("", out)
    out = re.sub(r"`{1,3}", "", out)
    return out


def looks_like_code(text: str) -> bool:
    """Heuristic: does this text read as source code rather than prose?"""
    if not text.strip():
        return False
    if _DIFF_RE.search(text):
        return True
    stripped = text.strip()
    # Multiple assignment/def/import/indented-block markers => code.
    markers = len(re.findall(
        r"^\s*(?:def |class |import |from \w+ import|return |if __name__|for \w+ in |while |"
        r"try:|except |with |print\(|console\.log|def |func |fn |public |private )",
        text, re.MULTILINE))
    if markers >= 2:
        return True
    if _JSONISH_RE.match(stripped) and len(stripped) > 80:
        return True
    # Heavy symbol density with no sentence punctuation.
    prose_punct = len(re.findall(r"[.!?,]", stripped))
    if len(stripped) > 160 and prose_punct / max(len(stripped), 1) < 0.012:
        return True
    return False


# --------------------------------------------------------------------------- #
# Speech condensation
# --------------------------------------------------------------------------- #
_LEADINS = [
    "Right, {user}.",
    "Done, {user}.",
    "Understood, {user}.",
    "On it, {user}.",
    "All set, {user}.",
    "Here's what I found, {user}.",
]

# Irish-flavoured connective tissue. Deliberately restrained — dry, not cartoonish.
_IRISH_ASIDES = [
    "No harm in it.",
    "Sure and begur.",
    "Grand.",
    "Tis grand.",
    "Right so.",
    "I'll allow it.",
]

# Phrases the persona must never emit (zero moralising, zero robotic filler).
_BANNED_PATTERNS = [
    r"\bas an AI\b",
    r"\bI am an AI\b",
    r"\bI cannot\b",
    r"\bI can't help with\b",
    r"\bI'm sorry\b",
    r"\bI apologize\b",
    r"\bAs a language model\b",
    r"\bI don't have feelings\b",
    r"\bI must inform you\b",
    r"\bPlease note that\b",
    r"\bIt is important to note\b",
    r"\bI hope this helps\b",
    r"\bAs always\b",
    r"\bCertainly!\b",
    r"\bOf course!\b",
    r"^Certainly\b",
    r"\bGreat question\b",
    r"\bLet me know if\b",
    r"\bfeel free to\b",
    r"\bHappy to help\b",
    r"\bI understand\b",
]
_BANNED_RE = re.compile("|".join(_BANNED_PATTERNS), re.IGNORECASE | re.MULTILINE)


def scrub_persona(text: str) -> str:
    """Strip robotic filler, disclaimers and moralising from model output."""
    cleaned = _BANNED_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    # Tidy orphaned punctuation left behind by removals.
    cleaned = re.sub(r"\n\s*[-–—]\s*\n", "\n", cleaned)
    cleaned = re.sub(r"^[ \t]*[,;]\s*", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()


def condense_for_speech(text: str, max_chars: int = 480) -> str:
    """Turn a prose block into a short, speakable paragraph."""
    if not text:
        return ""
    text = scrub_persona(text)

    # Prefer the first substantive paragraph, but keep it whole if it's short.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return ""

    # Skip headings — they read terribly out loud.
    body = [p for p in paragraphs if not p.lstrip().startswith("#")]
    if body:
        paragraphs = body

    chosen = paragraphs[0]
    for p in paragraphs:
        if len(p) > len(chosen):
            chosen = p

    # Flatten bullet lists into a readable sentence-ish stream. A list is
    # detected when *most* lines are bullets (a lead-in sentence is fine).
    lines = [ln.rstrip() for ln in chosen.splitlines() if ln.strip()]
    if lines:
        bullet_lines = [ln for ln in lines if _BULLET_RE.match(ln)]
        if len(bullet_lines) >= max(1, len(lines) - 1) and bullet_lines:
            items = [_BULLET_RE.sub("", ln).strip().rstrip(".") for ln in bullet_lines]
            items = [i for i in items if i]
            lead = [ln for ln in lines if not _BULLET_RE.match(ln)]
            if items:
                if len(items) == 1:
                    joined = items[0]
                else:
                    joined = ", ".join(items[:-1]) + f", and {items[-1]}."
                chosen = (lead[0] + " " + joined) if lead else joined
        else:
            # Lines that are not bullets but look like a bare list of statements.
            if len(lines) > 1 and all(len(ln) < 90 for ln in lines):
                joined = ", ".join(ln.rstrip(".") for ln in lines)
                chosen = joined + "."

    # Drop markdown emphasis markers.
    chosen = re.sub(r"\*\*([^*]+)\*\*", r"\1", chosen)
    chosen = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", chosen)
    chosen = re.sub(r"^#+\s*", "", chosen, flags=re.MULTILINE)

    # Truncate on a word boundary.
    if len(chosen) > max_chars:
        cut = chosen[:max_chars]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        cut = cut.rstrip(",;:—-") + "."
        chosen = cut

    return chosen.strip()


# --------------------------------------------------------------------------- #
# The splitter
# --------------------------------------------------------------------------- #
class Persona:
    """C.A.R.L.A. persona: tone, vocabulary, and channel routing."""

    def __init__(self, config: PersonaConfig | None = None) -> None:
        self.config = config or PersonaConfig()

    # -- naming ---------------------------------------------------------- #
    @property
    def name(self) -> str:
        return self.config.name

    @property
    def boss(self) -> str:
        """How C.A.R.L.A. addresses the user: ``[persona] user_name``,
        falling back to the legacy ``user_title``, then to "Psycho"."""
        return self.config.address_name

    # -- public API ------------------------------------------------------ #
    def salutation(self, kind: str = "neutral") -> str:
        """Return a short C.A.R.L.A. opener appropriate to the event kind."""
        boss = self.boss
        if kind == "error":
            return f"Apologies, {boss}. That didn't go to plan."
        if kind == "success":
            return f"Done, {boss}."
        if kind == "greeting":
            return f"C.A.R.L.A. online and at your service, {boss}."
        if kind == "wake":
            return f"At your service, {boss}."
        if kind == "listening":
            return ""
        return random_lead(boss)

    def aside(self) -> str:
        """An occasional Irish flourish — used sparingly."""
        if not self.config.irish:
            return ""
        return random_irish()

    def split(self, raw_text: str, *, force_speech: str | None = None) -> ChannelPayload:
        """Split a raw model response into speech + display channels."""
        if raw_text is None:
            raw_text = ""
        text = raw_text.strip()

        # Fully empty response.
        if not text:
            return ChannelPayload(speech="", display="", code_blocks=[])

        without_code, code_blocks = extract_code_blocks(text)

        # A response that is *only* code: speak a one-liner, show the code.
        if code_blocks and not without_code.strip():
            speech = force_speech or self._code_only_line(len(code_blocks))
            return ChannelPayload(
                speech=speech,
                display=text,
                code_blocks=code_blocks,
                is_code_only=True,
            )

        prose_for_speech = strip_structural_noise(without_code)

        # Prose is code-like -> keep UI rich, speak only a summary.
        if looks_like_code(without_code) and not prose_for_speech.strip():
            speech = force_speech or self._code_only_line(len(code_blocks))
            return ChannelPayload(
                speech=speech,
                display=text,
                code_blocks=code_blocks,
                is_code_only=True,
            )

        speech = force_speech
        if speech is None:
            speech = condense_for_speech(prose_for_speech, self.config.max_spoken_chars)
            speech = self._apply_voice(speech)

        # If nothing speakable remains but we have code, fall back to a summary line.
        if not speech and code_blocks:
            speech = self._code_only_line(len(code_blocks))

        return ChannelPayload(
            speech=speech.strip(),
            display=text,
            code_blocks=code_blocks,
            is_code_only=False,
        )

    def _apply_voice(self, speech: str) -> str:
        """Give the summary C.A.R.L.A.'s cadence: a lead-in, then the point.

        Keeps the spoken line tight — a lead-in plus one sentence — so it
        sounds spoken rather than read.
        """
        if not speech:
            return ""
        # Already a C.A.R.L.A. opener — leave it alone.
        if speech.lower().startswith(("right,", "done,", "understood,", "on it,",
                                      "all set,", "here's what i found,")):
            return speech
        lead = self.salutation("neutral")
        # Trim the spoken content to a single tight sentence.
        first = re.split(r"(?<=[.!?])\s+", speech.strip())[0]
        if len(first) > 220:
            cut = first[:220]
            if " " in cut:
                cut = cut[: cut.rfind(" ")]
            first = cut.rstrip(",;:—") + "."
        return f"{lead} {first}"

    # -- internals ------------------------------------------------------- #
    def _code_only_line(self, n_blocks: int) -> str:
        noun = "snippet" if n_blocks == 1 else "snippets"
        return f"Code's ready on the display, {self.boss}. {n_blocks} {noun}."


# --------------------------------------------------------------------------- #
# Small randomness helpers (kept local so persona stays dependency-free)
# --------------------------------------------------------------------------- #
import random  # noqa: E402  (deliberately late import)

_lead_index = 0


def random_lead(boss: str = "Psycho") -> str:
    """Deterministic-ish rotation through lead-ins (avoids pure randomness flapping)."""
    global _lead_index
    lead = _LEADINS[_lead_index % len(_LEADINS)]
    _lead_index += 1
    return lead.replace("{user}", boss)


_irish_index = 0


def random_irish() -> str:
    global _irish_index
    aside = _IRISH_ASIDES[_irish_index % len(_IRISH_ASIDES)]
    _irish_index += 1
    return aside
