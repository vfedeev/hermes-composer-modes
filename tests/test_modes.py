"""Tests for the mode catalogue — the text and the guards the model depends on."""
from __future__ import annotations

import pytest

from modes import (
    ASK_NOTE,
    DEBUG_NOTE,
    DEFAULT_MODE,
    LABELS,
    MODE_IDS,
    NOTES,
    PLAN_NOTE,
    is_mode,
    is_slash_shaped,
    note_for,
)


def test_every_mode_has_a_label_and_agent_is_the_default():
    assert DEFAULT_MODE == "agent"
    assert set(MODE_IDS) == {"ask", "agent", "plan", "debug"}
    assert set(LABELS) == set(MODE_IDS)


def test_agent_and_unknown_modes_carry_no_note():
    assert note_for("agent") is None
    assert note_for("nope") is None
    assert note_for(None) is None
    assert note_for(7) is None


@pytest.mark.parametrize("mode", ["ask", "plan", "debug"])
def test_notes_are_non_empty_and_tagged(mode):
    note = note_for(mode)
    assert note and note.startswith(f"[mode:{mode}]") or note.startswith("[/plan")
    assert len(note) > 200


def test_ask_note_closing_sentence_follows_the_language_not_a_hardcode():
    """The ask contract: the closing sentence is verbatim per language, resolved
    through the i18n ladder — never one hardcoded Spanish line."""
    assert "Never claim or pretend to have performed an action you did not perform." in ASK_NOTE
    # with nothing configured, the default resolves from the system locale; the
    # ladder still answers explicitly per requested language:
    assert "I am in Ask mode and can only answer." in (note_for("ask", "en") or "")
    assert "Я в режиме Ask и могу только отвечать." in (note_for("ask", "ru") or "")
    assert "Estoy en modo Ask, solo puedo responder." in (note_for("ask", "es") or "")
    # unknown locales degrade to English, never crash, never force Spanish
    assert "I am in Ask mode" in (note_for("ask", "th-TH") or "")


def test_notes_carry_the_answer_language_clause():
    from i18n import answer_language_clause

    assert "по-русски" in (note_for("ask", "ru") or "")
    assert answer_language_clause("en") in (note_for("plan", "en") or "")
    assert answer_language_clause("en") in (note_for("debug", "en") or "")


def test_plan_note_carries_the_approval_and_questions_directives():
    assert "::plan-approve{file=" in PLAN_NOTE
    assert "::plan-questions{file=" in PLAN_NOTE
    assert ".hermes/plans/" in PLAN_NOTE


def test_debug_note_carries_the_loop_directive():
    assert '::debug-loop{round="1"}' in DEBUG_NOTE
    assert "Mark as fixed" in DEBUG_NOTE


def test_notes_map_covers_exactly_the_non_agent_modes():
    assert set(NOTES) == {"ask", "plan", "debug"}


@pytest.mark.parametrize(
    "text",
    ["/plan do it", "/mode ask", "/help", "/compact now"],
)
def test_slash_shaped_is_recognised(text):
    assert is_slash_shaped(text)


@pytest.mark.parametrize(
    "text",
    ["", "plan", "a /slash in the middle", "//not-a-command", "text with /plan later"],
)
def test_non_slash_shapes_are_not_commands(text):
    assert not is_slash_shaped(text)


def test_is_mode_is_strict():
    assert is_mode("ask")
    assert not is_mode("ASK")
    assert not is_mode(" agent")
