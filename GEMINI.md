# Инструкции для AI-агента — IntraLink

Этот файл является системным руководством для AI-агентов в данном воркспейсе.
Он содержит **правила, принципы и ограничения** — не документацию проекта.

> [!IMPORTANT]
> Перед внесением изменений обязательно ознакомьтесь с документацией в папке [`docs/`](docs/):
> - **Архитектура системы:** [`docs/architecture.md`](docs/architecture.md)
> - **API Core Gateway:** [`docs/services/core-api/README.md`](docs/services/core-api/README.md)
> - **Внешний API IntraService:** [`docs/external/intraservice_api/IntraService_API_Index.md`](docs/external/intraservice_api/IntraService_API_Index.md)

---

## 0. Поддержание порядка в документации

### Правила для `GEMINI.md`
- **Только правила и инварианты.** Описание эндпоинтов, полей БД и команды запуска идут в `docs/` или `README.md`.
- **Нет абсолютных путей.** Все ссылки — относительные (`docs/architecture.md`, не `file:///...`).
- **Нет дублирования.** Если информация уже есть в `docs/` или `SKILL.md` — указывать ссылку.

### Структура `docs/` (инвариант — не нарушать)
```
docs/
├── architecture.md          ← единственный источник правды об архитектуре системы
├── services/                ← документация по нашим сервисам
│   ├── core-api/            ← API-контракт, конфигурация, особенности core-api
│   ├── telegram-bot/        ← документация бота (команды, FSM и т.п.)
│   └── <new-service>/       ← новые сервисы добавляются сюда
└── external/                ← справочники внешних систем (не наш код)
    └── intraservice_api/    ← документация внешнего API IntraService
```

---

## 1. Архитектура монорепозитория

Проект — **модульный монорепозиторий**:
- `core-api/` — FastAPI (шлюз состояния, AI Hub, правила SSOT, шина событий, хостинг React SPA `/admin`).
- `poller` (`core-api/app/poller.py`) — автономный фоновый демон опроса IntraService с распределенным Leader Lock.
- `execution-worker/` — фоновый headless-демон исполнения в среде Windows (Active Directory, WinRM, WMI, принтеры).
- `helpdesk-cli/` — **машинный инструментарий и SDK исключительно для AI-агента Antigravity (AGY)**.
- `shared/` — **единый пакет общих утилит (SSOT)** нормализации оборудования (`normalizer.py`), сетевой экспресс-диагностики (`diagnostics.py`) и сериализации (`json_utils.py`).
- `telegram-bot/` — aiogram 3.x (мобильный пейджер + HitL кнопки одобрения).

Каналы связи:
1. **HTTP REST** (`telegram-bot`, `helpdesk-cli`, `intra-web` → `core-api`) — управляющие команды, запросы данных, AI-инференс.
2. **Redis Streams & Pub/Sub** (`core-api`, `poller` → `telegram-bot`, `execution-worker`) — гарантированная доставка событий и очередь исполнения.

---

## 2. Ключевые архитектурные принципы (ПОЧЕМУ)

### `helpdesk-cli` — исключительно инструментарий для AI-агента AGY
Директория `helpdesk-cli/` не разрабатывается как интерактивный терминальный UI для человека. Она спроектирована и развивается **строго как машинный SDK/Tooling для AI-агента Antigravity (AGY)** при вызове слэш-команд (`/triage`, `/diag`, `/task`, `/sync`, `/kb`, `/redirect`) и исполнении специализированных навыков (`.agents/skills/`). Человек взаимодействует с системой через диалог с AI-агентом, Web SPA `/admin` или Telegram-бот.

### Бот не хранит учётные данные
Вся ответственность за хранение и использование паролей IntraService лежит **исключительно на Core API**. Бот получает только отфильтрованные шлюзом данные.

### Гарантированная доставка через Redis Streams
Оповещения в Telegram-бот используют персистентную очередь Redis Streams с Consumer Group `bot_group`, подтверждением доставки `XACK` и периодическим перехватом зависших сообщений `XAUTOCLAIM` (At-Least-Once).

### Нет глобального кэша в worker
В `worker.py` намеренно отсутствует кэш истории заявок между пользователями во избежание утечки данных между пользователями с разными правами доступа.

### Воркер опрашивает IntraService через сервисный аккаунт
`check_updates` выполняет запросы глобально от имени сервисного аккаунта и сопоставляет ID исполнителей локально, снижая сетевую нагрузку.

### Часовые пояса
`last_check_time` хранится в **UTC**. Перед запросом к IntraService конвертируется в `settings.INTRASERVICE_TZ` (по умолчанию `Europe/Moscow`). UTC-время напрямую в API IntraService не передается.

### Безопасное хранение доменных учетных данных в Redis
Доменные учетные данные для WinRM и SMB шифруются Fernet и сохраняются в Redis (`worker:domain_auth`), расшифровываясь воркером через Docker Secret `encryption_key`.

### Жадная загрузка (Eager Loading) кастомных полей
`check_updates` использует жадную загрузку кастомных полей (`include=customfields`) при массовом опросе задач для исключения узких мест при диспетчеризации.

### Единый пакет shared (Single Source of Truth)
Все общие алгоритмы нормализации сетевых устройств (`normalizer.py`), экспресс-диагностики (`diagnostics.py`) и сериализации (`json_utils.py`) живут строго в пакете `shared/`. Создание изолированных копий этих модулей в `helpdesk-cli` или `execution-worker` запрещено.

### Изоляция Poller Lifecycle
Встроенный APScheduler в `core-api` управляется параметром `ENABLE_INTERNAL_SCHEDULER` (по умолчанию `False`). Опрос очереди IntraService в Docker выполняет исключительно демон `poller` (`app.poller`) с распределенным Leader Lock, предотвращая дублирование запросов.

### Специализированные навыки (Skills)
- **Установка принтеров и WinRM/WMI:** [`.agents/skills/printer-orchestration/SKILL.md`](.agents/skills/printer-orchestration/SKILL.md)
- **Триаж очереди и Helpdesk-оператор:** [`.agents/skills/intraservice-helpdesk/SKILL.md`](.agents/skills/intraservice-helpdesk/SKILL.md)

---

## 3. Правила безопасности (запреты)

| Запрет | Причина |
|---|---|
| Не хардкодить `BOT_API_KEY` | Передавать только через переменные окружения |
| Не слушать на `0.0.0.0` вне Docker | При локальной разработке — только `127.0.0.1` |
| Не хранить пароли в боте | Только Core API работает с учётными данными |
| Не добавлять глобальный кэш в worker без учёта `auth_b64` | Уязвимость утечки данных между пользователями |
| Не удалять `secrets.compare_digest` в `deps.py` | Защита от Timing Attacks при проверке API-ключа |
| Не запускать тяжелые I/O задачи без Redis-локов | Опасность перегрузки шар и дублирования работы |

---

## 4. Аутентификация между сервисами

- **Бот / CLI → Core API:** Pre-shared ключ в заголовке `X-Bot-Api-Key: <key>`. Проверка в `core-api/app/routers/deps.py`.
- **Core API → IntraService:** Basic Auth (`Authorization: Basic <base64(login:password)>`).

---

## 5. Инженерные инварианты кодогенерации (для AI-кодера)

1. **Асинхронность и неблокирующий I/O:**
   - Все контроллеры FastAPI и сервисы пишутся строго асинхронно (`async def`).
   - Любые блокирующие операции (запуск subprocess/PowerShell, WMI-запросы, тяжелые расчеты) **обязательно** выносить в поток через `await asyncio.to_thread(...)`.
2. **SQLAlchemy 2.0 (Async):**
   - Использовать исключительно современный синтаксис 2.0: `await session.execute(select(...))` и `scalars()`.
   - Запрещено использовать устаревший синтаксис `session.query(...)`.
   - Сессии создавать через `AsyncSessionLocal()` с `expire_on_commit=False`.
3. **Pydantic v2:**
   - Использовать `model_validate(...)`, `model_dump(mode="json")`.
   - Не использовать устаревшие методы Pydantic v1 (`.dict()`, `.parse_obj()`).
4. **Единый пакет сериализации и утилит (SSOT):**
   - Для сериализации JSON использовать `shared.json_utils` (`json_dumps`, `json_loads` на базе `orjson`).
   - Для парсинга хостов и сетевых проверок импортировать `shared.normalizer` и `shared.diagnostics`.
5. **Современная типизация Python 3.11+:**
   - Использовать встроенные объединения типов (`str | None` вместо `Optional[str]`) и стандартные коллекции (`list[dict]`, `dict[str, Any]` вместо `typing.List`, `typing.Dict`).
6. **Правила обработки ошибок:**
   - В API-роутерах выбрасывать `HTTPException(status_code=..., detail=...)`.
   - В фоновых демонах (`poller`, `execution-worker`) непредвиденные исключения перехватывать через `try...except Exception as e: logger.exception(...)`, предотвращая аварийное падение процесса.

---

## 6. Верификация и запуск тестов

> [!IMPORTANT]
> При запуске тестов `core-api` **обязательно** задавать `PYTHONPATH`, включающий корень монорепозитория и `core-api` (для разрешения импортов `shared` и `app`):

```powershell
# Запуск тестов Core API (PowerShell / Windows)
$env:PYTHONPATH=".;core-api"; uv run pytest core-api/tests/ -v

# Запуск тестов Shared пакета
uv run pytest shared/ -v
```

---

## 7. Документация и правила подсистем

- **Специализированные правила кодинга:** [`.agents/rules/backend.md`](.agents/rules/backend.md)
- **Правила и рецепты тестирования:** [`.agents/rules/testing.md`](.agents/rules/testing.md)
- **Архитектура системы:** [`docs/architecture.md`](docs/architecture.md)
- **Руководство разработчика:** [`docs/developer_guide.md`](docs/developer_guide.md)