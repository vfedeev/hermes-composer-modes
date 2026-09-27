"""Live language matrix — what the plugin says in each supported language.

Loads the plugin exactly the way Hermes does (package import against a throwaway
HERMES_HOME), pushes a locale through the REAL path (dashboard API -> store ->
pre_llm_call hook), and prints the resolved model-facing sentences. Then reads
the desktop half's STR catalogue with Node and prints the same UI keys in every
bundle. No mocks: if the ladder, the persistence or the catalogue is broken,
this prints something wrong.

Run:  python scripts/lang_matrix.py [lang ...]     (default: en ru es de th)
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MARK = "\n" + "=" * 72


def load_plugin(home: Path):
    import os

    os.environ["HERMES_HOME"] = str(home)
    for var in ("HERMES_COMPOSER_MODES_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        os.environ.pop(var, None)  # clear the env/OS rungs: only the app-locale rung remains
    for name in [n for n in list(sys.modules) if n.startswith("composer_modes")]:
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        "composer_modes_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["composer_modes_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_api():
    spec = importlib.util.spec_from_file_location(
        "composer_modes_api_matrix", ROOT / "dashboard" / "plugin_api.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def closing_of(note: str) -> tuple[str, str]:
    """The quoted ask closing sentence + the answer-language clause (tail of the note)."""
    q = note.partition('language, adapting only the A/B/C list and keeping the rest word-for-word: "')
    rest = q[2] if q[0] else note
    sentence = rest.split('" ', 1)[0]
    clause = rest.rsplit(". ", 1)[-1].strip()
    return sentence, clause


def main() -> int:
    langs = sys.argv[1:] or ["en", "ru", "es", "de", "th"]
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        plugin = load_plugin(Path(tmp) / "home")
        api = load_api()
        ctx_holder = {}

        class Ctx:
            def register_hook(self, name, cb):
                ctx_holder.setdefault(name, cb)

            def register_command(self, name, handler, **_kw):
                ctx_holder["cmd_" + name] = handler

            def register_skill(self, *_a, **_kw):
                pass

        plugin.register(Ctx())
        pre_llm = ctx_holder["pre_llm_call"]
        mode_cmd = ctx_holder["cmd_mode"]
        store = plugin.store.load_store(plugin.PLUGIN_NAME)
        store.set_mode("sess-matrix", "ask")

        print(MARK)
        print("MODEL-FACING (agent half) — locale via the real API POST /locale,")
        print("persisted to state.json, resolved by the pre_llm_call hook:")
        print(MARK)
        failures = 0
        for loc in langs:
            asyncio.run(api.write_locale({"locale": loc}))
            stored = store.get_locale()
            note = pre_llm(session_id="sess-matrix", user_message="hi")
            text = (note or {}).get("context", "")
            sentence, clause = closing_of(text) if text else ("(no note!)", "")
            shown = "→ en (fallback)" if loc in {"de", "th"} else f"→ {loc}"
            print(f"\n  app locale {loc:<5} {shown:<14} stored={stored!r}")
            print(f"    ask closing : {sentence}")
            print(f"    answer instr: {clause[:110]}")
            reply = mode_cmd("banana")
            print(f"    /mode reply : {reply[:110]}")
            if not text:
                failures += 1
        print(MARK)

        # desktop half: same catalogue keys across bundles, via Node
        js = """
const fs=require('fs');
const src=fs.readFileSync(process.argv[1],'utf8');
const s=src.indexOf('const STR = {'); const e=src.indexOf('\\n}', s)+2;
eval(src.slice(s,e).replace('const STR = ','global.STR='));
const keys=['planReadyTitle','implement','modify','readPlan','debugRetry','noSession','qSend'];
for (const loc of ['en','ru','es']) {
  console.log('  '+loc.padEnd(3)+' | '+keys.map(k=>STR[loc][k]).join(' | '));
}
console.log('  parity en/ru/es: '+['ru','es'].every(l=>
  Object.keys(STR.en).every(k=>typeof STR[l][k]===typeof STR.en[k])));
"""
        out = subprocess.run(
            ["node", "-e", js, str(ROOT / "desktop" / "plugin.js")],
            capture_output=True, text=True,
        )
        print("DESKTOP-HALF UI (desktop/plugin.js STR catalogue, same keys per row):")
        print(out.stdout or out.stderr)
        print(MARK)

        # the ladder without a desktop half (CLI/TUI): env + OS rungs
        import os

        store.set_locale("")  # no app locale at all → env rung must answer
        os.environ["HERMES_COMPOSER_MODES_LANG"] = "ru"
        note = pre_llm(session_id="sess-matrix", user_message="привет")
        sentence, _ = closing_of((note or {}).get("context", ""))
        print(f"ENV RUNG  HERMES_COMPOSER_MODES_LANG=ru, no app locale:\n    {sentence}")
        os.environ["HERMES_COMPOSER_MODES_LANG"] = "xx"  # invalid env → OS rung
        os.environ["LANG"] = "en_US.UTF-8"
        note = pre_llm(session_id="sess-matrix", user_message="hi")
        sentence, _ = closing_of((note or {}).get("context", ""))
        print(f"ENV=xx (invalid) + LANG=en_US.UTF-8 → OS rung:\n    {sentence}")
        print(MARK)
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
