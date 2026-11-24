---
name: status-report
description: Produce a concise engineering status report covering shipped work, current risks, and next actions. Use when asked for a status update, a weekly summary, or a briefing for a team or stakeholder.
license: MIT
metadata:
  author: psycho-carla
  version: "1.0"
---

# Status Report

## When to use this skill

- The user asks for a status update, weekly summary, or briefing.
- A stakeholder needs to know what shipped, what is blocked, and what is next.

## Procedure

1. **Gather shipped work.** Pull recent commits and merged changes. Prefer the
   repository's own history over recollection.
2. **Identify risks.** Look for failing tests, open incidents, and work that
   slipped its target date. Name each risk with its owner and impact.
3. **Define next actions.** Every risk gets a concrete next action. An action
   without an owner is not an action.
4. **Write it in three sections.** Shipped, At risk, Next. Keep each section
   to three bullets or fewer.

## Output format

```
Shipped
- <what shipped> — <impact>

At risk
- <risk> — <owner> — <impact if unresolved>

Next
- <action> — <owner> — <when>
```

## Common edge cases

- If nothing shipped, say so plainly. Do not pad the section.
- If a risk has no owner, write "unowned" rather than omitting it — an
  unowned risk is the most important thing in the report.
- Do not state a metric you did not measure.

## Reference

See [the reference guide](references/FORMAT.md) for tone and length guidance.
