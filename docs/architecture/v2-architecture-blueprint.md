# Архитектурный манифест IntraLink v2: Vertical Slice Architecture & LiteLLM

> **Статус:** Принято к реализации  
> **Ветка:** `feat/v2-vertical-slice-architecture`  
> **Связанные документы:** [ADR 0004: Migration to Vertical Slice Architecture](file:///docs/adr/0004-vertical-slice-architecture-and-litellm.md)

---

## 🧭 1. Концепция и ключевые архитектурные инварианты

Новая архитектура IntraLink решает фундаментальную проблему проекта: **накопление технического долга, овер-инжиниринга и горизонтальной размазанности кода**.

### Четыре столпа архитектуры v2:

1. **Вертикальные срезы (Vertical Slice Architecture — VSA):**
   * Код организуется вокруг **бизнес-фичей**, а не вокруг технических слоев (`routers`, `services`, `models`).
   * Каждый срез самодостаточен: HTTP-роут, Pydantic-схемы, бизнес-логика и запросы к БД лежат в одной директории фичи.
   * **Правило нулевой связанности:** Срезы **не импортируют** внутренности друг друга. Срез `tickets` не вызывает функции из среза `reports`. Общая доменная логика выносится в `core/`.

2. **Единый интерфейс (Web-First):**
   * На текущем этапе проект ориентирован строго на единый клиент — **Web-интерфейс** (`web/`).
   * Клиентские модули фронтенда зеркалят вертикальные срезы бэкенда (`features/tickets`, `features/triage`, `features/knowledge-base`).
   * Вторичные интерфейсы (CLI, MCP для AI-агентов) заморожены и будут подключаться позже поверх чистого ядра `core/`.

3. **LiteLLM Gateway (Инфраструктурный AI-шлюз):**
   * Вся работа с LLM вынесена из кода бэкенда в отдельный прокси-контейнер `litellm`.
   * Бэкенд общается со шлюзом через стандартный клиент `AsyncOpenAI`.
   * Модели, локальный инференс Ollama/vLLM, облачные фолбэки (DeepSeek, OpenAI), кэширование в Redis и лимиты настраиваются декларативно в `deploy/litellm_config.yaml`.

4. **Радикальная очистка (Zero Dead Code):**
   * Все рудименты, заброшенные эксперименты, старые дублирующиеся команды (`commands` vs `commands_v2`) и утилиты верхнего уровня (`tools`, `backups`, `desktop-companion`) исключаются из активного дерева.
   * Вся история изменений сохраняется в Git.

---

## 🗂️ 2. Карта директорий репозитория (Repository Layout)

```text
intralink/
│
├── web/                               # 🖥️ ФРОНТЕНД (Клиентский интерфейс)
│   ├── src/
│   │   ├── features/                  # UI-модули по фичам (зеркалят бэкенд)
│   │   │   ├── tickets/               # Список заявок, фильтры, карточка
│   │   │   ├── triage/                # Дашборд экспресс-разбора 1-й линии
│   │   │   ├── knowledge-base/        # Семантический поиск и ответы RAG
│   │   │   ├── diagnostics/           # Статус рабочих станций, ping, порты
│   │   │   └── reports/               # Аналитические отчеты нагрузки
│   │   ├── shared/                    # UI-компоненты (кнопки, модалки, таблицы, API-клиент)
│   │   ├── App.vue / App.tsx
│   │   └── main.ts
│   ├── package.json
│   └── vite.config.ts
│
├── api/                               # ⚙️ БЭКЕНД: FastAPI НА ВЕРТИКАЛЬНЫХ СРЕЗАХ
│   ├── src/
│   │   ├── core/                      # Системная инфраструктура (чистый тех-стек)
│   │   │   ├── config.py              # Pydantic Settings v2 (переменные окружения)
│   │   │   ├── db.py                  # SQLAlchemy 2.0 Async Engine и сессии
│   │   │   ├── redis.py               # Пул соединений Redis
│   │   │   ├── ai.py                  # Клиент к LiteLLM Proxy (AsyncOpenAI)
│   │   │   └── security.py            # Аутентификация, токены, права
│   │   │
│   │   ├── features/                  # 🍰 САМОДОСТАТОЧНЫЕ ВЕРТИКАЛЬНЫЕ СРЕЗЫ
│   │   │   ├── tickets/               # Заявки и операции над ними
│   │   │   ├── triage/                # Автоматический разбор и классификация очереди
│   │   │   ├── knowledge_base/        # RAG, семантический поиск, pgvector
│   │   │   ├── diagnostics/           # Сетевая диагностика ПК (Ping, SMB, WinRM, AD)
│   │   │   └── reports/               # Аналитика нагрузки отделов
│   │   │
│   │   └── main.py                    # Инициализация FastAPI, CORS, роутеры срезов
│   ├── tests/
│   └── Dockerfile
│
├── worker/                            # 🔨 ФОНОВЫЙ ВОРКЕР (Асинхронные задачи)
│   ├── src/
│   │   ├── tasks/                     # Задачи воркера:
│   │   │   ├── printers.py            # Оркестрация принтеров (WMI/WinRM)
│   │   │   ├── sync_kb.py             # Синхронизация закрытых тикетов в векторную БД
│   │   │   └── ad_actions.py          # Сброс паролей, разблокировка учеток в AD
│   │   └── main.py                    # Рантайм воркера (ARQ / Taskiq)
│   ├── tests/
│   └── Dockerfile
│
├── core/                              # 📦 ОБЩИЙ ДОМЕН (Web-Agnostic Core)
│   ├── intraservice/                  # Клиент к IntraService API (HTTPX, пагинация, DTO)
│   ├── rag/                           # FastEmbed логика, модели векторизации
│   ├── diagnostic/                    # Низкоуровневые сетевые сокеты, Ping, LDAP-клиент
│   └── database/                      # Базовые модели SQLAlchemy, миграции Alembic
│
├── deploy/                            # 🐳 ИНФРАСТРУКТУРА
│   ├── docker-compose.yml             # Postgres (pgvector), Redis, LiteLLM, API, Worker
│   ├── litellm_config.yaml            # Декларативный конфиг моделей LiteLLM
│   └── .env.example
│
├── pyproject.toml                     # Единый файл управления зависимостями (uv workspaces)
└── ruff.toml                          # Линтер, форматтер и запрет циклических импортов
```

---

## 🍰 3. Анатомия вертикального среза (`api/src/features/*`)

Каждая папка внутри `api/src/features/` — это мини-приложение, отвечающее за конкретный бизнес-домен.

### Пример: Срез `knowledge_base` (RAG)
```text
api/src/features/knowledge_base/
├── router.py      # FastAPI APIRouter: /api/v2/kb/search, /api/v2/kb/ask
├── schemas.py     # Pydantic DTO запросов и ответов (SearchQuery, SolutionResponse)
├── service.py     # Бизнес-логика: сборка контекста, вызов LiteLLM
├── repo.py        # SQL-запросы с оператором косинусного расстояния (<=>) в pgvector
└── prompts.py     # Шаблоны промптов для RAG-синтеза
```

### Пример кода эндпоинта среза:
```python
# api/src/features/knowledge_base/router.py
from fastapi import APIRouter, Depends
from api.src.core.ai import get_ai_client
from .schemas import SearchQuery, SolutionResponse
from .service import KnowledgeBaseService

router = APIRouter(prefix="/kb", tags=["Knowledge Base"])

@router.post("/search", response_model=SolutionResponse)
async def search_solutions(
    query: SearchQuery,
    service: KnowledgeBaseService = Depends()
):
    return await service.find_and_synthesize(query.text)
```

---

## 🤖 4. Инфраструктура AI: LiteLLM Proxy

Вместо кастомных спагетти-оберток в коде бэкенда (`ai_synthesis.py`, `response_guard.py` и т.д.), весь трафик к моделям идет через единый шлюз.

### `deploy/docker-compose.yml` (Фрагмент):
```yaml
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    ports:
      - "4000:4000"
    volumes:
      - ./litellm_config.yaml:/app/config.yaml
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/intraservice
      - REDIS_URL=redis://redis:6379/1
    command: ["--config", "/app/config.yaml", "--port", "4000"]
```

### `deploy/litellm_config.yaml`:
```yaml
model_list:
  # Быстрая модель для классификации и триажа заявок
  - model_name: helpdesk-fast
    litellm_params:
      model: ollama/qwen2.5:7b
      api_base: http://host.docker.internal:11434
      max_tokens: 512
      temperature: 0.1

  # Рассуждающая модель для RAG-синтеза и сложных решений
  - model_name: helpdesk-reasoning
    litellm_params:
      model: openai/deepseek-reasoner
      api_key: os.environ/DEEPSEEK_API_KEY
      fallbacks: ["helpdesk-fast"]

litellm_settings:
  cache: true
  cache_type: redis
  cache_host: redis
  cache_port: 6379
  cache_ttl: 86400  # Кэширование одинаковых ответов на 24 часа
```

### Использование в бэкенде (`api/src/core/ai.py`):
```python
from openai import AsyncOpenAI
from api.src.core.config import settings

# Единый клиент для всего бэкенда
ai_client = AsyncOpenAI(
    base_url=settings.LITELLM_BASE_URL, # "http://litellm:4000/v1"
    api_key=settings.LITELLM_API_KEY,   # "sk-intralink"
)
```

---

## 🧹 5. Матрица сортировки кодовой базы: Keep / Merge / Kill

При переносе функционала из `core-api/app` и `execution-worker` действует строгая классификация:

| Модуль старого проекта | Статус | Куда переносится в v2 | Обоснование |
| :--- | :---: | :--- | :--- |
| `intraservice.py`, `service_catalog.py` | 🟢 **KEEP** | `core/intraservice/` | Фундаментальный API-клиент к Helpdesk. Очистить от дублирования `/api`. |
| `rag.py`, `knowledge_base/` | 🟢 **KEEP** | `api/src/features/knowledge_base/` | Поиск по `task_knowledge_base` (pgvector). Удалить устаревшие эвристики. |
| `active_directory.py`, `host_telemetry.py` | 🟢 **KEEP** | `api/src/features/diagnostics/` | Сетевые проверки (Ping, SMB:445, WinRM:5985, LDAP). |
| `reports.py`, `routers/reports.py` | 🟢 **KEEP** | `api/src/features/reports/` | Расчет метрик нагрузки инженеров с кэшированием в Redis. |
| `ai_suggestions.py`, `ai_synthesis.py` | 🟡 **MERGE** | `api/src/features/triage/` | Схлопнуть в 1 вызов через LiteLLM с Pydantic Structured Outputs. |
| `commands.py` + `commands_v2.py` | 🟡 **MERGE** | `api/src/features/tickets/actions.py` | Удалить `v1`. Оставить только чистый идемпотентный Outbox-паттерн v2. |
| `decision_compiler`, `decision_envelope`, `decision_journal` | 🟡 **MERGE** | `api/src/features/triage/pipeline.py` | Избыточный овер-инжиниринг. Заменить на прозрачный линейный пайплайн. |
| `ticket_runs.py`, `ticket_run_runner.py` | 🟡 **MERGE** | `worker/src/tasks/` | Перенести логику очередей в чистый воркер. |
| `truthfulness_guard`, `response_guard`, `safety` | 🔴 **KILL** | Заменяется на LiteLLM валидацию | 3 слоя самодельных фильтров заменяются системным промптом и JSON-схемой. |
| `desktop-companion/`, `helpdesk-cli/` | 🔴 **KILL** | Git History | Заморожены на этапе Web-First. При необходимости будут возрождены в `interfaces/`. |
| `tools/`, `backups/`, скрипты в корне | 🔴 **KILL** | Удаление из git | Мусор и рудименты, не относящиеся к коду системы. |

---

## 🛡️ 6. Контроль чистоты архитектуры (Linter Rules)

Для предотвращения повторного превращения проекта в кашу настраивается `ruff.toml`:

```toml
[lint]
select = ["E", "F", "I", "B", "TID"]

# Запрет перекрестных импортов между вертикальными срезами
[lint.flake8-tidy-imports.banned-api]
"api.src.features.tickets".msg = "Срезы не должны импортировать друг друга! Выносите общий код в core/."
"api.src.features.reports".msg = "Срезы не должны импортировать друг друга! Выносите общий код в core/."
"api.src.features.triage".msg = "Срезы не должны импортировать друг друга! Выносите общий код в core/."
```

---

## 🚀 7. План перехода (Execution Roadmap)

1. **Фаза 1 (Фундамент):**
   * Создание корневого `pyproject.toml` (uv) и каркаса каталогов (`web/`, `api/`, `worker/`, `core/`, `deploy/`).
   * Настройка `deploy/docker-compose.yml` (Postgres + pgvector, Redis, LiteLLM) и `deploy/litellm_config.yaml`.
2. **Фаза 2 (Перенос чистого ядра):**
   * Перенос `core/intraservice` (типизированный клиент IntraService API).
   * Перенос `core/database` (SQLAlchemy 2.0, подключение к БД и векторной таблице).
3. **Фаза 3 (Сборка срезов API):**
   * Срез `features/tickets` (чтение заявок, карточка).
   * Срез `features/knowledge_base` (RAG через LiteLLM + pgvector).
   * Срез `features/diagnostics` (сеть и хосты).
   * Срез `features/triage` (пакетный авторазбор).
4. **Фаза 4 (Фронтенд и финализация):**
   * Подключение компонентов `web/src/features/*` к новым эндпоинтам API.
   * Удаление старых директорий `core-api`, `execution-worker` и рудиментов.
