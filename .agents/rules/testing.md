# 🧪 Стандарты тестирования (Testing Rules & Recipes)

Инструкции для AI-агента по написанию, запуску и отладке тестов в монорепозитории **IntraLink**.

---

## 1. Команды запуска тестов

> [!IMPORTANT]
> **Окружение PYTHONPATH:**
> При запуске тестов `core-api/tests/` обязательно передавать путь к корню и к `core-api`, иначе Python не найдет пакет `shared` и модуль `app`:

```powershell
# Запуск всех тестов core-api (Windows / PowerShell)
$env:PYTHONPATH=".;core-api"; uv run pytest core-api/tests/ -v

# Запуск конкретного тестового файла
$env:PYTHONPATH=".;core-api"; uv run pytest core-api/tests/test_triage.py -v

# Запуск тестов общего пакета shared
uv run pytest shared/ -v
```

---

## 2. Архитектура тестов и изоляция

1. **Изоляция базы данных:**
   * Тесты не должны требовать работающего внешнего PostgreSQL.
   * Для БД используется in-memory SQLite: `sqlite+aiosqlite:///:memory:`.
   * Модели SQLAlchemy поддерживают SQLite-совместимые типы (`JSON_TYPE` и `UUID_TYPE` с fallback из PostgreSQL JSONB/UUID).
2. **Мокирование Redis:**
   * Тесты изолируются от реального Redis через `unittest.mock.AsyncMock`.
   * Мокируются методы: `get`, `set`, `delete`, `expire`, `xadd`, `xreadgroup`, `xack`, `publish`.
3. **Мокирование внешнего API IntraService:**
   * **Никаких реальных запросов** к внешней сети IntraService в юнит-тестах.
   * Все вызовы `aiohttp.ClientSession` перехватываются фикстурами `aresponses` или моками ответов.

---

## 3. Требования к новым тестам

* Все асинхронные тесты помечаются `@pytest.mark.asyncio`.
* Тесты должны проверять как позитивные сценарии (happy path), так и краевые случаи (таймауты, невалидный JSON, пустые ответы, 404/500 коды).
* После внесения изменений в код сервисов прогон тестов соответствующего модуля **обязателен**.
