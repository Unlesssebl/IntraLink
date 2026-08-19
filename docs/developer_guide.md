# 🤖 Руководство разработчика (AI Agent Developer Guide)

Этот документ является настольной книгой для AI-агентов и разработчиков, работающих над кодовой базой **IntraLink**. В нем зафиксированы архитектурные инварианты, правила межсервисного взаимодействия, команды отладки и стандарты безопасности.

---

## 🗺️ 1. Карта микросервисного монорепозитория

Проект организован как монорепозиторий со следующими микросервисами и инструментами:

```
intraservice-tg-bot/
├── core-api/          # FastAPI Gateway: база данных, фоновый воркер опроса, веб-панель администратора
├── bot/               # Telegram Bot (aiogram 3.x): интерфейс пользователя и слушатель Redis Pub/Sub
├── ai-worker/         # Фоновый микросервис: классификация заявок и автоответчик (RAG + pgvector + LLM)
├── printer-worker/    # Фоновый микросервис: автоустановка принтеров через WMI / WinRM / SMB
├── helpdesk_agent/    # Инструменты Helpdesk для прямого ведения очереди из среды Antigravity (AGY)
├── admin-ui/          # Фронтенд веб-панели администратора (Vite + Vue 3 / Vanilla JS)
├── debug_tools/       # CLI-утилиты для автономной отладки воркеров (LLM-as-judge)
├── tools/             # Скрипты индексации драйверов печати
├── docs/              # Единый источник документации проекта
└── .agents/skills/    # Навыки Antigravity для автоматизации задач
```

---

## 🔒 2. Ключевые архитектурные инварианты (ПОЧЕМУ так устроено)

### 1. Бот не хранит пароли и секреты
* **Правило:** Бот (`bot/`) является тонким интерфейсным слоем. Вся ответственность за хранение зашифрованных паролей IntraService и доступ к API лежит на `core-api/`.
* **Причина:** Изоляция секретов. При компрометации бота злоумышленник не получает доступа к корпоративным учетным записям.

### 2. Часовые пояса и форматы дат
* **Правило:** Внутри БД и Redis все метки времени (`last_check_time`, события) хранятся в **UTC**.
* **Причина:** Перед отправкой запроса к IntraService API дата конвертируется в локальный часовой пояс (`settings.INTRASERVICE_TZ`, по умолчанию `Europe/Moscow`). Формат для API: `YYYY-MM-DD HH:MM`. Не передавайте UTC-время напрямую в IntraService!

### 3. WMI Bootstrap для WinRM
* **Правило:** `printer-worker` динамически включает службу WinRM на рабочей станции пользователя через WMI (DCOM/RPC) непосредственно перед установкой принтера, а после завершения (успешного или аварийного) **гарантированно отключает её**.
* **Причина:** Минимизация поверхности сетевых атак в домене и отсутствие необходимости глобального распространения GPO.

### 4. UPN-форматирование доменных учетных записей
* **Правило:** При обращении к SMB/WinRM логин всегда приводится к UPN-формату: `username@domain.com` (вместо `domain\username`).
* **Причина:** Устранение ошибок `STATUS_LOGON_FAILURE` при аутентификации из Linux-контейнеров в Active Directory.

### 5. Сетевой Fail-Fast перед подтверждением
* **Правило:** Перед переводом заявки в статус ожидания подтверждения (`WAITING_APPROVAL`) микросервис `printer-worker` обязательно валидирует пинг до ПК и принтера. Если хост недоступен — задача сразу переводится в «Требует уточнения» с понятным комментарием.

### 6. Распределенные блокировки (Distributed Locks)
* **Правило:** Любые тяжелые I/O-операции (индексация шар драйверов, массовая выгрузка) защищаются Redis-локами с TTL (например, `indexer:status`).

---

## 📡 3. Межсервисные коммуникации

```mermaid
flowchart LR
    subgraph Clients
        TG["Telegram Bot (aiogram)"]
        Web["Admin Web UI"]
        AGY["Antigravity Helpdesk Agent"]
    end

    subgraph Core Infrastructure
        Core["Core API (FastAPI)"]
        Redis[("Redis Pub/Sub")]
        PG[("PostgreSQL + pgvector")]
    end

    subgraph Autonomous Workers
        PW["Printer Worker"]
        AIW["AI Worker"]
    end

    subgraph External Systems
        IS["IntraService REST API"]
        AD["Active Directory / SMB"]
    end

    TG -->|HTTP REST (X-Bot-Api-Key)| Core
    Web -->|HTTP REST (JWT Session)| Core
    AGY -->|Basic Auth / direct API| IS
    AGY -->|pgvector query| PG

    Core -->|Basic Auth| IS
    Core -->|SQLAlchemy Async| PG
    Core -->|Publish Events| Redis

    Redis -->|Push Notifications| TG
    Redis -->|Dispatch Jobs| PW
    Redis -->|Dispatch Classify| AIW

    PW -->|WinRM / SMB / WMI| AD
    PW -->|REST / Redis Logs| Core
```

### Каналы Redis:
* `printer_actions` — задачи на установку принтеров.
* `printer_job_logs:{job_id}` — поток логов реального времени для SSE веб-панели.
* `task_events` — поток событий для push-уведомлений Telegram-бота.

---

## 🛠 4. Инструменты автономной отладки для AI-агентов (`debug_tools`)

AI-агенты могут использовать пакет `debug_tools` для автономной проверки своей работы (подход **LLM-as-judge**):

```bash
# 1. Отладка AI-классификатора (показывает RAG-совпадения в pgvector)
python -m debug_tools.cli ai --task-id 139022

# 2. Отладка парсера принтеров и сетевых стратегий
python -m debug_tools.cli printer --task-id 139022

# 3. Батч-тестирование на выборке для оценки точности (Accuracy)
python -m debug_tools.cli printer --batch 133609,141205,145001

# 4. Поиск зависших в обработке задач
python -m debug_tools.cli stuck

# 5. Анализ состояния Redis
python -m debug_tools.cli redis
```

---

## 🧪 5. Тестирование и проверка качества

```bash
# Запуск всех юнит-тестов Core API:
uv run pytest core-api/tests/ -v

# ПроверкаHelpdesk I/O утилиты:
uv run python helpdesk_agent/helpdesk_tool.py queue --limit 3

# Проверка семантического поиска RAG:
uv run python helpdesk_agent/helpdesk_tool.py search-kb "проблема с печатью"
```

---

## 📝 6. Правила написания кода для AI-агентов

1. **Строгая асинхронность:** Все сетевые и дисковые операции выполняются через `async`/`await` (`aiohttp`, `asyncpg`, `aioredis`).
2. **Сессии aiohttp:** Используйте долгоживущие сессии `aiohttp.ClientSession`, привязанные к `lifespan` сервиса. Закрывайте сессии в `finally`.
3. **Безопасность:** Не хардкодить пароли и токены. Не логировать сырые пароли пользователей.
4. **Документация:**
   - Пути в документации — **только относительные** (`docs/architecture.md`, без `file:///...`).
   - При добавлении нового микросервиса сразу создавать заглушку `docs/services/<name>/README.md`.
