# Audit — `[persona] user_name` setting

**Setting:** `user_name` in `[persona]` — what C.A.R.L.A. calls the user.
Blank (the default) means she calls you **Psycho**.
**Date:** 2026-11-13
**Verdict: PASS, with one defect found and fixed during the audit.**

---

## 1. Definition (verified in source)

`PersonaConfig` (`core/config.py`):

```python
user_name: str = ""     # what C.A.R.L.A. calls the user
user_title: str = ""    # legacy alias, kept for older configs

@property
def address_name(self) -> str:
    return (self.user_name or self.user_title or "Psycho").strip() or "Psycho"
```

`Persona.boss` returns `address_name`; every consumer goes through it.
The generated config template exposes `user_name = ""` with explanatory
comments and contains no `"Boss"`.

## 2. Behaviour matrix (fresh unit-level runs)

| Case | Result |
|---|---|
| Blank default | `Psycho` ✅ |
| `user_name = "Alex"` | `Alex` ✅ |
| Legacy `user_title = "Chief"` only | `Chief` ✅ |
| Both set | `user_name` wins (`Alex`) ✅ |
| Whitespace-only name | falls back to `Psycho` ✅ |
| Padded name `"  Sam  "` | trimmed to `Sam` ✅ |
| `config_from_dict` (new / legacy / empty) | `Riley` / `Chief` / `Psycho` ✅ |
| TOML dump → load round-trip with `user_name = "Jo"` | preserved ✅ |

## 3. Surfaces wired (source-checked)

| Surface | Mechanism | Status |
|---|---|---|
| Persona salutations / lead-ins | `Persona.boss` → `address_name` | ✅ |
| Agent system prompts | `self.persona.boss` | ✅ |
| Channel bridge replies + `/help` | `ChannelBridge._user` from runtime persona | ✅ |
| CLI chat prompt / memory / cron messages | `persona.boss` / `rt.persona.boss` | ✅ |
| Runtime error speech | `self.persona.boss` | ✅ |
| Runtime status API | `status()` includes `user_name` | ✅ |
| HUD (JS) | `USER_NAME` refreshed from `/api/status`; toasts, message role, avatar initial | ✅ after fix (§5) |
| Offline fallback (direct + via router) | adapter `persona` option ← `config.persona.address_name` | ✅ |
| Scheduler agent | `self.persona.boss` | ✅ |
| Digest honorific default | `Psycho` | ✅ |

Hardcoded-`Boss` sweep of `src/` (`.py`, `.js`, `.html`): **0 hits.**
(The persona lead-in templates now use a `{user}` placeholder instead of
the literal word.)

## 4. Live verification (fresh servers)

| Scenario | Evidence |
|---|---|
| Default home (`carla init`, blank `user_name`) | `/api/status` → `C.A.R.L.A. / Psycho`; `/help` → “at your service, Psycho.”; e2e **46/46** |
| Custom name (`user_name = "Alex"`) | `/api/status` → `Alex`; chat → “Hello, Alex.” (speech + display); e2e **46/46** (previous run) |
| Legacy config (`user_title = "Chief"`, `user_name` blank) | `/api/status` → `Chief`; `/help` → “at your service, Chief.”; chat → “Hello, Chief.” |

## 5. Defect found and fixed during this audit

**HUD never refreshed `USER_NAME` from `/api/status`.** `applySnapshot`
was missing `if (snap.user_name) USER_NAME = snap.user_name;` (lost in an
earlier edit). Effect: with a custom name, server-side replies used it but
HUD-generated toasts/labels would have kept saying “Psycho.”
**Fixed** in `server/web/static/carla.js`; `node --check` passes, and the
refresh line is verified in place.

## 6. Regression

| Check | Result |
|---|---|
| `pytest tests/` | **403 collected, exit code 0** |
| `tests/test_user_name.py` (12 tests) | all pass |
| Old tests asserting the previous “Boss” default | updated to the new default; suite green |
| e2e smoke (default home) | **46/46** |

## 7. Notes

- The workspace’s own saved config (`.psycho_carla/psycho_carla.toml`)
  had its legacy `user_title = "Boss"` replaced with blank `user_name`,
  so it now resolves to **Psycho** as intended.
- Names stored as ordinary data (e.g. a memory “Boss prefers dark mode”,
  test fixtures naming a person “Boss”) are untouched — they are content,
  not the address setting.

**Conclusion:** the setting is defined correctly, resolves
`user_name` → legacy `user_title` → “Psycho” exactly as specified, and is
honoured on every user-facing surface. The one defect found (HUD refresh)
is fixed and verified.
