# Name-Change Audit — PSYCHO-C.A.R.L.A.

**New name:** PSYCHO-C.A.R.L.A.
**Expansion:** **C**oding, **A**utomated **R**easoning, & **L**anguage **A**ssistant
**Date:** 2026-11-13
**Verdict: PASS** — the rename is complete across the codebase and nothing regressed.

---

## 1. Residual old-name sweep

Case-insensitive search for `friday` across `src/`, `tests/`, `examples/`,
`tools/`, `docs/`, `README.md`, `pyproject.toml`, `install.ps1`,
`PsychoCARLA.spec`, `hud-preview.html` returned **exactly 1 hit**:

| Location | Content | Disposition |
|---|---|---|
| `examples/skills/status-report/references/FORMAT.md:21` | “Last Tuesday” is ambiguous by Friday. | **Legitimate weekday reference.** Not branding. Preserved deliberately. |

Additional findings during this audit (both fixed):

1. `server/web/templates/voice.html` still asked “does it sound like
   F.R.I.D.A.Y?” — a variant the rename pass missed (no trailing period).
   **Fixed** to C.A.R.L.A.
2. The sandbox still had the old `psycho-friday` distribution installed,
   leaving stale `friday`/`friday-psycho` console scripts pointing at the
   deleted package. **Uninstalled**; only `carla`, `psycho-carla`, and
   `carla-app` scripts remain. (Sandbox-only artefact; a fresh Windows
   install never had the old distribution.)

Out-of-scope residuals, noted for completeness:

- `voice/stt.py` still defaults the openWakeWord **model file** to
  `hey_jarvis` — that is the third-party library’s pretrained model name.
  The configured wake *phrase* is “hey carla”.
- Two docstrings cite upstream’s historical `jarvis …` CLI commands as
  provenance notes. These describe upstream, not this product.

## 2. New identity in place (135 occurrences of `PSYCHO-C.A.R.L.A.`)

| Surface | Verified |
|---|---|
| Package | `src/psycho_carla/`; `import psycho_friday` correctly fails |
| Public classes | `Carla`, `CarlaConfig`, `CarlaRuntime` import cleanly |
| App constants | `APP_NAME = "PSYCHO-C.A.R.L.A."`, `APP_SLUG = "psycho_carla"`, `CONFIG_FILENAME = "psycho_carla.toml"` |
| Distribution | `pip show psycho-carla` → Summary carries the full expansion |
| Entry points | `carla`, `psycho-carla`, `carla-app` |
| Config/state | `~/.psycho_carla/`, `%LOCALAPPDATA%\PsychoCarla`, `PSYCHO_CARLA_HOME` honoured; persona `name = "C.A.R.L.A."` in generated config |
| HUD/API | `/` serves C.A.R.L.A. branding; `/api/status` → `"app": "PSYCHO-C.A.R.L.A."` |
| Packaging | `PsychoCARLA.spec` (`PsychoCARLA.exe`, `CARLA_BUILD_VOICE`); `install.ps1` uses `[CARLA]` steps and `PsychoCarla` state dir |
| Expansion text | README title block and `pyproject.toml` description |
| Local state migration | workspace `.psycho_friday/` → `.psycho_carla/` (config and skill-source registry renamed) |

## 3. Regression verification (all fresh runs)

| Check | Result |
|---|---|
| `pytest tests/` | **391 collected, exit code 0** |
| `python -m compileall src/psycho_carla` | exit code 0 |
| `carla init` (fresh `PSYCHO_CARLA_HOME`) | config written, suggests `carla doctor` |
| `carla doctor` | “══ Ready ══” |
| `carla serve` cold start + `tests/e2e_smoke.py` | **46/46 checks passed** |
| `carla --help` | 44 commands under the new name |

## 4. Notes

- API/MCP surface renamed consistently: response extension key `carla`,
  MCP tools `carla_ask` / `carla_memory_search`, metric
  `carla_requests_total` — e2e checks covering these pass.
- Static HUD assets renamed: `carla.js`, `carla.css`.
- Historical parity audits under `docs/` were updated to the new product
  name along with everything else.

**Conclusion:** the rename to PSYCHO-C.A.R.L.A. (Coding, Automated
Reasoning, & Language Assistant) is complete, and the product passes its
full test and end-to-end suites unchanged under the new identity.
