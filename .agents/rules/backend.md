# 🛠 Стандарты написания бэкенд-кода (Backend Coding Rules)

Данный свод правил обязателен для AI-агента при генерации или модификации кода сервисов `core-api`, `execution-worker`, `helpdesk-cli` и `shared`.

---

## 1. Асинхронный стек и управление параллелизмом

* **FastAPI:**
  * Все эндпоинты объявляются как `async def`.
  * Для зависимостей использовать `Depends()`. Взаимодействие с базой данных — строго через `AsyncSession = Depends(get_db)`.
  * Валидация входных данных: Pydantic v2 `BaseModel` в качестве тел запросов и query/path параметров.
* **Неблокирующий I/O:**
  * **Запрещены** блокирующие функции `time.sleep()`, `subprocess.run()`, синхронные WMI-запросы в основном цикле событий `asyncio`.
  * Для пауз использовать `await asyncio.sleep(...)`.
  * Для вызова синхронного системного кода или WinRM/WMI обязательно использовать:
    ```python
    result = await asyncio.to_thread(sync_heavy_function, *args, **kwargs)
    ```
* **Сетевые клиенты (aiohttp):**
  * Никогда не создавать новый `aiohttp.ClientSession()` на каждый запрос.
  * Использовать долгоживущие сессии с пулом соединений (`TCPConnector(limit=..., ttl_dns_cache=300)`) и явным закрытием при `lifespan` завершении приложения.

---

## 2. Работа с базой данных (SQLAlchemy 2.0 Async)

* **Синтаксис запросов:**
  * Использовать `select()`, `update()`, `delete()`.
  * Выполнение: `result = await session.execute(stmt)`.
  * Извлечение сущностей: `items = result.scalars().all()` или `item = result.scalar_one_or_none()`.
  * **Запрещен устаревший синтаксис:** `session.query(Model)...`.
* **Управление сессиями:**
  * Всегда использовать контекстный менеджер:
    ```python
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # операции с авто-коммитом
    ```
  * Модели `Mapped[...] = mapped_column(...)` строго с указанием типов.

---

## 3. Pydantic v2 и типизация

* **Спецификация моделей:**
  * Использовать `ConfigDict(from_attributes=True)` вместо устаревшего `class Config: orm_mode = True`.
  * Валидация объектов: `MySchema.model_validate(obj)`.
  * Сериализация в словарь: `schema.model_dump(mode="json")`.
  * Валидаторы полей: `@field_validator("field_name")` вместо устаревшего `@validator`.
* **Аннотации типов (Python 3.11+):**
  * `value: str | None = None` (вместо `Optional[str]`).
  * `items: list[int]` (вместо `List[int]`).
  * `mapping: dict[str, Any]` (вместо `Dict[str, Any]`).

---

## 4. Использование пакета shared (SSOT)

* Всегда импортировать утилиты нормализации и диагностики из `shared`:
  ```python
  from shared.normalizer import normalize_pc_name, extract_pc_names_from_text, is_valid_pc_name
  from shared.diagnostics import run_host_diagnostics
  from shared.json_utils import json_dumps, json_loads
  ```
* Запрещено создавать локальные дубликаты регулярных выражений или функций сетевых проверок.

---

## 5. Работа с Redis и очередями

* Все обращения к Redis выполняются через `redis.asyncio` (`aioredis`).
* События публикуются через `XADD` в персистентный стрим (с ограничением `maxlen=10000, approximate=True`).
* Распределенные блокировки (`lock:host:<pc_name>`) всегда освобождаются через безопасный Lua-скрипт по токену владельца.
