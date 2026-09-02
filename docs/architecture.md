# Архитектура проекта IntraLink (intraservice-tg-bot)

Данный документ описывает целевую архитектуру системы, её ключевые компоненты, потоки данных и принципы взаимодействия между сервисами.

> **Главная цель проекта** — создание единой, надежной платформы автоматизации обработки и выполнения заявок в IntraService. Система сочетает детерминированный движок правил (Rule Engine), централизованный семантический RAG-поиск (FastEmbed/Gemini + pgvector) и модули прямого исполнения задач в инфраструктуре Windows (Active Directory, WinRM, SMB).

---

## 🏗️ 1. Общий обзор системы

Проект построен по **модульной сервисной архитектуре с единым источником правды (Single Source of Truth) в Core API**:

| Сервис / Модуль | Стек технологий | Роль в системе |
|---|---|---|
| **Core API Gateway** | FastAPI, SQLAlchemy 2.0 (async), pgvector, Redis, ldap3 | Единый шлюз состояния, движок правил (Rule Engine), AI Hub (DLP/PII Vault), семантический RAG, прямое управление AD по LDAPS (636), хостинг SPA (`/operator-panel` и `/admin`) |
| **Poller Service** | Python, aiohttp, Redis Leader Lock | Автономный фоновый демон опроса IntraService с распределенным замком лидера (`lock:poller_leader`) против Split-Brain |
| **Execution Worker** | Python, Redis Streams, PowerShell, WinRM | Windows Headless Daemon оркестрации принтеров (WinRM/CIM) и фоновых задач через Consumer Group `execution_group` |
| **Helpdesk CLI** | Python, aiohttp, Typer | Машинный инструментарий (Tooling SDK) исключительно для AI-агента Antigravity (AGY) при обработке слэш-команд |
| **Shared (SSOT)** | Python, orjson | Единый пакет общих алгоритмов нормализации оборудования (`normalizer.py`), экспресс-диагностики (`diagnostics.py`) и сериализации |
| **Telegram Bot** | aiogram 3.x, aiohttp | Мобильный пейджер и HITL-согласования с гарантированной доставкой через Redis Streams (`stream:intraservice_events`) и `XAUTOCLAIM` |
| **Intra Web UI** | React 19, Vite, Tailwind CSS v4 | Двухконтурный интерфейс: операторский центр очереди 1-й линии (`/operator-panel`) и защищенная консоль администратора (`/admin`) |

---

### 1.1. Схема взаимодействия компонентов

```mermaid
flowchart TB
    classDef userStyle fill:#2d3748,stroke:#4a5568,stroke-width:2px,color:#fff;
    classDef clientStyle fill:#3182ce,stroke:#2b6cb0,stroke-width:2px,color:#fff;
    classDef coreStyle fill:#1a365d,stroke:#2b6cb0,stroke-width:2px,color:#fff;
    classDef workerStyle fill:#22543d,stroke:#2f855a,stroke-width:2px,color:#fff;
    classDef dbStyle fill:#805ad5,stroke:#6b46c1,stroke-width:2px,color:#fff;
    classDef extStyle fill:#742a2a,stroke:#9b2c2c,stroke-width:2px,color:#fff;
    classDef redisStyle fill:#c53030,stroke:#9b2c2c,stroke-width:2px,color:#fff;

    subgraph Clients ["Интерфейсные слои"]
        User(["👤 Пользователь в Telegram"]):::userStyle
        Admin_Web(["👨‍💻 Оператор в Web UI"]):::userStyle
        AGY_Agent(["🤖 AI-агент Antigravity (AGY)"]):::userStyle
    end

    subgraph Interface_Layer ["Слой интерфейсов и SDK"]
        TG_Bot["💬 Telegram Bot (aiogram 3.x)"]:::clientStyle
        Intra_Web["🖥️ Intra Web SPA (/admin)"]:::clientStyle
        HD_CLI["🛠️ helpdesk-cli (AGY Tooling SDK)"]:::clientStyle
    end

    subgraph Core_Layer ["Core API Gateway & Poller (SSOT)"]
        API_Main["⚡ FastAPI Gateway Hub"]:::coreStyle
        Poller_Daemon["👑 Poller Daemon (Leader Lock)"]:::coreStyle
        Rule_Engine["⚙️ Rule Engine & SSOT Templates"]:::coreStyle
        AI_Hub["🛡️ AI Hub & DLP Sanitizer"]:::coreStyle
        RAG_Engine["🧠 Hybrid RAG (pgvector + FastEmbed)"]:::coreStyle
    end

    subgraph Execution_Layer ["Слой исполнения (Windows Node)"]
        Win_Worker["⚙️ Execution Worker (Consumer Group)"]:::workerStyle
        AD_Exec["🏢 AD Executor (WLAN, User)"]:::workerStyle
        Prn_Exec["🖨️ Printer Executor (WinRM, SMB)"]:::workerStyle
    end

    subgraph Storage_Layer ["Слой данных и очередей"]
        Postgres_DB[("PostgreSQL 16 (pgvector HNSW)")]:::dbStyle
        Redis_Broker[("Redis 7 (Streams, Locks, PII Vault)")]:::redisStyle
        Ollama_Local[("Ollama (Qwen2.5:1.5B / bge-m3)")]:::dbStyle
    end

    subgraph External_Infrastructure ["Корпоративная инфраструктура"]
        IS_API["🌐 IntraService REST API"]:::extStyle
        AD_Domain["🏢 Active Directory / WinRM (corporate.loc)"]:::extStyle
    end

    %% Клиенты к интерфейсам
    User <--> TG_Bot
    Admin_Web <--> Intra_Web
    AGY_Agent <--> HD_CLI

    %% Интерфейсы к Core API
    TG_Bot <-->|HTTP REST (X-Bot-Api-Key)| API_Main
    Intra_Web <-->|REST API / JWT Session| API_Main
    HD_CLI <-->|HTTP REST (X-Bot-Api-Key)| API_Main

    %% Внутри Core
    API_Main --> Rule_Engine & AI_Hub & RAG_Engine
    Poller_Daemon -->|Leader Lock / SET NX EX| Redis_Broker
    Poller_Daemon -->|Polling / Service Account| IS_API
    Poller_Daemon -->|stream:intraservice_events| Redis_Broker

    %% Core к хранилищу
    RAG_Engine <--> Postgres_DB
    API_Main <--> Postgres_DB
    AI_Hub -->|RED Zone| Ollama_Local
    API_Main -->|Host Locks / Dead Man's Switch| Redis_Broker
    API_Main -->|stream:execution_queue| Redis_Broker

    %% Очереди исполнения и доставки
    Redis_Broker -->|stream:intraservice_events (XAUTOCLAIM)| TG_Bot
    Redis_Broker -->|stream:execution_queue| Win_Worker
    Win_Worker --> AD_Exec & Prn_Exec
    AD_Exec & Prn_Exec --> AD_Domain
    Win_Worker -->|HTTP REST / Complete| API_Main
```

---

## 🔒 2. Ключевые архитектурные принципы

1. **Единый источник правды (No Standalone Fragmentation):**
   Все правила триажа, шаблоны ответов заявителям, семантический поиск в базе знаний RAG и нормализация кастомных полей сосредоточены в `core-api`. Агент Helpdesk и веб-интерфейс используют единый backend REST API.
2. **Защита от слепого закрытия (Verified Execution Only):**
   Статус `29 Выполнена` устанавливается **исключительно** после фактического успешного выполнения действия в инфраструктуре (добавление в группу AD `WLAN-WORKNET`, создание пользователя в AD, установка принтера по WinRM).
3. **Двухэтапный жизненный цикл финализации (`apply` / `wlan` / `create-user`):**
   Перевод в финальные статусы `29 Выполнена` или `30 Отменена` обязательно выполняется через статус `27 В работе` с фиксацией двух исполнителей (`Беликов Ален` - ID `8664` и `Беликов Ален_assitant` - ID `10502`) и списанием трудозатрат.
4. **Асинхронность и отказоустойчивость:**
   Все системные вызовы PowerShell в CLI обернуты в `asyncio.to_thread`. Вызовы к внешнему IntraService защищены Circuit Breaker с экспоненциальным backoff.
5. **Strict Grounding и Intent Gates (детерминированные шлюзы):**
   - Вся механическая логика (DLP маскирование, Ping/WMI зонды хостов, редиректы каталога, исполнение в Active Directory, Redis-локи и списание трудозатрат) выполняется строго нативным детерминированным Python-кодом.
   - Перед передачей запроса в LLM применяются **детерминированные Intent Gates** (`ai_synthesis.py`): не-IT заявки (АХО, канцелярия — `_NON_IT_KEYWORDS`) и RED-контур (пароли/AD) немедленно возвращают готовый ответ без обращения к нейросети.
   - LLM получает только задокументированные **факты**: подтверждённое решение из RAG и телеметрию хоста. Если фактов нет — модель обязана сообщить о принятии заявки в работу, а не сочинять выполненные действия (**Strict Grounding**).
   - Нейросеть (LLM/Vision) подключается исключительно в точках гибкости: синтез персонализированных ответов заявителю, интерпретация неструктурированного текста, семантический RAG-поиск, анализ скриншотов ошибок.

---

## 🛡️ 3. Многоконтурная безопасность данных и гибридный AI-роутинг

Система реализует многоконтурную модель маршрутизации AI-инференса и RAG на принципах **Privacy-First & Zero Trust**:

```mermaid
flowchart LR
    subgraph Input ["Входные данные"]
        Req["Запрос оператора / Заявка"]
    end

    subgraph Router ["AI Smart Router & DLP Sanitizer"]
        DLP["DLP / PII Инспектор\n(Регулярные выражения + Heuristics)"]
        Decision{"Контур безопасности"}
        Vault[("Redis PII Vault\n(TTL 300s)")]
        Rehydrate["Rehydration\n(Деанонимизация)"]
    end

    subgraph RedZone ["🔴 Закрытый контур (On-Prem)"]
        LocalLLM["Локальная Ollama (Qwen/bge-m3)"]
        LocalRAG[("Локальная pgvector + FastEmbed")]
    end

    subgraph GreenZone ["🟢 / 🟡 Открытый контур (Cloud)"]
        LiteLLM["LiteLLM Proxy / Gemini Cloud API"]
    end

    Req --> DLP
    DLP --> Decision

    Decision -- "🔴 RED: Пароли, СБ, учетные записи" --> LocalLLM
    Decision -- "🟡 YELLOW: Есть ПДн/IP/Хосты -> Маскирование" --> Vault
    Vault --> LiteLLM
    LiteLLM --> Rehydrate
    Rehydrate <--> Vault

    Decision -- "🟢 GREEN: Общие регламенты без ПДн" --> LiteLLM
```

### Контуры безопасности данных:
1. 🔴 **Красный контур (RED / On-Premise)**:
   - Применяется при наличии паролей, токенов, конфиденциальных заявок СБ или кадровой службы.
   - Обработка и векторизация выполняются **строго локально** (Ollama `qwen`/`bge-m3` и `FastEmbed`). Данные не покидают периметр.
2. 🟡 **Трансформируемый контур (YELLOW / Sanitized Cloud)**:
   - Применяется при наличии персональных данных (ФИО, телефоны, email), внутренних IP-адресов (`10.x.x.x`, `192.168.x.x`) и имен ПК (`NTEMW...`).
   - Перед отправкой в Cloud Gemini сущности заменяются на обратимые маркеры (`{{USER_1}}`, `{{INTERNAL_IP_1}}`), а маппинг сохраняется в **Redis PII Vault**. В ответе модели маркеры автоматически восстанавливаются (**Rehydration**).
3. 🟢 **Открытый контур (GREEN / Cloud Direct)**:
   - Применяется для публичных вопросов, синтаксиса скриптов и общих регламентов. Запрос направляется в Gemini Cloud напрямую.

---

## 🛡️ 4. Защитные механизмы (Safety & Resiliency)

- **Distributed Host Concurrency Locks (`safety.py`):**
  - Распределенная блокировка в Redis (`lock:host:<pc_name>`, TTL 30s) с уникальным токеном владельца и Lua-скриптом безопасного снятия.
  - Предотвращает гонки параллельных WinRM/WMI сессий к одному хосту и исключает системные сбои `0x80338029`.
- **Аварийный тормоз (Dead Man's Switch):**
  - Скользящее окно Redis ZSET (`ratelimit:triage:apply`) блокирует массовые применения > 10 заявок/мин без явного подтверждения инженером (`confirmed_by_human=True`).

---

## 📡 5. Фоновая экспресс-телеметрия хостов (Fail-Fast Pre-fetch)

- **Сетевой каскад (`host_telemetry.py`):**
  - Fast ICMP Ping (таймаут 400 мс) $\rightarrow$ при оффлайне моментальный возврат `OFFLINE` без блокировки WMI.
  - TCP-пробы портов `5985` (WinRM) и `445` (SMB) с таймаутом 300 мс.
  - Ограничение сетевой нагрузки: `subnet_rate_limit` (не более 3 одновременных зондов на подсеть `/24`).
- **Сбор системных метрик:**
  - При открытом WinRM под защитой `host_concurrency_lock` собираются: остаток диска `C:`, службы `Spooler` и `1C:Enterprise`, активный пользователь.
  - Кэширование в Redis (`diag:<task_id>`, TTL 10 мин) обеспечивает мгновенный вывод телеметрии (0ms latency).

---

## 🧠 6. Двухэтапный Advanced Hybrid RAG и Канонизация базы знаний

```mermaid
flowchart LR
    Q["Запрос заявителя"] --> QD["Query Distillation (<10ms)"]
    QD --> Dense["Dense pgvector (3072-dim)"]
    QD --> Sparse["Sparse tsvector (Коды ошибок, Модели, Службы)"]
    Dense --> RRF["Reciprocal Rank Fusion (k=60)"]
    Sparse --> RRF
    RRF --> CE["Cross-Encoder Reranker (BAAI/bge-reranker)"]
    CE --> Synth["Response Synthesis (Инженер Беликов Ален)"]
```

1. **Query Distillation:** отсечение эмоционального шума и выделение кодов ошибок (`0x80070005`, `0x0000011b`), моделей оборудования и служб.
2. **Hybrid Retrieval (Dense + Sparse RRF):** параллельный векторный поиск в `pgvector` и полнотекстовый поиск по техническим термам с объединением по формуле $RRF\_Score = \sum \frac{1}{60 + rank_i}$.
3. **Cross-Encoder Reranking:** локальная переоценка пар `(query, document)` через `BAAI/bge-reranker-base` в неблокирующем потоке (`asyncio.to_thread`) с порогом $\ge 0.85$.
4. **Auto-KB Canonization (`canonize_task_solution`):** автоматическое извлечение триады `[Проблема] ➔ [Первопричина] ➔ [Решение]` из переписки закрытых заявок. Первопричина (`root_cause`) определяется детерминированными эвристиками (от специфичных кодов ошибок к общим терминам). Подписи инженера очищаются через SSOT-паттерн `_SIGNATURE_CLEANUP_RE` (ограничен концом строки, не жадный).
5. **Strict Grounding Synthesis (`synthesize_triage_resolution`):** ответ синтезируется строго на основе фактов RAG и телеметрии хоста. При отсутствии фактов — принятие заявки в работу без выдумывания действий. Синглтон `ai_hub` переиспользуется всеми компонентами (один HTTP-пул и семафор параллелизма на процесс).
