"""Response-language resolution — the single source of truth for the language
the plugin answers in.

The plugin used to hardcode an Argentine-Spanish closing sentence into the ask
note ("Estoy en modo Ask, solo puedo responder..."). This module localizes that
contract instead: the language is resolved through an ordered ladder, and every
model-facing sentence is built from a catalogue that never falls off — an
unknown language degrades to English, mirroring the desktop SDK's own
plugin-i18n resolution (active locale → en → raw key).

Resolution ladder (first hit wins):

1. ``app_locale`` — the desktop half reports the app's active display language
   (``display.language``) with every mode stage; the API half persists it in
   the shared store, and the note builder passes it here per turn. This is the
   only signal that follows the user's own choice.
2. ``HERMES_COMPOSER_MODES_LANG`` — explicit user override.
3. OS locale — ``LANGUAGE`` / ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG`` on
   POSIX, or ``locale.getlocale()`` as the last system probe. This is what
   answers for CLI/TUI surfaces with no desktop half.
4. English — the safe fallback when nothing above is recognized.

Adding a language means one catalogue entry (see docs/localization.md); no
call site changes. Protocol tokens (``::plan-approve`` etc.) are NEVER
translated — they are parsed by the desktop half.
"""

from __future__ import annotations

import locale as _locale_mod
import os

__all__ = [
    "SUPPORTED_LANGS",
    "DEFAULT_LANG",
    "normalize_lang",
    "system_lang",
    "resolve_lang",
    "answer_language_clause",
    "ask_closing_sentence",
    "mode_message",
]

#: Languages the catalogue ships. Keep in sync with docs/localization.md.
SUPPORTED_LANGS = ("en", "ru", "es")
DEFAULT_LANG = "en"

#: Explicit language codes. Anything not listed falls through to English — a
#: wrong-but-plausible locale must never force a language the user cannot read.
_ALIASES: dict[str, str] = {
    "en": "en",  # english
    "ru": "ru",  # russian
    "es": "es",  # spanish (es-AR / es-419 / es-MX map here via prefix)
}

# ── catalogue (the ONLY place these sentences live) ──────────────────────────

#: Appended to every non-agent note: which language the model answers in. The
#: user's own typed language always wins over this — the clause says so.
_ANSWER_CLAUSE = {
    "en": "Answer the user in English unless the user is clearly writing in another language, in which case match theirs.",
    "ru": "Отвечай пользователю по-русски, если только пользователь явно не пишет на другом языке — тогда отвечай на его языке.",
    "es": "Respondé al usuario en español, salvo que escriba claramente en otro idioma: en ese caso, respondé en su idioma.",
}

#: The mandated ask-mode closing line. {actions} stands for the A/B/C list the
#: model adapts to the refused action(s).
_ASK_CLOSING = {
    "en": "I am in Ask mode and can only answer. If you want me to proceed with {actions}, ask me in Agent mode.",
    "ru": "Я в режиме Ask и могу только отвечать. Если нужно, чтобы я {actions}, попроси меня об этом в режиме Agent.",
    "es": "Estoy en modo Ask, solo puedo responder. Si querés que proceda a {actions}, tenés que pedírmelo en modo Agent.",
}

#: The short verb-phrase the model substitutes for A/B/C in each language.
_ASK_ACTIONS = {
    "en": "A/B/C",
    "ru": "выполнить A/B/C",
    "es": "A/B/C",
}

#: The ``/mode`` command's own replies (the plugin speaking to the user).
_MODE_MESSAGES = {
    "list": {
        "en": "Composer modes: {ids} — default is '{default}'. Use /mode <id> to change it. In the desktop app the mode button sets it per session.",
        "ru": "Режимы композера: {ids} — по умолчанию «{default}». Смените /mode <id>. В desktop-приложении режим выбирается кнопкой в композере (для каждой сессии своей).",
        "es": "Modos del composer: {ids} — el default es '{default}'. Cambialo con /mode <id>. En la app de desktop el botón del composer fija el modo por sesión.",
    },
    "unknown": {
        "en": "Unknown mode '{arg}'. Known modes: {ids}.",
        "ru": "Неизвестный режим «{arg}». Доступные режимы: {ids}.",
        "es": "Modo desconocido '{arg}'. Modos conocidos: {ids}.",
    },
    "set": {
        "en": "Default composer mode set to '{label}' ({arg}). Sessions with their own mode keep it — change that in the desktop composer.",
        "ru": "Режим по умолчанию: «{label}» ({arg}). Сессии со своим режимом сохранят его — меняется кнопкой в desktop-композере.",
        "es": "Modo default del composer: '{label}' ({arg}). Las sesiones con modo propio lo conservan — se cambia con el botón del composer en desktop.",
    },
}


def normalize_lang(value: object) -> str | None:
    """Map any locale-ish string ('ru-RU', 'es-419', 'zh_HK.UTF-8', 'AGENT')
    to a supported language, or ``None`` when unrecognized."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("_", "-")
    if not text:
        return None
    base = text.split("@", 1)[0].split("-", 1)[0]
    return _ALIASES.get(base)


def system_lang() -> str:
    """Best-effort OS locale → supported language, English when unknown.
    Never raises."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        hit = normalize_lang(os.environ.get(var, ""))
        if hit:
            return hit
    try:
        hit = normalize_lang(_locale_mod.getlocale()[0] or "")
        if hit:
            return hit
    except Exception:
        pass
    return DEFAULT_LANG


def resolve_lang(app_locale: object = "") -> str:
    """The ladder: app locale → env override → OS locale → en."""
    hit = normalize_lang(app_locale)
    if hit:
        return hit
    hit = normalize_lang(os.environ.get("HERMES_COMPOSER_MODES_LANG", ""))
    if hit:
        return hit
    return system_lang()


def answer_language_clause(lang: str | None = None) -> str:
    lang = lang if lang in _ANSWER_CLAUSE else resolve_lang()
    return _ANSWER_CLAUSE[lang]


def ask_closing_sentence(lang: str | None = None) -> str:
    lang = lang if lang in _ASK_CLOSING else resolve_lang()
    return _ASK_CLOSING[lang].format(actions=_ASK_ACTIONS[lang])


def mode_message(key: str, lang: str | None = None, **fields: str) -> str:
    """A ``/mode`` reply template, formatted with *fields*."""
    lang = lang if lang in _ANSWER_CLAUSE else resolve_lang()
    table = _MODE_MESSAGES.get(key)
    if not table:
        raise KeyError(key)
    return table[lang].format(**fields)
