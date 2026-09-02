# IntraLink

Современная платформа комплексной автоматизации обработки и выполнения заявок в Helpdesk-системе **IntraService**.

Система сочетает детерминированный движок правил (Rule Engine), централизованный семантический RAG-поиск (pgvector + FastEmbed), многоконтурную безопасность данных (Dual-Circuit Zero Trust DLP), интеграционный API-шлюз и модули прямого исполнения в инфраструктуре Windows (Active Directory, WinRM, сетевая печать).

---

## 🏗 Архитектура монорепозитория

Проект построен по модульной сервисной архитектуре с единым источником правды (**Single Source of Truth**) в `core-api`:

```text
IntraLink/
├── core-api/          # Единый шлюз состояния: FastAPI, PostgreSQL+pgvector, AI Hub, хостинг /admin
│   └── app/poller.py  # Автономный демон опроса IntraService с Leader Lock в Redis
├── execution-worker/  # Фоновый Windows Headless Daemon исполнения задач (AD WLAN, WinRM, SMB)
├── helpdesk-cli/      # Машинный инструментарий и SDK исключительно для AI-агента Antigravity (AGY)
├── shared/            # Единый пакет общих алгоритмов (SSOT): normalizer, diagnostics, json_utils
├── telegram-bot/      # Мобильный пейджер инженера и HITL-согласования (aiogram 3.x, Redis Streams)
├── intra-web/         # Фронтенд веб-панели администратора (React 19, Vite, Tailwind CSS v4)
├── tools/             # Скрипты индексации драйверов печати
└── docs/              # Системная документация проекта
```

> [!NOTE]
> Полные архитектурные схемы, контуры безопасности DLP и Data Flow описаны в **[`docs/architecture.md`](docs/architecture.md)**.  
> Архитектурные инварианты («ПОЧЕМУ») и стандарты разработки зафиксированы в **[`docs/developer_guide.md`](docs/developer_guide.md)**.

---

## 🚀 Ключевые возможности

* **Защищенный API Gateway (Core API)**: Единая точка доступа к REST API IntraService, Fernet-шифрование учетных записей, централизованный Rule Engine и двухэтапный Hybrid RAG.
* **Изолированный Poller с Leader Lock**: Независимый демон фонового опроса очереди от сервисного аккаунта под защитой распределенного замка (`lock:poller_leader`, TTL 15s) для предотвращения Split-Brain при масштабировании.
* **Гарантированная доставка событий (Redis Streams)**: Поток `stream:intraservice_events` с Consumer Groups, подтверждением `XACK` и периодическим перехватом зависших сообщений `XAUTOCLAIM` (At-Least-Once).
* **Исполнение в Windows-домене (Execution Worker)**: Фоновая обработка очереди `stream:execution_queue` — выдача сетевого доступа в AD (`WLAN-WORKNET`), создание учетных записей, удаленная установка принтеров через WinRM/CIM с поддержкой Dead-Letter Queue (DLQ).
* **Многоконтурная безопасность данных (Zero Trust DLP)**:
  * 🔴 **RED Zone (On-Prem)**: пароли и заявки СБ обрабатываются локально (Ollama Qwen2.5 / bge-m3).
  * 🟡 **YELLOW Zone (Sanitized Cloud)**: ПДн, IP и имена хостов маскируются токенами через Redis PII Vault перед вызовом облачных моделей.
  * 🟢 **GREEN Zone (Cloud Direct)**: открытые регламенты и технические вопросы.
* **Инструментарий AI-агента (AGY Toolset)**: Управление очередью прямо из диалога с AI-ассистентом через слэш-команды (`/triage`, `/task`, `/diag`, `/screen`, `/kb`, `/sync`, `/redirect`).
* **Мобильный пейджер (Telegram Bot)**: Моментальные уведомления, просмотр активных заявок инженера и кнопки подтверждения операций (Human-in-the-Loop).
* **Встроенный Web SPA (`/admin`)**: React 19 интерфейс мониторинга очереди 1-й линии, телеметрии хостов и управления базой знаний.

---

## 🛠 Технологический стек

* **Бэкенд**: Python 3.11+, FastAPI, SQLAlchemy 2.0 (Async), asyncpg, aiohttp, orjson.
* **База данных и поиск**: PostgreSQL 16, pgvector (HNSW), FastEmbed (ONNX MiniLM-L12 + bge-reranker).
* **Шина сообщений и кэш**: Redis 7 (Streams, Consumer Groups, Distributed Locks, PII Vault).
* **ИИ и инференс**: Ollama (Qwen2.5:1.5B, bge-m3), LiteLLM Proxy / Gemini Cloud.
* **Фронтенд**: React 19, TypeScript, Tailwind CSS v4, Vite 6 (`vite-plugin-singlefile`).
* **Бот**: aiogram 3.x, aiohttp.
* **Инфраструктура исполнения**: PowerShell, pywinrm, WMI / CIM over WinRM:5985.

---

## ⚡ Быстрый запуск

### 1. Запуск основной инфраструктуры в Docker:
```bash
docker compose up -d
```
Поднимает контейнеры: `postgres` (pgvector), `redis`, `ollama`, `core-api` (Gateway + Admin SPA) и `poller` (фоновый опрос с Leader Lock).

* Веб-панель управления: `http://localhost:8000/admin`
* Интерактивная документация API: `http://localhost:8000/docs`

### 2. Запуск Telegram-бота (опционально):
```bash
docker compose --profile with-bot up -d telegram-bot
```

### 3. Запуск воркера исполнения в Windows:
```powershell
$env:REDIS_URL = "redis://localhost:6379/0"
$env:CORE_API_URL = "http://localhost:8000/api/v1"
$env:BOT_API_KEY = "<bot-api-key>"

uv run python execution-worker/worker.py
```

### 4. Взаимодействие с AI-агентом Antigravity (AGY):
Инженеры работают с очередью через диалог с AI-ассистентом:
* `/triage` — пакетный разбор стопки заявок со сводной матрицей;
* `/task <ID>` — детальная карточка инцидента с RAG-поиском решений;
* `/diag <ХОСТ>` — сетевая экспресс-диагностика доступности ПК;
* `/screen <ID>` — визуальный анализ скриншотов из вложений;
* `/kb <запрос>` — семантический поиск по базе исторических решений;
* `/sync` — синхронизация закрытых заявок в векторную базу знаний.

CLI-справка по всем командам инструментария:
```bash
uv run python helpdesk-cli/helpdesk.py --help
```
