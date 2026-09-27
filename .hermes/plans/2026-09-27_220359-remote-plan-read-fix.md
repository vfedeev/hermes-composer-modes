# План: починка «всё ещё испанский + ошибки чтения» и внесение правки в git

## Goal

Устранить обе проблемы со скриншота — карточки `Preguntas sobre el plan · v13.1-c5frm` (на Windows крутится старая desktop-половина upstream v13.1) и ошибку `Error invoking remote method 'hermes:readFileText': ... file does not exist` (desktop-половина читает файлы планов с локального диска клиента, а в SSH-режиме файл живёт на бэкенде) — добавить чтение планов через backend-REST, выкатить форк в оба end-point (git + установка), открыть PR автору.

## Current context / assumptions

Скририншот разобран; диагностика (подтверждена кодом и состоянием):

1. **Испанский в UI** — не наш баг фикса: хедер карточки показывает `v13.1-c5frm`, т.е. desktop-приложение пользователя (Windows) грузит **старую upstream-половину** `~/.hermes/desktop-plugins/composer-modes/plugin.js` (в SSH-режиме Electron живёт на клиенте, и `desktop-plugins/` — на диске Windows). Наша v14.0 (`STR`-каталог, ru/en/es) лежит в репозитории форка и на сервере в `~/.hermes/plugins/composer-modes` (agent-half обновлён rsync'ом), но на клиент ещё не доставлялась: half materialize'ится из **источника установки плагина**, а он указывает на upstream.
2. **`hermes:readFileText ... does not exist`** — классический client/backend split: `resolvePlanAbs()` (desktop/plugin.js:748) склеивает `host.state.cwd` (удалённый путь вида `/home/vvv/...`) и относительный путь плана, а `window.hermesDesktop.readFileText(abs)` — это IPC Electron-main'а, читающий **локальный диск Windows**, где `/home/vvv/...` не существует. Это ломает `::plan-questions`-карточку и plan-reader pane на любой SSH/удалённой connection — известная болевая точка (записана в памяти как «в SSH-режиме не работает, выводите вопросы текстом»). Чинится правильно: чтение через бэкенд.
3. `state.json` на сервере: `"locale": ""` — v13.1 не умеет слать локаль; после доставки v14.x в клиент это самопочинится.
4. Git-состояние: форк `vfedeev/hermes-composer-modes`, ветка `feat/system-language` (HEAD `db8a9ca`, 141 pytest, smoke зелёные), локально checked out `feat/system-language`; upstream main `de372aa`. PR ни одного не открывалось.
5. Допущение: у пользователя плагин установлен как catalog-пакет (`hermes plugins install composer-modes`), поэтому переустановка нужна с явным git-источником форка. Синтаксис источника проверяем на шаге 6 (`--help`), а не угадываем.

## Architecture / proposed approach

Единый путь чтения файлов планов: **backend first, local IPC fallback**. Добавляем в dashboard-API плагина read-only эндпоинт `GET /plan?path=...`, который пускает только пути, маторящиеся `…/.hermes/plans/<safe-name>.md|json` (без `..`), с потолком размера; desktop-половина читает планы через `ctx.rest('/plan?...')`, а `readFileText` остаётся запасным вариантом для старых бэкендов. После этого форк доставляется в SSH-сетап переустановкой плагина из `vfedeev/hermes-composer-modes` и открывается PR автору (инфраструктура чтения файлов для него ценна сама по себе — это фикс целого класса багов).

## Step-by-step tasks

### Task 1 — Failing test: эндпоинт GET /plan (5 мин)

Файл: `tests/test_api.py` (создать рядом с существующими api-тестами; фикстура `api` конftest'а поднимает FastAPI-модуль плагина).

```python
# tests/test_api.py — добавить в конец файла (импорты asyncio/pytest там уже есть;
# если файла нет — взять шапку фикстуры из tests/test_bilingual.py, импорт модуля
# dashboard/plugin_api.py под именем "composer_modes_api_bilingual")
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
```

Прогон (ожидаем FAIL, `read_plan` ещё нет):

```bash
cd ~/work/projects/hermes-composer-modes
/home/vvv/.hermes/hermes-agent/venv/bin/pytest -c tests/pytest.ini tests/test_api.py -q
# expected: AttributeError ... module has no attribute 'read_plan' → FAILED
```

### Task 2 — Реализация эндпоинта (5 мин)

Файл: `dashboard/plugin_api.py`. В секцию импортов добавить `import pathlib` (если нет). После существующих моделей/хелперов, рядом с другими роутами, вставить хелпер и роут — `read_plan` вызывается как `await api.read_plan(payload)` из тестов и как handler с query-param:

```python
PLAN_READ_RE = re.compile(r"^[^\x00]*/\.hermes/plans/([A-Za-z0-9._-]{1,160})\.(md|json)$")
PLAN_MAX_BYTES = 512_000

async def read_plan(payload: dict) -> dict:
    """Read-only plan/ questions file reader for remote backends (SSH/cloud):
    the desktop half cannot touch the backend's disk via local IPC."""
    raw = payload.get("path") if isinstance(payload, dict) else payload
    path = str(raw or "")
    if ".." in path or not PLAN_READ_RE.match(path):
        return {"ok": False, "error": "invalid-path"}
    p = pathlib.Path(path)
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
    return await read_plan({"path": path})
```

Проверка: `pytest ... tests/test_api.py -q` → PASS; весь прогон `pytest -c tests/pytest.ini` → `142 passed`.

Commit: `git add dashboard/plugin_api.py tests/test_api.py && git commit -m "feat(api): read-only GET /plan for remote backends"`

### Task 3 — Desktop: readPlanText + переключение двух ридеров (10 мин)

Файл: `desktop/plugin.js`.

3a. Рядом с `stageLocale` (после строки ~400) добавить:

```js
/** Backend-first plan reader: in SSH/remote mode the file lives on the
 *  backend's disk and local readFileText cannot see it. ctx.rest('/plan') is
 *  answered by dashboard/plugin_api.py; old backends 404 → IPC fallback. */
async function readPlanText(absPath) {
  if (ctxRef && typeof ctxRef.rest === 'function') {
    try {
      const r = await ctxRef.rest('/plan?path=' + encodeURIComponent(absPath))
      if (r && r.ok === true && typeof r.text === 'string') return r.text
      if (r && r.error === 'too-large') throw new Error(T('qTooLarge'))
      if (r && r.error === 'not-found') throw new Error(T('qMissing'))
    } catch (e) {
      if (e && e.__local) throw e
      if (e && (e.message === T('qTooLarge') || e.message === T('qMissing'))) throw e
      /* old backend / route missing: fall through to local IPC */
    }
  }
  const read = typeof window !== 'undefined' && window.hermesDesktop ? window.hermesDesktop.readFileText : null
  if (typeof read !== 'function') throw new Error(T('readerNoRead'))
  const r = await read(absPath)
  if (r && r.binary) throw new Error(T('readerBinary'))
  return String((r && r.text) || '')
}
```

В каталог `STR` (en/ru/es) добавить ключи, если их нет: `qTooLarge` — en `'plan file too large'`, ru `'файл плана слишком большой'`, es `'archivo de plan demasiado grande'`; `qMissing` — en `'plan file not found'`, ru `'файл плана не найден'`, es `'archivo de plan no encontrado'`. (Проверить наличие: `grep -n "qTooLarge\|qMissing" desktop/plugin.js`.)

3b. В `PlanReaderPane` (~стр. 870–905) заменить блок получения текста: убрать `const read = ...readFileText...`, проверку `typeof read`, `Promise.resolve(read(abs))` и ветку `r.binary`; тело `useEffect` после `if (!abs)` становится:

```js
    let alive = true
    planReaderView.set({ status: 'loading' })
    readPlanText(abs)
      .then((text) => {
        if (!alive) return
        planReaderView.set({ status: 'ok', text })
        probe(`planview ok file=${file} len=${text.length}`)
      })
      .catch((e) => {
        if (!alive) return
        planReaderView.set({ status: 'err', msg: String((e && e.message) || e) })
        probe(`planview err file=${file} msg=${String((e && e.message) || e)}`)
      })
    return () => { alive = false }
```

3c. В useEffect план-вопросов (~стр. 1255–1290) аналогично: удалить `const read`/`typeof read`/`r.binary`-ветку, `Promise.resolve(read(abs)).then((r) => { ... normalizeQuestions(r ? r.text : '') ... })` заменить на:

```js
        let alive = true
        setQEntry(file, { status: 'loading' })
        readPlanText(abs)
          .then((text) => {
            if (!alive) return
            const list = normalizeQuestions(text)
            if (!list || !list.length) {
              setQEntry(file, { status: 'err', msg: T('qParseErr') })
              probe(`pq load err file=${file} parse`)
              return
            }
            setQEntry(file, { status: 'ok', questions: list, answers: {}, index: 0 })
            probe(`pq load ok n=${list.length}`)
          })
          .catch((e) => {
            if (!alive) return
            setQEntry(file, { status: 'err', msg: String((e && e.message) || e) })
            probe(`pq load err file=${file} msg=${String((e && e.message) || e)}`)
          })
        return () => { alive = false }
```

Тот же паттерн применить к любому третьему месту вызова `readFileText` (найти: `grep -n "readFileText" desktop/plugin.js` — остаются только helper 3a и `readerNoRead`-строки каталога).

### Task 4 — Smoke + тесты desktop-половины (5 мин)

Файл: `scripts/smoke_desktop_half.mjs`. В заглушке `ctx.rest` добавить маршрут:

```js
    if (String(url).startsWith('/plan')) {
      return { ok: true, text: '{"title":"t","questions":[{"q":"?","options":["a"]}]}' }
    }
```

Если smoke-заглушка `host.state.cwd` даёт относительный путь — трогать не нужно. Прогон:

```bash
node --check desktop/plugin.js && node scripts/smoke_desktop_half.mjs   # expected: SMOKE PASSED
/home/vvv/.hermes/hermes-agent/venv/bin/pytest -c tests/pytest.ini      # expected: 142 passed
hermes plugins validate .                                               # expected: Validation passed
```

Commit: `git add desktop/plugin.js scripts/smoke_desktop_half.mjs && git commit -m "fix(desktop): read plan files through the backend, local IPC as fallback"`

### Task 5 — Версии 2.1.1 + changelog (3 мин)

Одновременный бамп всех пяти (правило AGENTS.md §5): `__init__.py VERSION`, `dashboard/plugin_api.py VERSION`, `plugin.yaml version`, `dashboard/manifest.json "version"` → `"2.1.1"`; `desktop/plugin.js` `const VER = 'v14.1'`. В `CHANGELOG.md` новой секцией `## 2.1.1` описать фикс чтения на удалённых бэкендах. В `docs/localization.md` §«Каналы» дописать одну строку: план-файлы читаются с бэкенда, поэтому карточки работают и в SSH-режиме. Коммит: `chore(release): v2.1.1 — plan reads over REST (remote backends)`.

### Task 6 — Git: main форка + установка + PR автору (10 мин)

6a. Смержить в main форка и запушить обе ветки (т.к. источник установки смотрит на default branch):

```bash
git checkout main && git merge --ff-only feat/system-language
git push origin main feat/system-language
# expected: ca4a422..db8a9ca... + новые коммиты, both branches up to date
```

6b. Установить форк в SSH-сетап (Windows-клиент → Linux-бэкенд). Сначала разведка синтаксиса источника, без догадок:

```bash
hermes plugins install --help | sed -n '1,40p'
```

Затем (обычно форма owner/repo):

```bash
hermes plugins uninstall composer-modes
hermes plugins install composer-modes --source vfedeev/hermes-composer-modes   # подставить флаг из --help
hermes plugins enable composer-modes
```

Проверки после рестарта backend/Reload desktop plugins (⌘K) в приложении:
- `grep -m1 VERSION ~/.hermes/plugins/composer-modes/__init__.py` (сервер) → `2.1.1`;
- **на Windows-клиенте**: `findstr /C:"const VER" %USERPROFILE%\.hermes\desktop-plugins\composer-modes\plugin.js` → `v14.1`;
- через пару секунд после любого режима: `python -c "import json;print(json.load(open('/home/vvv/.hermes/plugin-data/composer-modes/state.json'))['locale'])"` → `ru`;
- ручная E2E: послать `/mode plan`, дать агенту сгенерировать план с `::plan-questions{file=...}`, дождаться карточки — карточка должна быть **по-русски** и **без** `hermes:readFileText`.
- Если `--source`-флаг в этом клике недоступен (каталог/пиновка SHA), альтернатива: временно `rsync` desktop-половины на клиент (`copy desktop\plugin.js %USERPROFILE%\.hermes\desktop-plugins\composer-modes\plugin.js`) и пометить в этом плане риск — тогда постоянный путь: PR автора → их релиз, или pin на SHA форка.

6c. PR автору (отдельный, только bug-fix channel — локализация тоже в этом PR, но как два коммит-блока уже существуют). `gh` авторизован:

```bash
gh pr create --repo LisandroNahuelH/hermes-composer-modes --head vfedeev:main --base main \
  --title "fix: make plan cards language-aware and readable on remote backends" \
  --body-file PR_BODY.md   # написать PR_BODY.md по мотивам docs/localization.md + README секции 2.1.0/2.1.1
```

### Task 7 — Память/навыки (2 мин)

Обновить запись памяти «Desktop SSH mode: ... карточка ::plan-questions читает локальный диск Windows → в SSH-режиме не работает» на «исправлено в форке v2.1.1 (GET /plan + readPlanText)».

## Tests / validation (итог)

- `pytest -c tests/pytest.ini` → **142 passed** (новый `test_plan_read_endpoint`).
- `node --check`, `smoke_desktop_half.mjs` (с `/plan`-роутом) → `SMOKE PASSED`.
- `hermes plugins validate .` → passed.
- Ручная SSH-mode E2E из 6b — карточка вопросов на русском, читается с бэкенда.
- Критерий приёмки: скриншот-сценарий невоспроизводим: нет `v13.1` (клиент поставлен с форка), нет `hermes:readFileText` ошибки (чтение через REST), нет испанского (app-локаль `ru` через STR).

## Risks, tradeoffs, open questions

- **Материализация desktop-half из источника установки** не проверена на этом хосте до шага 6b — если CLI-плагин не позволяет поставить форк, остаётся ручной copy на клиент (риск рассинхрона) и постоянный путь через релиз автора; флаг из `--help` решает это на шаге 6b.
- `GET /plan` отдаёт любой файл с суффиксом `.hermes/plans/*.md|json` — accepted risk: read-only, whitelist-regex, без `..`, ≤512 КБ; путь содержит только содержимое планов, которые агент и так пишет в воркспейс. Если автору это не понравится, альтернатива в PR — session-scoped `GET /plan?file=<rel>` с резолвом cwd бэкенда (в плане не реализовано сознательно, YAGNI).
- Старая desktop-половина + новый бэкенд (обратный порядок апгрейда): без `/plan` клиента всё работает по-старому (IPC) — обратная совместимость не ломается.
- Испанский в заголовке `· v13.1-c5frm` — это и есть доказательство, что фикс 2.1.0 не доехал до клиента; если после 6b хедер покажет `v14.1`, проблема доставки закрыта.
- **Не решается этим планом**: гонка «clear check» при реконнекте (память) — вне области composer-modes.
