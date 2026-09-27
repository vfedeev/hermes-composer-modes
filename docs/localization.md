# Localization — writing in the system language, with zero hardcodes

This document is the contract for the fork's language behavior and the guide
for maintaining (or upstreaming) it. It answers two questions:

1. **How does the plugin decide which language to answer in?**
2. **How do you add a language without touching any call site?**

The bug this fixes: the plugin shipped an Argentine-Spanish closing sentence
hardcoded into the ask-mode note ("Estoy en modo Ask, solo puedo responder…")
and Spanish UI strings throughout the desktop half. On any machine — Russian,
English, whatever — the model answered in Spanish and the cards spoke Spanish.
Now: **the plugin answers in the language of the system/app it runs on, and
falls back to English when that language cannot be determined.**

There are exactly two kinds of text in this plugin, and the rule for each is
different. Read this before adding anything.

---

## 1. Two text channels — never mix them

| Channel | Audience | Language rule | Where |
|---|---|---|---|
| **Protocol wording** | parsers (the desktop half, the core) | **Always English, never translated** | directive tokens (`::plan-approve{file="…"}`, `::plan-questions`, `::debug-loop{round="N"}`), `.hermes/plans/` paths, note tags (`[mode:ask]`), tool names |
| **Voice** | humans and the model-as-speaker | **Localized via the ladder below** | UI labels/copy (`desktop/plugin.js`), the ask closing sentence, the answer-language clause, `/mode` replies |

Translating a token of the first kind silently breaks the feature (the card
regex won't match it). A test pins every directive token in `modes.py`
(`test_plan_note_carries_the_approval_and_questions_directives`,
`test_debug_note_carries_the_loop_directive`) — keep them passing.

## 2. The language ladder (single source: `i18n.py`)

`resolve_lang(app_locale)` picks the first rung that yields a *supported*
language. Unknown or malformed values skip to the next rung; the last rung
always answers. Nothing ever raises.

1. **App locale** — the desktop app's active display language
   (`Settings → display.language`). The desktop half reads it
   (`useI18n().locale`, hydrated from `navigator.language`) and reports it to
   the backend with every mode stage (`POST /mode {…, locale}`) and on its own
   (`POST /locale`). The API half persists it (`store.py`, `state.json →
   "locale"`), so a restarted backend keeps answering in the user's language
   until the app re-syncs. This rung is what "language of the system" means
   when a Hermes UI is present — it follows the *user's choice*, not a guess.
2. **`HERMES_COMPOSER_MODES_LANG`** — explicit override for CLI/TUI/messaging
   surfaces or for forcing a language on a desktop install (values like `ru`,
   `en`, `es-419` — anything `normalize_lang` recognizes; unknown values are
   ignored, not fatal).
3. **OS locale** — `LANGUAGE` → `LC_ALL` → `LC_MESSAGES` → `LANG`, then
   `locale.getlocale()`. This is the answer for a headless backend on a
   Russian-locale Linux box with no desktop half connected.
4. **English** — the floor. When nothing above is recognized, every user- and
   model-facing sentence is English. **Never a fallback to any other specific
   language.**

Normalization (`normalize_lang`): lowercase, `_`→`-`, strip `@modifier` and
region (`es-AR.UTF-8` → `es`), then lookup in `_ALIASES`. Only languages with
a complete catalogue entry are supported; everything else → `None` → next rung.

## 3. Where localized text lives (and only lives)

- **Model-facing sentences**: `i18n.py` — `_ANSWER_CLAUSE` (which language the
  model should answer in; the user's own typed language always wins, the
  clause says so), `_ASK_CLOSING` + `_ASK_ACTIONS` (the mandated ask-mode
  closing sentence), `_MODE_MESSAGES` (the `/mode` command's own replies).
  `modes.py` composes the three notes as **factories evaluated per call**
  (`_ask_note(app_locale)` …): a locale switch takes effect on the next turn
  with no reload. Do not cache a note in a module constant; the legacy
  `ASK_NOTE`/`PLAN_NOTE`/`DEBUG_NOTE` names still work via PEP-562
  `__getattr__` and re-resolve on every access.
- **UI copy**: `desktop/plugin.js` — the `STR = { en: …, ru: …, es: … }`
  catalogue is the only place these strings exist. Components translate with
  `usePluginI18n(ID)` (re-renders on a locale switch), handlers and module
  functions with `T(...)` (`ctx.i18n.t`, hydrated in `register`; before
  hydration it resolves against `navigator.language`). The registration mirrors
  the locale to the backend (`stageLocale`) so the model-facing half follows
  the same language as the UI half.
- **Both i18n entry points (`usePluginI18n`, `useI18n`) are taken off the SDK
  namespace, not as bare named imports** — on a shell predating them
  (`requires_hermes` is `>=0.21.3`, the exports landed in 0.21.4) they degrade
  to the English bundle instead of failing the plugin load.

## 4. Adding a language — checklist

Say you want German (`de`):

1. `i18n.py`: add `"de": "de"` to `_ALIASES`; add one entry for **every key**
   in `_ANSWER_CLAUSE`, `_ASK_CLOSING`, `_ASK_ACTIONS`, and every table in
   `_MODE_MESSAGES`. Do not copy sentences blindly — a native speaker writes
   them; the EN entry is the semantic reference.
2. `desktop/plugin.js`: add a `de: { … }` bundle to `STR` with **every key** of
   the `en` bundle. Missing keys are *allowed* by the SDK (it falls back to
   `en`) but forbidden by style: a half-translated UI is worse than a fully
   English one. Keep `SUPPORTED_LANGS` (`i18n.py`) and the `STR` bundle list
   in sync by hand — `test_every_catalogue_language_has_every_entry` pins the
   Python side (every supported language has every catalogue entry); the JS
   side is pinned by smoke only, so diff the bundle key counts when adding a
   language.
3. `SUPPORTED_LANGS`: append `"de"`.
4. Run: `python -m pytest -c tests/pytest.ini` + `node scripts/smoke_desktop_half.mjs`.
5. Update this file's table below.

Anti-checklist — **never**:
- add a conditional that hardcodes a language string at a call site
  (`lang === 'ru' ? 'Открыть' : …`);
- translate protocol tokens (see §1);
- make the fallback anything but English — a misdetected locale must degrade
  to the lingua franca, not to a plausible but wrong language;
- let `resolve_lang` raise. Unknown input must silently consume the next rung.

## 5. Behavior matrix (what each user sees)

| Setup | Mode note + closing sentence | UI copy |
|---|---|---|
| Desktop, app language `ru` | Russian (via `/mode` stage locale) | Russian |
| Desktop, app language `th` (unsupported) | English | English (SDK falls back to the `en` bundle) |
| Desktop, app language `es-AR` | Spanish (Rioplatense) | Spanish |
| CLI/TUI only, OS locale `ru_RU.UTF-8` | Russian (rung 3) | n/a |
| Headless server, no locale, `HERMES_COMPOSER_MODES_LANG=ru` | Russian (rung 2 wins OS) | n/a |
| Everything unset / garbage | English | English |

## 6. Forcing Russian regardless of system (the "minimum task" option)

You can still pin a language without patching strings — that's what rung 2 is
for: set `HERMES_COMPOSER_MODES_LANG=ru` in the Hermes process environment.
Every note and reply resolves through it, UI follows the app locale. A Russian
user who wants the UI pinned too sets the app's `display.language` to Русский
in Settings (rung 1). No code edit needed — this is why the fork did not simply
hardcode Russian: it would have reintroduced the exact bug at a different
language.

## 7. Patching guidance for an upstream PR

The author's original intent — one mandated closing sentence, verbatim, with
only the A/B/C list adapted — is preserved *per language*. The minimal
upstreamable diff is: `i18n.py` (new), the note factories in `modes.py`,
`locale` in `store.py`/`plugin_api.py`, the `STR` catalogue + i18n wiring in
`desktop/plugin.js`, and the test updates. Behavior at EN defaults is the
author's original protocol; at `es-AR` it is bit-for-bit the old experience.
The one visible change for Spanish users: `MARK as fixed`-style button labels
are now catalogued, so the desktop card stays consistent when the app language
changes mid-session (before, it could not change at all).
