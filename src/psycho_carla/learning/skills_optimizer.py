"""
PSYCHO-C.A.R.L.A. — Skill optimiser.

Upstream ships ``jarvis optimize skills --policy dspy``. This is the
local, deterministic equivalent: it analyses the installed skills
catalog and proposes better **descriptions**.

Why descriptions? Because in the `agentskills.io` progressive-disclosure
model the description is the *only* thing the agent sees before it
decides whether to load a skill. A vague description means a skill
never fires; an over-long one burns context. This optimiser therefore
focuses on the trigger text.

Two policies are supported:

``heuristic``
    Rule-based. Flags descriptions that are too short, too long, missing
    trigger vocabulary ("use when..."), or that lack concrete keywords.

``dspy``
    The same rule engine, but scored and ranked with a weighted
    objective, and the ranking is what drives the "which skills need
    attention first" ordering. No network calls, no model required —
    the point of the local-first design.

Layer: Pillar 5 (Learning).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Words that signal a skill's trigger conditions.
TRIGGER_WORDS = (
    "use when",
    "use this",
    "when the user",
    "when asked",
    "call this",
    "invoke",
    "for when",
    "use it",
)

# Words that make a description concrete rather than generic.
CONCRETE_HINTS = (
    "file", "files", "directory", "code", "test", "tests", "deploy",
    "deployment", "report", "reports", "email", "message", "schedule",
    "data", "database", "api", "web", "http", "url", "search", "git",
    "commit", "pull request", "pr", "bug", "error", "log", "logs",
    "markdown", "json", "csv", "yaml", "toml", "python", "javascript",
    "review", "debug", "refactor", "install", "configure", "monitor",
)

# Generic descriptions that never fire.
VAGUE_WORDS = (
    "helps with", "helps you", "does stuff", "general purpose",
    "useful for many", "various tasks", "miscellaneous", "general helper",
)


@dataclass
class SkillIssue:
    """One problem found in a skill description."""

    skill: str
    severity: str = "info"  # info | warning | error
    message: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class SkillOptimizationReport:
    """The full outcome of a skill-catalogue optimisation pass."""

    policy: str = "dspy"
    scanned: int = 0
    issues: list[SkillIssue] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    ranking: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> list[str]:
        return [name for name in self.ranking if name not in
                {i.skill for i in self.issues if i.severity in ("warning", "error")}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "scanned": self.scanned,
            "issues": [i.to_dict() for i in self.issues],
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "ranking": list(self.ranking),
            "healthy": self.healthy,
        }


class SkillOptimizer:
    """Analyses skill descriptions and proposes improvements."""

    def __init__(self, catalog: Any = None) -> None:
        self.catalog = catalog

    def optimize(self, policy: str = "dspy") -> SkillOptimizationReport:
        """Run the optimiser over the catalog."""
        policy = (policy or "dspy").lower()
        report = SkillOptimizationReport(policy=policy)
        if self.catalog is None:
            return report

        skills = list(self.catalog)
        report.scanned = len(skills)

        for skill in skills:
            issues = self._inspect(skill)
            report.issues.extend(issues)
            report.scores[skill.name] = self._score(skill, issues, policy)

        # Worst first — those are the ones worth fixing.
        report.ranking = [
            name for name, _ in sorted(
                report.scores.items(), key=lambda kv: kv[1]
            )
        ]
        return report

    # -- internals -------------------------------------------------------- #
    def _inspect(self, skill: Any) -> list[SkillIssue]:
        issues: list[SkillIssue] = []
        name = skill.name
        desc = (skill.description or "").strip()
        low = desc.lower()

        if not desc:
            issues.append(
                SkillIssue(
                    name, "error",
                    "description is empty — the agent will never activate it",
                    "Describe what the skill does and when to use it.",
                )
            )
            return issues

        if len(desc) < 40:
            issues.append(
                SkillIssue(
                    name, "warning",
                    f"description is only {len(desc)} characters — too vague "
                    "to match reliably",
                    "Add concrete trigger vocabulary and say when to use it.",
                )
            )
        if len(desc) > 400:
            issues.append(
                SkillIssue(
                    name, "warning",
                    f"description is {len(desc)} characters — it costs context "
                    "on every request",
                    "Move detail into the SKILL.md body and keep the "
                    "description to the trigger.",
                )
            )
        if not any(word in low for word in TRIGGER_WORDS):
            issues.append(
                SkillIssue(
                    name, "warning",
                    "description has no trigger clause (e.g. 'Use when...')",
                    "Add 'Use when ...' so the agent knows when to load it.",
                )
            )
        if any(word in low for word in VAGUE_WORDS):
            issues.append(
                SkillIssue(
                    name, "warning",
                    "description uses generic filler that matches everything",
                    "Replace with the specific nouns this skill handles.",
                )
            )
        if not any(word in low for word in CONCRETE_HINTS):
            issues.append(
                SkillIssue(
                    name, "info",
                    "description has no concrete domain keywords",
                    "Name the file types, tools or subjects this skill covers.",
                )
            )
        if skill.errors:
            issues.append(
                SkillIssue(
                    name, "error",
                    "skill does not conform to the agentskills.io spec: "
                    + "; ".join(skill.errors),
                    "Fix the frontmatter so the skill loads correctly.",
                )
            )
        body_lines = len((skill.body or "").splitlines())
        if body_lines > 500:
            issues.append(
                SkillIssue(
                    name, "warning",
                    f"SKILL.md body is {body_lines} lines — the spec "
                    "recommends under 500",
                    "Move long material into references/ and link to it.",
                )
            )
        return issues

    def _score(self, skill: Any, issues: list[SkillIssue], policy: str) -> float:
        """Score a skill's description quality, 0 (bad) to 1 (good)."""
        desc = (skill.description or "").strip()
        if not desc:
            return 0.0
        score = 1.0
        for issue in issues:
            if issue.severity == "error":
                score -= 0.5
            elif issue.severity == "warning":
                score -= 0.15
            else:
                score -= 0.05
        # Reward concrete trigger vocabulary.
        low = desc.lower()
        concrete = sum(1 for word in CONCRETE_HINTS if word in low)
        score += min(0.15, concrete * 0.03)
        # Reward a stated trigger condition.
        if any(word in low for word in TRIGGER_WORDS):
            score += 0.1
        # A description in the 80–300 character band is the sweet spot.
        if 80 <= len(desc) <= 300:
            score += 0.05
        if policy == "dspy":
            # The dspy policy weights trigger coverage most heavily.
            trigger_hits = sum(1 for word in TRIGGER_WORDS if word in low)
            score += min(0.1, trigger_hits * 0.05)
        return max(0.0, min(1.0, score))
