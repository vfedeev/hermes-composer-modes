"""GET /plan — the read-only plan file reader for remote backends.

In SSH/cloud mode the desktop half's local IPC (hermes:readFileText) cannot
see the backend's disk; the plugin's own REST route is the only path that
works on every topology. These tests pin the security shape of that route:
only files under a .hermes/plans/ directory, plain names only, bounded size.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys

import pytest


@pytest.fixture()
def api(monkeypatch):
    """The dashboard REST half, isolated like the bilingual suite's."""
    pytest.importorskip("fastapi")
    for name in [n for n in list(sys.modules) if n == "composer_modes_api_plan"]:
        sys.modules.pop(name, None)
    from conftest import ROOT

    spec = importlib.util.spec_from_file_location(
        "composer_modes_api_plan", ROOT / "dashboard" / "plugin_api.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["composer_modes_api_plan"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("composer_modes_api_plan", None)


def test_plan_read_endpoint(tmp_path, api):
    plans = tmp_path / ".hermes" / "plans"
    plans.mkdir(parents=True)
    (plans / "2026-09-27_x.md").write_text("# plan body", encoding="utf-8")
    ok = asyncio.run(api.read_plan({"path": str(plans / "2026-09-27_x.md")}))
    assert ok["ok"] and ok["text"] == "# plan body"
    assert asyncio.run(api.read_plan({"path": str(plans / "ghost.md")}))["error"] == "not-found"
    assert asyncio.run(api.read_plan({"path": "/etc/hosts"}))["error"] == "invalid-path"
    sneaky = str(plans / ".." / ".." / "secret.md")
    assert asyncio.run(api.read_plan({"path": sneaky}))["error"] == "invalid-path"


def test_plan_read_size_cap(tmp_path, api):
    plans = tmp_path / ".hermes" / "plans"
    plans.mkdir(parents=True)
    big = plans / "huge.md"
    big.write_text("x" * (600_000), encoding="utf-8")
    assert asyncio.run(api.read_plan({"path": str(big)}))["error"] == "too-large"


def test_plan_read_accepts_questions_json(tmp_path, api):
    plans = tmp_path / ".hermes" / "plans"
    plans.mkdir(parents=True)
    q = plans / "2026-09-27_y-questions.json"
    q.write_text('{"title":"t"}', encoding="utf-8")
    ok = asyncio.run(api.read_plan({"path": str(q)}))
    assert ok["ok"] and ok["text"] == '{"title":"t"}'
