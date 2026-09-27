"""Tests for the i18n ladder — the language the plugin answers in.

The bug class this covers: an Argentine-Spanish closing sentence was hardcoded
into the ask note, so the model drifted into Spanish on any machine. These
tests pin the ladder (app locale → env → OS locale → en) and the fallback.
"""
from __future__ import annotations

import pytest

import i18n


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG", "HERMES_COMPOSER_MODES_LANG"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ru", "ru"),
        ("ru-RU", "ru"),
        ("ru_RU.UTF-8", "ru"),
        ("es-AR", "es"),
        ("es-419", "es"),
        ("en-US", "en"),
        ("EN", "en"),
        ("  es_mx  ", "es"),
        # unsupported-but-real locales must NOT force a language
        ("de-DE", None),
        ("th", None),
        ("", None),
        ("banana", None),
        (None, None),
        (7, None),
    ],
)
def test_normalize_lang(raw, expected):
    assert i18n.normalize_lang(raw) == expected


def test_resolve_prefers_the_app_locale(monkeypatch):
    monkeypatch.setenv("HERMES_COMPOSER_MODES_LANG", "en")
    assert i18n.resolve_lang("ru") == "ru"


def test_env_override_beats_the_system_locale(monkeypatch):
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.setenv("HERMES_COMPOSER_MODES_LANG", "es")
    assert i18n.resolve_lang("") == "es"


def test_system_locale_is_the_desktopless_answer(monkeypatch):
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    assert i18n.resolve_lang("") == "ru"


def test_everything_unknown_falls_back_to_english():
    assert i18n.resolve_lang("kl-DK") == "en"  # env unset by fixture
    assert i18n.system_lang() in i18n.SUPPORTED_LANGS


def test_closing_sentence_and_clause_cover_every_supported_lang():
    for lang in i18n.SUPPORTED_LANGS:
        sentence = i18n.ask_closing_sentence(lang)
        clause = i18n.answer_language_clause(lang)
        assert sentence and clause
        assert "A/B/C" in sentence or "A/B/C" in sentence.replace("выполнить A/B/C", "A/B/C")
    # unknown language degrades instead of raising
    assert i18n.ask_closing_sentence("xx") == i18n.ask_closing_sentence("en")


def test_mode_messages_format_without_key_errors():
    out = i18n.mode_message("unknown", "en", arg="banana", ids="ask, agent")
    assert "banana" in out and "ask, agent" in out
    out_ru = i18n.mode_message("set", "ru", label="Plan", arg="plan")
    assert "Plan" in out_ru and "по умолчанию" in out_ru


def test_every_catalogue_language_has_every_entry():
    for table in (i18n._ANSWER_CLAUSE, i18n._ASK_CLOSING, i18n._ASK_ACTIONS):
        assert set(table) == set(i18n.SUPPORTED_LANGS)
    for key, table in i18n._MODE_MESSAGES.items():
        assert set(table) == set(i18n.SUPPORTED_LANGS), key
