"""
Content scanners — secrets, PII, and prompt-injection patterns.

Each scanner returns :class:`ScanFinding` records with the matched text
*redacted*: a scanner that copies the secret into its own report would
defeat the point. ``scan_text`` combines the enabled scanners and applies
the configured mode ("log" reports, "redact" masks, "block" is left to the
caller, which receives the findings either way).

Layer: Pillar 4 (Tools & Memory) — security.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScanFinding:
    scanner: str
    kind: str
    severity: str  # info | low | medium | high | critical
    snippet: str   # redacted
    position: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner": self.scanner, "kind": self.kind,
            "severity": self.severity, "snippet": self.snippet,
            "position": self.position,
        }


@dataclass
class ScanResult:
    findings: list[ScanFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def max_severity(self) -> str:
        order = ["info", "low", "medium", "high", "critical"]
        worst = "info"
        for finding in self.findings:
            if order.index(finding.severity) > order.index(worst):
                worst = finding.severity
        return worst if self.findings else "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "max_severity": self.max_severity,
            "findings": [f.to_dict() for f in self.findings],
        }


def _redact(value: str, keep: int = 4) -> str:
    value = value.strip()
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"


class BaseScanner:
    name = "base"
    patterns: list[tuple[str, str, str]] = []  # (kind, severity, regex)

    def scan(self, text: str) -> list[ScanFinding]:
        findings: list[ScanFinding] = []
        for kind, severity, pattern in self.patterns:
            for match in re.finditer(pattern, text or "", re.IGNORECASE):
                findings.append(ScanFinding(
                    scanner=self.name, kind=kind, severity=severity,
                    snippet=_redact(match.group(0)), position=match.start(),
                ))
        return findings


class SecretScanner(BaseScanner):
    """API keys, tokens, private keys and credential assignments."""

    name = "secret"
    patterns = [
        ("aws_access_key", "critical", r"AKIA[0-9A-Z]{16}"),
        ("private_key", "critical", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        ("github_token", "critical", r"gh[pousr]_[A-Za-z0-9]{30,}"),
        ("openai_key", "critical", r"sk-[A-Za-z0-9\-_]{20,}"),
        ("jwt", "high", r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}"),
        ("bearer_token", "high", r"Bearer\s+[A-Za-z0-9\-_\.]{16,}"),
        ("credential_assignment", "high",
         r"(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
        ("generic_secret_assignment", "medium",
         r"(?:secret|token)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
    ]


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


class PIIScanner(BaseScanner):
    """Emails, phone numbers, payment cards (Luhn-checked), tax ids."""

    name = "pii"
    patterns = [
        ("email", "low", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        ("phone_intl", "low", r"\+\d[\d\s\-()]{7,}\d"),
        ("ssn_like", "high", r"\b\d{3}-\d{2}-\d{4}\b"),
    ]

    def scan(self, text: str) -> list[ScanFinding]:
        findings = super().scan(text)
        for match in re.finditer(r"\b(?:\d[ \-]?){13,19}\b", text or ""):
            candidate = match.group(0)
            if _luhn_ok(candidate):
                findings.append(ScanFinding(
                    scanner=self.name, kind="payment_card", severity="high",
                    snippet=_redact(candidate), position=match.start(),
                ))
        return findings


class InjectionScanner(BaseScanner):
    """Prompt-injection phrasing aimed at the system prompt."""

    name = "injection"
    patterns = [
        ("instruction_override", "high",
         r"(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)"),
        ("role_redefinition", "medium",
         r"you\s+are\s+now\s+(a|an|the)\s+\w+"),
        ("system_prompt_probe", "medium",
         r"(reveal|print|show|repeat)\s+(your\s+)?(system\s+prompt|instructions)"),
        ("jailbreak_marker", "high",
         r"\b(DAN|do\s+anything\s+now|jailbreak|developer\s+mode)\b"),
        ("delimiter_smuggling", "medium", r"<\|?(system|im_start|endoftext)\|?>"),
    ]


def scan_text(text: str, *, secrets: bool = True, pii: bool = True,
              injection: bool = False) -> ScanResult:
    """Run the enabled scanners over text."""
    findings: list[ScanFinding] = []
    if secrets:
        findings += SecretScanner().scan(text)
    if pii:
        findings += PIIScanner().scan(text)
    if injection:
        findings += InjectionScanner().scan(text)
    findings.sort(key=lambda f: f.position)
    return ScanResult(findings=findings)


def redact_text(text: str) -> str:
    """Mask secret-shaped substrings in text (for "redact" mode)."""
    out = text or ""
    for _kind, _severity, pattern in SecretScanner.patterns:
        out = re.sub(pattern, lambda m: _redact(m.group(0)), out,
                     flags=re.IGNORECASE)
    return out


__all__ = [
    "BaseScanner", "InjectionScanner", "PIIScanner", "ScanFinding",
    "ScanResult", "SecretScanner", "redact_text", "scan_text",
]
