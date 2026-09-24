# 🧪 Стандарты тестирования v2 (Testing Rules & Recipes)

Инструкции для AI-агента по написанию, запуску и отладке тестов в модульном монорепозитории **IntraLink v2**.

---

## 1. Команды запуска тестов

Конфигурация pytest и пути поиска модулей (`pythonpath = [".", "core", "api", "worker"]`) зафиксированы в корневом `pyproject.toml`. Запуск тестов не требует ручной настройки `PYTHONPATH`.

```powershell
# Запуск всех тестов монорепозитория (Windows / PowerShell)
uv run pytest -v

# Запуск тестов конкретного пакета / среза
uv run pytest api/tests/ -v
uv run pytest core/tests/ -v
uv run pytest worker/tests/ -v

# Запуск конкретного тестового файла
uv run pytest api/tests/test_tickets.py -v
uv run pytest worker/tests/test_command_dispatcher.py -v
```

---

## 2. Архитектура тестов и изоляция

1. **Изоляция базы данных:**
   * Юнит-тесты не должны зависеть от работающего внешнего контейнера PostgreSQL.
   * Для изолированных тестов используется in-memory SQLite: `sqlite+aiosqlite:///:memory:`.
   * Модели SQLAlchemy поддерживают типы с fallback из PostgreSQL JSONB/UUID.
   * Все фикстуры сессий должны очищать состояние или изолироваться в транзакциях с откатом (`rollback`).
2. **Мокирование Redis:**
   * Тесты изолируются от реального Redis через `unittest.mock.AsyncMock`.
   * Мокируются вызовы Redis: `get`, `set`, `delete`, `expire`, `publish`, а также методы брокера Taskiq.
3. **Мокирование внешнего API IntraService:**
   * **Никаких реальных запросов** к внешней сети IntraService в автоматических тестах.
   * В v2 сетевой стек построен на `httpx.AsyncClient`. Все HTTP-запросы мокируются через библиотеку `respx` или `unittest.mock.AsyncMock` (использование `aiohttp` / `aresponses` строго запрещено).
4. **Тестирование фоновых задач Taskiq (Worker):**
   * Задачи, декорированные `@broker.task`, в тестах вызываются напрямую как функции (`await my_task(...)`) с передачей необходимых аргументов и замокированных зависимостей (`TaskiqDepends`), либо через in-memory брокер.

---

## 3. Требования к качеству и No-Kludge Policy в тестах

* **No False Positives & No Stubs:** Запрещено писать фиктивные тесты-заглушки вида `assert True` или маскировать провалы через `try ... except Exception: pass`.
* **Asyncio:** Все асинхронные тесты помечаются `@pytest.mark.asyncio` (или подхватываются автоматически благодаря `asyncio_mode = "auto"` в `pyproject.toml`).
* **Покрытие краевых случаев:** Тесты обязаны покрывать не только счастливый путь (happy path), но и краевые случаи: сетевые таймауты, пустые ответы API, некорректный JSON, HTTP 401/403/404/500, состояние гонки (Optimistic Lock) и циклы автоответчиков (Anti-Loop).
* **Обязательный прогон:** После изменения логики любого модуля запуск соответствующих тестов (`uv run pytest <path> -v`) является строго обязательным шагом валидации.
