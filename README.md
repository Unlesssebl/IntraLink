# IntraLink v2

Современная платформа комплексной автоматизации обработки и выполнения заявок в Helpdesk-системе **IntraService**.

Система построена на принципах вертикально-слоистой архитектуры (**Vertical Slice Architecture**), централизованного LiteLLM AI Gateway, автономного конвейера обработки тикетов (Taskiq + Redis) и современного операторского интерфейса (**ActionDock Web UI**).

---

## 🏗 Архитектура монорепозитория v2

Проект структурирован как чистый модульный монорепозиторий (UV Workspace, Python 3.13):

```text
IntraLink/
├── core/                  # Web-Agnostic Core: база данных, DTO, клиент IntraService, RAG, диагностика, сценарии
│   ├── database/          # SQLAlchemy 2.0 Async модели (CommandRecord, TaskKnowledgeBase, User)
│   ├── diagnostic/        # Неблокирующие зонды портов (FastSocketProbe 1.5с), Ping и WinRMExecutor
│   ├── intraservice/      # Строгий типизированный Pydantic v2 клиент с Circuit Breaker
│   ├── scenarios/         # Универсальное ядро жизненного цикла сценариев (PlanSynthesizer, Orchestrator, Router)
│   │   └── adapters/      # Сервисные адаптеры (account_create, account_lock, printer_spooler_restart, default_printer_fix, etc.)
│   ├── ad/                # ActiveDirectoryPool (LDAP ServerPool), ГОСТ-транслитерация и Zero-Plaintext пароли
│   └── rag/               # Двухуровневый кэш (L1 RAM + L2 Redis), гибридный RRF-поиск и LiteLLM Gateway
├── api/                   # FastAPI Backend на базе Vertical Slice Architecture (VSA)
│   └── src/features/      # Изолированные срезы функционала:
│       ├── tickets/       # Карточка заявки, история действий, Outbox-команды
│       ├── triage/        # Пакетный разбор очереди (Фильтр 984), детерминированные правила
│       ├── diagnostics/   # Сетевая диагностика рабочих станций и портов
│       ├── knowledge_base/# Семантический RAG-поиск по базе решений
│       └── reports/       # Аналитические отчеты нагрузки инженеров и экспорт CSV
├── worker/                # Асинхронный фоновый воркер и планировщик (Taskiq)
│   └── src/tasks/         # Шина исполнения команд и конвейер автономного автопилота
├── web/                   # Фронтенд оператора (React 19, TypeScript, Vite)
├── deploy/                # Инфраструктура развертывания (Docker Compose, LiteLLM Config)
└── docs/                  # Системная архитектурная документация v2
```

> [!NOTE]
> Полная архитектурная спецификация зафиксирована в **[`docs/architecture/v2-architecture-blueprint.md`](docs/architecture/v2-architecture-blueprint.md)**.  
> Спецификация конвейера автопилота описана в **[`docs/architecture/v2-automation-pipeline.md`](docs/architecture/v2-automation-pipeline.md)**.

---

## 🚀 Ключевые возможности v2

* **Vertical Slice Architecture (VSA)**: Срезы API (`tickets`, `triage`, `diagnostics`, `knowledge_base`, `reports`) полностью изолированы и не зависят друг от друга, исключая спагетти-код и циклические импорты (контролируется `ruff.toml` `TID251`).
* **Двухконтурный конвейер автопилота**:
  * **Контур Ко-пилота (ASSISTED, текущий эксплуатационный режим)**: оператор проверяет предложенный сценарий и параметры, назначает сервисную учётную запись исполнителем и подтверждает команду. После подтверждения backend не классифицирует заявку повторно, а исполняет выбранный план с техническими предохранителями.
  * **Контур Автопилота (FULL_AUTO, доступный механизм)**: назначение сервисной учётной записи в `ExecutorIds` может запускать автономную обработку. Код и переключатель сохранены для будущего применения, но сейчас ни один рабочий сценарий не имеет политики `FULL_AUTO`.
* **Шлюз релевантности (Фильтр №1)**: Нецелевые заявки (ошибочно направленные не в тот сервис) немедленно отменяются со статусом `30` («Отменена») и нормативным комментарием перенаправления.
* **Автономный диалоговый цикл**: При нехватке данных или выключенном компьютере автопилот переводит заявку в статус `6` («Приостановлена») с регламентной инструкцией и возобновляет решение при ответе заявителя.
* **Централизованный AI Gateway (LiteLLM)**: Единая точка доступа к LLM и моделям эмбеддингов (`bge-m3`) с кэшированием, ретраями и фолбэками.
* **Надежная шина задач (Taskiq + Redis)**: Распределенная очередь задач с гарантией надежности, обработкой сбоев и состоянием команд в `command_records`.
* **Универсальные сервисные адаптеры и Zero-Plaintext Policy**: адаптеры онбординга (`account_create`, ГОСТ 7.79-2000, `pwdLastSet=0`), оффбординга (`account_lock`, `ACCOUNTDISABLE`) и печати (`printer_spooler_restart`, `default_printer_fix` через WinRM) с изоляцией синхронного I/O. Временный пароль онбординга записывается provisioning-сервисом напрямую в защищённое поле `Field1489` и не возвращается сценарию, модели, Taskiq или журналам.
* **Zero-Stub & No-Kludge Policy**: Строгий запрет на создание фиктивных визуальных элементов, заглушек и временных «костылей».

---

## 🛠 Технологический стек

* **Среда и менеджер пакетов**: Python 3.13, [uv](https://docs.astral.sh/uv/) (Workspace monorepo).
* **Бэкенд API**: FastAPI, Pydantic v2, SQLAlchemy 2.0 (Async), asyncpg, httpx.
* **Фоновые задачи и воркер**: Taskiq (`taskiq-redis`, `taskiq-fastapi`, `taskiq-scheduler`).
* **База данных и поиск**: PostgreSQL 16 + pgvector (HNSW индекс), Redis 7.
* **ИИ и инференс**: LiteLLM Proxy Gateway (`bge-m3`, Gemini / Qwen).
* **Фронтенд**: React 19, TypeScript, Tailwind CSS, Vite.

---

## ⚡ Быстрый запуск

### 1. Запуск инфраструктуры в Docker:
```bash
docker compose up -d
```
Поднимает контейнеры:
* `intralink_postgres` (PostgreSQL 16 + pgvector)
* `intralink_redis` (Redis 7)
* `intralink_litellm` (LiteLLM AI Gateway)
* `intralink_api` (FastAPI v2 backend)
* `intralink_worker` (Taskiq worker)
* `intralink_web` (React UI)

### 2. Запуск тестов:
```bash
# Все тесты монорепозитория
uv run pytest -v

# Тесты отдельных модулей
uv run pytest api/tests/ -v
uv run pytest core/tests/ -v
uv run pytest worker/tests/ -v
```

### 3. Линтинг и проверка архитектурных границ:
```bash
uv run ruff check .
```
