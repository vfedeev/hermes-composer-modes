"""Composer Modes backend — the REST namespace the desktop half talks to.

Mounted by Hermes at ``/api/plugins/composer-modes/`` whenever the plugin is
enabled (user plugins get their backend imported only when they are in
``plugins.enabled``). The desktop half reaches it with ``ctx.rest``, which is
scoped to this namespace by construction and profile-aware.

Routes
------
``GET  /state``              every known session mode + the default + the locale
``GET  /mode?session_id=…``  the mode that will frame the next turn
``POST /mode``               ``{session_id?, mode, locale?}`` — pin a session (or the default)
``POST /locale``             ``{locale}`` — report the app's display language
``GET  /plan?path=…``        read a plan/questions file (backend disk, whitelist)
``POST /reset``              ``{session_id}`` — drop a session's pin
``GET  /health``             liveness + version

The state itself lives in ``store.py`` beside this file and is shared with the
agent half (same process) through a file-backed, mtime-checked store. The
locale drives the language of the model-facing sentences (see ``i18n.py``).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

PLUGIN_NAME = "composer-modes"
VERSION = "2.1.1"
STORE_MODULE = "composer_modes_store"
_PLUGIN_DIR = Path(__file__).resolve().parent.parent

MODES = ("ask", "agent", "plan", "debug")
DEFAULT_MODE = "agent"

router = APIRouter()


def _store():
    """The shared mode store (same module object as the agent half when possible)."""
    mod = sys.modules.get(STORE_MODULE)
    if mod is None or not hasattr(mod, "load_store"):
        spec = importlib.util.spec_from_file_location(STORE_MODULE, _PLUGIN_DIR / "store.py")
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise RuntimeError("composer-modes: cannot load store.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[STORE_MODULE] = mod
        spec.loader.exec_module(mod)
    return mod.load_store(PLUGIN_NAME)


def _clean_session_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:200]


@router.get("/health")
async def health() -> dict:
    return {"ok": True, "plugin": PLUGIN_NAME, "version": VERSION}


@router.get("/state")
async def state() -> dict:
    snapshot = _store().snapshot()
    return {
        "ok": True,
        "version": VERSION,
        "modes": list(MODES),
        "default": snapshot["default"],
        "locale": snapshot.get("locale", ""),
        "sessions": snapshot["sessions"],
        "enforce_ask": True,
    }


@router.get("/mode")
async def read_mode(session_id: Optional[str] = None) -> dict:
    store = _store()
    sid = _clean_session_id(session_id)
    return {"ok": True, "session_id": sid, "mode": store.get_mode(sid), "default": store.get_default()}


@router.post("/mode")
async def write_mode(body: Optional[dict] = None) -> Any:
    """Pin a mode. Body: ``{"session_id": "...", "mode": "ask", "locale": "ru"}``.

    ``mode: "default"`` (or a missing ``session_id``) writes the default instead
    of pinning a session. A ``locale`` travels with the stage and persists, so
    the model-facing note follows the app's display language.
    """
    payload = body if isinstance(body, dict) else {}
    mode = str(payload.get("mode") or "").strip().lower()
    sid = _clean_session_id(payload.get("session_id"))
    if mode == "default" or (not sid and mode):
        mode = mode or DEFAULT_MODE
    if mode not in MODES:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": f"unknown mode {mode!r}", "modes": list(MODES)},
        )
    try:
        store = _store()
        locale = payload.get("locale")
        if locale is not None:
            store.set_locale(str(locale))
        if sid:
            store.set_mode(sid, mode)
        else:
            store.set_default(mode)
    except Exception as exc:  # never 500 into the composer
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
    return {
        "ok": True,
        "session_id": sid,
        "mode": mode,
        "default": store.get_default(),
        "locale": store.get_locale(),
    }


@router.post("/locale")
async def write_locale(body: Optional[dict] = None) -> Any:
    """Report the app's display language. Body: ``{"locale": "ru"}``.

    Raw codes ('ru', 'ru-RU', 'es-AR', 'en-US'…) are stored as-is and
    normalized by the note builder; unknown values degrade to English there.
    """
    payload = body if isinstance(body, dict) else {}
    value = str(payload.get("locale") or "").strip()[:20]
    try:
        store = _store()
        store.set_locale(value)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
    return {"ok": True, "locale": store.get_locale()}


PLAN_READ_RE = re.compile(r"^[^\x00]*/\.hermes/plans/([A-Za-z0-9._-]{1,160})\.(md|json)$")
PLAN_MAX_BYTES = 512_000


async def read_plan(payload: dict) -> dict:
    """Read-only plan/questions file reader for remote backends.

    In SSH/cloud mode the desktop half lives on the client machine and its
    local IPC (hermes:readFileText) cannot see the backend's disk — the file
    the agent just wrote 'does not exist' there. This route reads it where it
    actually lives. Security shape: only plain names directly under a
    ``.hermes/plans/`` directory, no traversal, no globs, bounded size.
    """
    raw = payload.get("path") if isinstance(payload, dict) else payload
    path = str(raw or "")
    if ".." in path or not PLAN_READ_RE.match(path):
        return {"ok": False, "error": "invalid-path"}
    p = Path(path)
    try:
        if not p.is_file():
            return {"ok": False, "error": "not-found"}
        if p.stat().st_size > PLAN_MAX_BYTES:
            return {"ok": False, "error": "too-large"}
        return {"ok": True, "text": p.read_text(encoding="utf-8", errors="replace")}
    except OSError:
        return {"ok": False, "error": "not-found"}


@router.get("/plan")
async def get_plan(path: str = ""):
    """GET /plan?path=<abs path under .hermes/plans/> — text or a safe error."""
    return await read_plan({"path": path})


@router.post("/reset")
async def reset(body: Optional[dict] = None) -> dict:
    """Drop a session's pin so it falls back to the default mode."""
    payload = body if isinstance(body, dict) else {}
    sid = _clean_session_id(payload.get("session_id"))
    store = _store()
    if sid:
        store.clear_session(sid)
    return {"ok": True, "session_id": sid, "mode": store.get_mode(sid) if sid else store.get_default()}
