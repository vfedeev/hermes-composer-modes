"""Bilingual (EN ↔ RU) multilingual test — the real path, no mocks.

Drives the whole chain the desktop half drives in production:
POST /locale (dashboard API) -> state.json (store) -> pre_llm_call (hook) ->
the model-facing note. Asserts that English and Russian notes are actually
in their languages, that switching mid-session takes effect on the next
turn, that the EN note never leaks Spanish, and — the other half of the
contract — that protocol tokens are IDENTICAL in every language because the
desktop parses them.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys

import pytest


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """The dashboard REST half, sharing the conftest plugin's store."""
    pytest.importorskip("fastapi")
    monkeypatch.delenv("HERMES_COMPOSER_MODES_LANG", raising=False)
    monkeypatch.setenv("LANGUAGE", "en")  # pin the OS rung so fallback rows are deterministic
    for name in [n for n in list(sys.modules) if n == "composer_modes_api_bilingual"]:
        sys.modules.pop(name, None)
    from conftest import ROOT

    spec = importlib.util.spec_from_file_location(
        "composer_modes_api_bilingual", ROOT / "dashboard" / "plugin_api.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["composer_modes_api_bilingual"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("composer_modes_api_bilingual", None)


EN_CLOSING = "I am in Ask mode and can only answer."
RU_CLOSING = "Я в режиме Ask и могу только отвечать."
ES_CLOSING = "Estoy en modo Ask, solo puedo responder."


def _note_for(ctx, api, session_id, mode, locale):
    """Stage like the desktop half does, then run the hook for the next turn."""
    asyncio.run(api.write_mode({"session_id": session_id, "mode": mode, "locale": locale}))
    result = ctx.hooks["pre_llm_call"][0](session_id=session_id, user_message="hi")
    assert result and result.get("context")
    return result["context"]


def test_english_note_is_english_and_carries_no_foreign_voice(ctx, api):
    note = _note_for(ctx, api, "sess-bi", "ask", "en-US")
    assert EN_CLOSING in note
    assert RU_CLOSING not in note
    assert ES_CLOSING not in note  # the upstream regression, pinned off
    assert "Answer the user in English" in note


def test_russian_note_is_russian(ctx, api):
    note = _note_for(ctx, api, "sess-bi", "ask", "ru-RU")
    assert RU_CLOSING in note
    assert EN_CLOSING not in note
    assert ES_CLOSING not in note
    assert "Отвечай пользователю по-русски" in note


def test_switching_language_mid_session_hits_the_next_turn(ctx, api):
    """The desktop can switch display language between turns; per-call notes must
    follow it without any reload. A/B/A, not just A/B."""
    first = _note_for(ctx, api, "sess-bi", "ask", "ru")
    assert RU_CLOSING in first
    second = _note_for(ctx, api, "sess-bi", "ask", "en")
    assert EN_CLOSING in second and RU_CLOSING not in second
    third = _note_for(ctx, api, "sess-bi", "ask", "ru")
    assert RU_CLOSING in third and EN_CLOSING not in third


@pytest.mark.parametrize("locale", ["en", "ru", "es", "de", "th"])
def test_protocol_tokens_never_localize(ctx, api, locale):
    """Parsers are language-blind: the directives, paths and tags the desktop
    matches on must be byte-identical in every response language — including
    the unsupported ones that fall back."""
    plan_note = _note_for(ctx, api, "sess-bi", "plan", locale)
    for token in ("::plan-approve{file=", "::plan-questions{file=", ".hermes/plans/", "[/plan"):
        assert token in plan_note, (locale, token)
    debug_note = _note_for(ctx, api, "sess-bi", "debug", locale)
    assert '::debug-loop{round="1"}' in debug_note
    assert "Mark as fixed" in debug_note  # the retry-button contract phrase stays too


def test_unsupported_language_falls_to_english_not_spanish(ctx, api):
    note = _note_for(ctx, api, "sess-bi", "ask", "th-TH")
    assert EN_CLOSING in note
    assert ES_CLOSING not in note


def test_mode_command_replies_are_bilingual(ctx, api, monkeypatch):
    """The /mode command answers in the persisted app language."""
    asyncio.run(api.write_locale({"locale": "ru"}))
    assert "Неизвестный режим" in ctx.commands["mode"]("banana")
    assert "Режимы композера" in ctx.commands["mode"]("")
    asyncio.run(api.write_locale({"locale": "en"}))
    assert "Unknown mode" in ctx.commands["mode"]("banana")
    assert "Composer modes" in ctx.commands["mode"]("")
