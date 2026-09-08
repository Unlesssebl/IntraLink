# План очистки проекта IntraLink от рудиментов

## Описание
В процессе разработки и слияния веток в проекте накопились временные артефакты, устаревшие заметки в корне, заброшенные директории и кэши объемом ~2.8 ГБ.

---

## 1. Безопасная немедленная очистка (100% однозначные рудименты)

Эти элементы не влияют на работу кода и сервисов, их удаление абсолютно безопасно.

### 1.1. Временные и устаревшие файлы в корне Git
* **`.codex-stage-commands.patch`** (2.2 КБ) — временный патч предыдущей сессии. Все изменения уже закоммичены в `commands_v2.py`.
* **`diag.md`** (16 КБ) — черновик из чата по паттерну Functional Core / Imperative Shell. Полностью перенесён в `docs/adr/0002-modular-worker-platform.md` и `docs/plans/worker-platform-evolution.md`.
* **`plan.md`** (12 КБ) — старый черновик плана модернизации Rule Engine. Уже реализован в коммитах `1910a3b`, `cd3bb9d`, `a57f9cf` и влит в `develop`.

### 1.2. Заброшенные папки и кэши на диске (освободит ~2.8 ГБ)
* **`bot/`** — старая директория Telegram-бота, оставшаяся после переименования в `telegram-bot/`. Содержит только устаревшие скомпилированные `.pyc` файлы за июнь 2026 года (исходников нет).
* **`core-api/.venv/`** (~89 МБ) — старое изолированное окружение `core-api`, оставшееся до перехода проекта на единый корневой `.venv` через `uv`.
* **`.uv-cache-codex/`** (~164 МБ) — изолированный кэш пакетов от предыдущего агента.
* **`desktop-companion/src-tauri/target-codex/`** (~2.58 ГБ) — дубликат сборочного кэша Rust.
* **`desktop-companion/src-tauri/target/debug/codex-write-test.tmp`** — временный файл проверки записи.

---

## 2. Раздел для последующего решения: Файлы-прокладки (Shims)

В проекте присутствуют файлы-прокладки по 10 строк:
* `execution-worker/diagnostics.py`
* `execution-worker/normalizer.py`
* `helpdesk-cli/diagnostics.py`
* `helpdesk-cli/normalizer.py`
* `core-api/app/utils/normalizer.py`

### Технический контекст и назначение:
1. **Зачем они нужны сейчас:**
   * Если запускать воркер или CLI не из корня монорепозитория, а из подкаталога (`cd execution-worker && python worker.py`), Python помещает в `sys.path` только текущую папку, но **не** корень проекта.
   * Файлы-прокладки динамически добавляют корень в `sys.path` (`sys.path.insert(0, str(_ROOT))`), благодаря чему воркер успешно находит пакет `shared.*` (`shared.printers`, `shared.domain`, `shared.diagnostics`).
   * Внешние скрипты или задачи Windows Task Scheduler могут использовать вызовы вида `from diagnostics import ...`.
2. **Почему они ранее рассматривались как рудименты:**
   * В `GEMINI.md` зафиксировано архитектурное правило: *«Все общие алгоритмы нормализации сетевых устройств (normalizer.py), экспресс-диагностики (diagnostics.py) и сериализации (json_utils.py) живут строго в пакете shared/. Создание изолированных копий этих модулей в helpdesk-cli или execution-worker запрещено»*.
   * Новые обработчики (`install_printer.py`, `create_user.py`) уже используют прямые импорты `from shared.diagnostics import ...`.

### Варианты решения (будет выбрано позже):

* **Вариант А (Оставить как есть — Сохранение совместимости):**
  Файлы `execution-worker/normalizer.py`, `execution-worker/diagnostics.py` и `helpdesk-cli/*` **НЕ удаляются**. Они продолжают служить тонкими адаптерами совместимости, гарантируя работоспособность демона при любом способе запуска (включая автономный запуск на сервере Windows без настройки `PYTHONPATH`).

* **Вариант Б (Чистый SSOT — Рефакторинг точки входа):**
  Перед удалением прокладок в точку входа `execution-worker/worker.py` и `helpdesk-cli/helpdesk.py` явно добавляется инициализация путей:
  ```python
  import sys
  from pathlib import Path
  _ROOT = Path(__file__).resolve().parent.parent
  if str(_ROOT) not in sys.path:
      sys.path.insert(0, str(_ROOT))
  ```
  Импорты внутри сервисов переводятся на `from shared.diagnostics import ...`. После этого файлы-прокладки удаляются.

> [!NOTE]
> В текущей итерации **файлы-прокладки НЕ трогаем**. Они остаются нетронутыми до отдельного решения.

---

## 3. План выполнения текущей очистки (только безопасная часть)

1. **Удаление файлов в Git:**
   ```powershell
   git rm .codex-stage-commands.patch diag.md plan.md
   ```
2. **Удаление мусорных папок и кэшей с диска:**
   - `Remove-Item -Recurse -Force bot/`
   - `Remove-Item -Recurse -Force core-api/.venv/`
   - `Remove-Item -Recurse -Force .uv-cache-codex/`
   - `Remove-Item -Recurse -Force desktop-companion/src-tauri/target-codex/`
   - `Remove-Item -Force desktop-companion/src-tauri/target/debug/codex-write-test.tmp`
3. **Верификация:**
   - Запуск тестов:
     ```powershell
     $env:PYTHONPATH="."; uv run pytest shared/ -v
     $env:PYTHONPATH=".;core-api"; uv run pytest core-api/tests/ -v
     ```
   - Проверка сборки `intra-web`: `npm run build`
4. **Фиксация коммита:**
   ```powershell
   git commit -m "chore: clean up root patch/drafts and stale build caches"
   git push origin develop
   ```
