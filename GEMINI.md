# Инструкции для AI-агента — IntraLink

Этот файл является системным руководством для AI-агентов в данном воркспейсе.
Он содержит **правила, принципы и ограничения** — не документацию проекта.

> [!IMPORTANT]
> Перед внесением изменений обязательно ознакомьтесь с документацией в папке [`docs/`](docs/):
> - **Архитектура системы:** [`docs/architecture.md`](docs/architecture.md)
> - **API Core Gateway:** [`docs/services/core-api/api_reference/README.md`](docs/services/core-api/api_reference/README.md)
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
- `core-api/` — FastAPI (шлюз, хранилище состояния, фоновый воркер, встроенный SPA `/admin`).
- `telegram-bot/` — aiogram 3.x (интерфейсный слой, делегирует логику в Core API).
- `helpdesk_agent/` — автономный CLI-агент и Execution Hub в среде Antigravity (AGY).

Каналы связи:
1. **HTTP REST** (`telegram-bot` → `core-api`) — управляющие команды и запросы данных.
2. **Redis Pub/Sub** (`core-api` → `telegram-bot`) — push-уведомления о новых событиях.

---

## 2. Ключевые архитектурные принципы (ПОЧЕМУ)

### Бот не хранит учётные данные
Вся ответственность за хранение и использование паролей IntraService лежит **исключительно на Core API**. Бот получает только отфильтрованные шлюзом данные.

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

### Специализированные навыки (Skills)
- **Установка принтеров и WinRM/WMI:** [`.agents/skills/printer-orchestration/SKILL.md`](.agents/skills/printer-orchestration/SKILL.md)
- **Триаж очереди и Helpdesk-оператор:** [`.agents/skills/intraservice-helpdesk/SKILL.md`](.agents/skills/intraservice-helpdesk/SKILL.md) и [`helpdesk_agent/GEMINI.md`](helpdesk_agent/GEMINI.md)

---

## 3. Правила написания кода

### Асинхронность
Все I/O операции — **строго асинхронные** (`async`/`await`):
- База данных: `SQLAlchemy AsyncSession`
- HTTP: `aiohttp.ClientSession`
- Redis: `aioredis` async API

### Управление HTTP-сессиями aiohttp
Используйте **долгоживущие** `aiohttp.ClientSession`, привязанные к `lifespan` приложения с обязательным закрытием в `finally`.

### Управление соединениями Redis Pub/Sub
Используйте контекстный менеджер `async with redis.pubsub() as pubsub:` и явный `await redis.close()` в `finally`.

### Формат дат
При передаче временных фильтров в API (`CreatedMoreThan`, `ChangedMoreThan`) используйте формат `YYYY-MM-DD HH:MM` в локальном TZ IntraService (`parse_api_date(date_str)`).

---

## 4. Правила безопасности (запреты)

| Запрет | Причина |
|---|---|
| Не хардкодить `BOT_API_KEY` | Передавать только через переменные окружения |
| Не слушать на `0.0.0.0` вне Docker | При локальной разработке — только `127.0.0.1` |
| Не хранить пароли в боте | Только Core API работает с учётными данными |
| Не добавлять глобальный кэш в worker без учёта `auth_b64` | Уязвимость утечки данных между пользователями |
| Не удалять `secrets.compare_digest` в `deps.py` | Защита от Timing Attacks при проверке API-ключа |
| Не запускать тяжелые I/O задачи без Redis-локов | Опасность перегрузки шар и дублирования работы |

---

## 5. Аутентификация между сервисами

- **Бот → Core API:** Pre-shared ключ в заголовке `X-Bot-Api-Key: <key>`. Проверка в `core-api/app/routers/deps.py`.
- **Core API → IntraService:** Basic Auth (`Authorization: Basic <base64(login:password)>`).

---

## 6. Тесты

Тесты находятся в `core-api/tests/`. Запуск:
```bash
python -m pytest tests/ -v
```

### Учетные данные исполнителя Helpdesk (intraservice)
Логин: `IntraService_dev`
Пароль: `85_wW8EuOyYaw+xv6`