# 🛠 Стандарты написания бэкенд-кода v2 (Backend Coding Rules)

Данный свод правил обязателен для AI-агента при генерации или модификации кода модулей `api/src/features/*`, фонового сервиса `worker/` и ядра `core/` в архитектуре IntraLink v2.

---

## 0. 🚫 Принцип бескомпромиссного качества (No-Kludge & Root-Cause Policy)

* **СТРОГО ЗАПРЕЩЕНО внедрять «костыли», временные заплатки, нетипизированные хаки или маскировать архитектурные дефекты.**
* **Обязанность агента:** При обнаружении фундаментальной проблемы, несоответствия контрактов, узких мест или концептуальных противоречий в архитектуре — **агент обязан немедленно и открыто обозначить проблему, вскрыть первопричину (root cause) и предложить фундаментальное («big shot») решение**, устраняющее дефект в корне, а не прятать его под слоем условных операторов, фиктивных фолбэков или костылей.
* Любое решение должно быть архитектурно чистым, расширяемым и долговечным.

---

## 1. Асинхронный стек и управление параллелизмом

* **FastAPI:**
  * Все эндпоинты объявляются как `async def`.
  * Для зависимостей использовать `Depends()`. Взаимодействие с базой данных — строго через `AsyncSession = Depends(get_session)`.
  * Валидация входных данных: Pydantic v2 `BaseModel` в качестве тел запросов и query/path параметров.
* **Неблокирующий I/O:**
  * **Запрещены** блокирующие функции `time.sleep()`, `subprocess.run()`, синхронные WMI/RPC-запросы в основном цикле событий `asyncio`.
  * Для пауз использовать `await asyncio.sleep(...)`.
  * Для вызова синхронного системного кода или WinRM/PowerShell обязательно использовать:
    ```python
    result = await asyncio.to_thread(sync_heavy_function, *args, **kwargs)
    ```
* **Сетевые клиенты (httpx):**
  * Никогда не создавать новый `httpx.AsyncClient()` на каждый HTTP-запрос.
  * Использовать долгоживущий клиент с пулом соединений (`httpx.Limits(max_keepalive_connections=20, max_connections=50)`) и явным закрытием при `lifespan` завершении приложения.

---

## 2. Работа с базой данных (SQLAlchemy 2.0 Async)

* **Синтаксис запросов:**
  * Использовать `select()`, `update()`, `delete()`.
  * Выполнение: `result = await session.execute(stmt)`.
  * Извлечение сущностей: `items = result.scalars().all()` или `item = result.scalar_one_or_none()`.
  * **Запрещен устаревший синтаксис:** `session.query(Model)...`.
* **Управление сессиями:**
  * Модели `Mapped[...] = mapped_column(...)` строго с указанием типов.
  * Всегда использовать контекстный менеджер при ручном управлении:
    ```python
    async with async_session_maker() as session:
        async with session.begin():
            # операции с авто-коммитом
    ```

---

## 3. Pydantic v2 и типизация

* **Спецификация моделей:**
  * Использовать `ConfigDict(populate_by_name=True, from_attributes=True)` вместо устаревшего `class Config: orm_mode = True`.
  * Валидация объектов: `MySchema.model_validate(obj)`.
  * Сериализация в словарь: `schema.model_dump(mode="json")`.
  * Валидаторы полей: `@field_validator("field_name", mode="before")` вместо устаревшего `@validator`.
* **Аннотации типов (Python 3.12+):**
  * `value: str | None = None` (вместо `Optional[str]`).
  * `items: list[int]` (вместо `List[int]`).
  * `mapping: dict[str, Any]` (вместо `Dict[str, Any]`).

---

## 4. Использование пакета `core/` (Web-Agnostic Core)

* Вся повторно используемая бизнес-логика выносится в пакет `core/`:
  ```python
  from core.diagnostic.ports import probe_diagnostic_ports, probe_tcp_port
  from core.diagnostic.ping import fast_ping
  from core.intraservice import IntraServiceClient, TaskDTO
  from core.rag.embedder import get_embedding_vector
  from core.database.models import CommandRecord, TaskKnowledgeBase
  ```
* Запрещено создавать локальные дубликаты сетевых проверок, клиентов API или функций парсинга.

---

## 5. Работа с очередями задач (Taskiq) и изоляция срезов (VSA)

* **Шина задач:** Фоновые задачи оформляются через `@broker.task(queue_name=...)`.
* **Внедрение зависимостей:** Использовать `TaskiqDepends` для получения сессий БД и клиентов.
* **Изоляция срезов (Banned API):**
  * Вертикальные срезы `api/src/features/*` **строго изолированы** и не имеют права импортировать друг друга (контролируется правилом линтера `TID251` в `ruff.toml`).
  * Любой общий функционал переносится в `core/`.
