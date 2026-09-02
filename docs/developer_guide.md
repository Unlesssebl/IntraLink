# 🤖 Руководство разработчика (AI Agent Developer Guide)

Этот документ является настольной книгой для AI-агентов и разработчиков, работающих над кодовой базой **IntraLink**. В нем зафиксированы архитектурные инварианты, правила межсервисного взаимодействия, команды отладки и стандарты безопасности.

---

## 🗺️ 1. Карта монорепозитория

Проект организован как модульный монорепозиторий со следующими сервисами и инструментами:

```text
intraservice-tg-bot/
├── core-api/          # FastAPI Gateway: база данных (PostgreSQL+pgvector), фоновый воркер, веб-панель администратора
├── telegram-bot/      # Telegram Bot (aiogram 3.x): интерфейс пользователя и подписчик Redis Pub/Sub
├── intra-web/          # Фронтенд веб-панели администратора (React 19, Vite, Tailwind CSS v4)
├── helpdesk-cli/    # Инженерный кокпит в AGY: Rule Engine, FastEmbed RAG, AD (Wi-Fi) и WinRM (принтеры)
├── tools/             # Скрипты индексации драйверов печати
├── docs/              # Фундаментальная документация проекта
└── .agents/skills/    # Навыки Antigravity для автоматизации задач
```

---

## 🔒 2. Ключевые архитектурные инварианты (ПОЧЕМУ так устроено)

### 1. Бот не хранит пароли и секреты
* **Правило:** Бот (`telegram-bot/`) является тонким интерфейсным слоем. Вся ответственность за хранение зашифрованных паролей IntraService и доступ к API лежит на `core-api/`.
* **Причина:** Изоляция секретов. При компрометации бота злоумышленник не получает доступа к корпоративным учетным записям.

### 2. Часовые пояса и форматы дат
* **Правило:** Внутри БД и Redis все метки времени (`last_check_time`, события) хранятся в **UTC**.
* **Причина:** Перед отправкой запроса к IntraService API дата конвертируется в локальный часовой пояс (`settings.INTRASERVICE_TZ`, по умолчанию `Europe/Moscow`). Формат для API: `YYYY-MM-DD HH:MM`. Не передавайте UTC-время напрямую в IntraService!

### 3. WMI Bootstrap для WinRM
* **Правило:** Модуль установки принтеров динамически включает службу WinRM на рабочей станции пользователя через WMI (DCOM/RPC) непосредственно перед установкой принтера, а после завершения (успешного или аварийного) **гарантированно отключает её**.
* **Причина:** Минимизация поверхности сетевых атак в домене и отсутствие необходимости глобального распространения GPO.

### 4. UPN-форматирование доменных учетных записей
* **Правило:** При обращении к SMB/WinRM логин всегда приводится к UPN-формату: `username@domain.com` (вместо `domain\username`).
* **Причина:** Устранение ошибок `STATUS_LOGON_FAILURE` при аутентификации из Linux-контейнеров в Active Directory.

### 5. Сетевой Fail-Fast перед подтверждением
* **Правило:** Перед переводом заявки в статус ожидания подтверждения (`WAITING_APPROVAL`) микросервис обязательно валидирует пинг до ПК и принтера. Если хост недоступен — задача сразу переводится в «Требует уточнения» с понятным комментарием.

### 6. Распределенные блокировки (Distributed Locks)
* **Правило:** Любые тяжелые I/O-операции (индексация шар драйверов, массовая выгрузка) защищаются Redis-локами с TTL (например, `indexer:status`).

---

## 📡 3. Межсервисные коммуникации

```mermaid
flowchart LR
    subgraph Clients
        TG["Telegram Bot (aiogram)"]
        Web["Admin Web UI (intra-web)"]
        AGY["Helpdesk Agent (AGY / CLI)"]
    end

    subgraph Core Infrastructure
        Core["Core API (FastAPI + Worker)"]
        Redis[("Redis Streams / PubSub")]
        PG[("PostgreSQL + pgvector")]
    end

    subgraph External Systems
        IS["IntraService REST API"]
        AD["Active Directory / WinRM / SMB"]
    end

    TG -->|HTTP REST (X-Bot-Api-Key)| Core
    Web -->|HTTP REST (JWT Session)| Core
    AGY -->|Basic Auth / direct API| IS
    AGY -->|pgvector query| PG
    AGY -->|AD / WinRM / SMB| AD

    Core -->|Basic Auth| IS
    Core -->|SQLAlchemy Async| PG
    Core -->|Publish Events (task_events)| Redis

    Redis -->|Push Notifications| TG
```

### Каналы Redis:
* `stream:intraservice_events` — поток событий очереди (Redis Streams) с Consumer Groups.
* `printer_actions` — задачи на установку принтеров.
* `printer_job_logs:{job_id}` — поток логов реального времени для SSE веб-панели.
* `task_events` — поток событий для push-уведомлений Telegram-бота.

---

## 🛠 4. Инструменты автономной диагностики инженера (`helpdesk.py`)

Для автономной проверки и управления очередью заявок, RAG-поиска и инфраструктурных действий используется CLI-инструмент [`helpdesk-cli/helpdesk.py`](../services/helpdesk-cli/README.md):

```bash
# 1. Пакетный разбор очереди (триаж)
uv run python helpdesk-cli/helpdesk.py batch --limit 10

# 2. Семантический RAG-поиск по базе решений
uv run python helpdesk-cli/helpdesk.py search-kb "проблема с принтером"

# 3. Сетевая экспресс-диагностика хоста (Ping, DNS, SMB, WinRM)
uv run python helpdesk-cli/helpdesk.py diag "PC-NAME-OR-IP"

# 4. Выдача Wi-Fi доступа (AD WLAN-WORKNET)
uv run python helpdesk-cli/helpdesk.py wlan 145001

# 5. Локальная AI-суммаризация инцидента через Ollama (Qwen2.5:1.5B)
uv run python helpdesk-cli/helpdesk.py summary 145001
```

---

## 🧪 5. Тестирование и проверка качества

```bash
# Запуск всех юнит-тестов Core API:
uv run pytest core-api/tests/ -v

# Проверка сборки фронтенда intra-web:
cd intra-web && npm run build
```

---

## 📝 6. Правила написания кода для AI-агентов

1. **Строгая асинхронность:** Все сетевые и дисковые операции выполняются через `async`/`await` (`aiohttp`, `asyncpg`, `aioredis`).
2. **Сессии aiohttp:** Используйте долгоживущие сессии `aiohttp.ClientSession`, привязанные к `lifespan` сервиса. Закрывайте сессии в `finally`.
3. **Безопасность:** Не хардкодить пароли и токены. Не логировать сырые пароли пользователей.
4. **Документация:**
   - Пути в документации — **только относительные** (`docs/architecture.md`, без `file:///...`).
   - Документировать архитектурные решения и инварианты («ПОЧЕМУ»), не дублировать код внутренних эндпоинтов (для них источник правды — FastAPI OpenAPI `/docs`).
