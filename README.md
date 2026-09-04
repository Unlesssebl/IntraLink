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
├── execution-worker/  # Фоновый Windows Headless Daemon исполнения задач (AD WLAN, WinRM, SMB, принтеры)
├── intralink-mcp/     # FastMCP Hub для AI-агента Antigravity (JSON-RPC 2.0 stdio инструменты)
├── helpdesk-cli/      # Машинный инструментарий и SDK исключительно для AI-агента Antigravity (AGY)
├── shared/            # Единый пакет общих алгоритмов (SSOT): normalizer, diagnostics, json_utils, printers
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

* **Защищенный API Gateway (Core API)**: Единая точка доступа к REST API IntraService, Fernet-шифрование учетных записей, централизованный Rule Engine, декомпозированный `TriageService` и двухэтапный Hybrid RAG.
* **Декларативный реестр навыков (Action Registry) & Policy Engine**: Каталог инфраструктурных действий с JSON-схемами, мгновенным аппаратным Killswitch (`HTTP 403 Forbidden`) и режимами автономного исполнения (`auto`) или ручного подтверждения (`confirm` / HitL).
* **Шлюз инструментов FastMCP Hub (`intralink-mcp`)**: Автономный MCP-сервер инструментов для AI-агента Antigravity (пакетный триаж, карточки тикетов, телеметрия хостов, векторный RAG и постановка задач в Command Bus).
* **Изолированный Poller с Leader Lock**: Независимый демон фонового опроса очереди от сервисного аккаунта под защитой распределенного замка (`lock:poller_leader`, TTL 15s) для предотвращения Split-Brain при масштабировании.
* **Гарантированная доставка событий (Redis Streams)**: Поток `stream:intraservice_events` с Consumer Groups, подтверждением `XACK` и периодическим перехватом зависших сообщений `XAUTOCLAIM` (At-Least-Once).
* **Исполнение в Windows-домене (Execution Worker)**: Фоновая обработка очереди `stream:execution_queue` на базе стандарта `BaseActionExecutor` (`Preflight ➔ Execute ➔ Verify`) — выдача сетевого доступа в AD (`WLAN-WORKNET`), создание учетных записей, удаленная установка принтеров через WinRM с WMI Bootstrap и защитой от коллизий сессий (`lock:host:<pc>`, TTL 30s).
* **Многоконтурная безопасность данных (Zero Trust DLP)**:
  * 🔴 **RED Zone (On-Prem)**: пароли и заявки СБ обрабатываются локально (Ollama Qwen2.5 / bge-m3).
  * 🟡 **YELLOW Zone (Sanitized Cloud)**: ПДн, IP и имена хостов маскируются токенами через Redis PII Vault перед вызовом облачных моделей.
  * 🟢 **GREEN Zone (Cloud Direct)**: открытые регламенты и технические вопросы.
* **Прямой LDAPS-клиент Active Directory (порт 636)**: Управление доменными объектами и выдача доступа к корпоративному Wi-Fi (`WLAN-WORKNET`) без необходимости обращения к клиентскому ПК и без участия Windows-воркера.
* **Многоуровневый каскад исполнения (Tiered Execution)**: Автоматическая установка принтеров через WinRM $\rightarrow$ LiteManager (порт 5650) $\rightarrow$ DameWare (порт 6129) $\rightarrow$ One-Liner ассистент оператора.
* **Инструментарий AI-агента (AGY Toolset)**: Управление очередью прямо из диалога с AI-ассистентом через слэш-команды (`/triage`, `/task`, `/diag`, `/screen`, `/kb`, `/sync`, `/redirect`) и нативные MCP-инструменты.
* **Мобильный пейджер (Telegram Bot)**: Моментальные уведомления, просмотр активных заявок инженера и кнопки подтверждения операций (Human-in-the-Loop) через единую шину Command Bus.
* **Двухконтурный Web SPA (`/operator-panel` и `/admin`)**: React 19 интерфейс с центрами обработки заявок, вкладкой **Skills Hub** с тумблерами Killswitch, Live SSE Terminal мониторинга исполнения и 1-Click Action виджетами в карточке заявки.

---

## 🛠 Технологический стек

* **Бэкенд**: Python 3.11+, FastAPI, SQLAlchemy 2.0 (Async), asyncpg, ldap3, aiohttp, orjson, pyjwt, cryptography (Fernet).
* **База данных и поиск**: PostgreSQL 16, pgvector (HNSW), FastEmbed (ONNX MiniLM-L12 + bge-reranker).
* **Шина сообщений и кэш**: Redis 7 (Streams, Consumer Groups, Distributed Locks, PII Vault).
* **ИИ и инференс**: Ollama (Qwen2.5:1.5B, bge-m3), LiteLLM Proxy / Gemini Cloud.
* **Фронтенд**: React 19, TypeScript, Tailwind CSS v4, Vite 6 (`vite-plugin-singlefile`).
* **Бот**: aiogram 3.x, aiohttp.
* **Инфраструктура исполнения**: PowerShell, pywinrm, WMI / CIM over WinRM:5985, LiteManager / DameWare integration.

---

## ⚡ Быстрый запуск

### 1. Запуск основной инфраструктуры в Docker:
```bash
docker compose up -d
```
Поднимает контейнеры: `postgres` (pgvector), `redis`, `core-api` (Gateway + Admin/Operator SPA) и `poller` (фоновый опрос с Leader Lock).

#### Выбор режима Ollama и аппаратного ускорения AI:
Инфраструктура поддерживает гибкое переключение режимов через переменную `COMPOSE_FILE` в файле `.env`:

| Режим работы | Настройка в `.env` | Описание |
|---|---|---|
| **Хостовая Ollama (Рекомендуется)** | `COMPOSE_FILE=docker-compose.yml`<br>`OLLAMA_BASE_URL=http://localhost:11434` | Максимальная скорость (**~115 токенов/сек** на RTX 3050). Контейнер Ollama **не создается**, прямое подключение через `host.docker.internal:11434` без оверхеда. |
| **CPU в Docker** | `COMPOSE_FILE=docker-compose.yml:docker-compose.ollama-cpu.yml` | Автономный запуск Ollama в контейнере на CPU без привязки к хосту. |
| **NVIDIA в Docker (CUDA)** | `COMPOSE_FILE=docker-compose.yml:docker-compose.ollama-nvidia.yml`<br>`CUDA_VISIBLE_DEVICES=1` | Проброс GPU в контейнер через NVIDIA Container Toolkit с фиксацией на 8 ГБ RTX 3050. |
| **AMD в Docker (Vulkan)** | `COMPOSE_FILE=docker-compose.yml:docker-compose.ollama-vulkan.yml` | Аппаратное ускорение графического процессора AMD через Vulkan (`/dev/dri`) без ROCm. |


* **Операторский центр обработки заявок:** `http://localhost:8000/operator-panel`
* **Консоль системного администратора:** `http://localhost:8000/admin`
* **Интерактивная документация API:** `http://localhost:8000/docs`

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

### Проверка в Docker

Production-образы не содержат dev-зависимостей. Для воспроизводимого прогона
тестов используется отдельный Docker target с зависимостями из `uv.lock`:

Перед переключением трафика на новый образ обязательно передайте SHA в build-arg
`GIT_SHA`, поднимите кандидат на отдельном порту и выполните fail-closed проверку:

```powershell
python core-api/scripts/rollout_check.py --base-url http://127.0.0.1:8001 --sha <commit-sha>
```

Проверка разрешает rollout только когда SHA Core API и встроенного Web UI совпадают
с указанным коммитом и миграция `security_audit_log` доступна. Она не перезапускает
основной Docker-стек и не включает трафик самостоятельно.

```bash
docker compose --profile test run --rm tests
```

Для запуска отдельного набора тестов передайте путь вместо стандартной команды:

```bash
docker compose --profile test run --rm tests tests/test_ai_sanitizer.py -q
```

Профиль поднимает изолированный Redis без постоянного тома. Чтобы не
останавливать основной стек, после прогона удалите только тестовый контейнер:

```bash
docker compose --profile test rm --stop --force test-redis
```

### Offline eval AI-автоматизации

Контур не выполняет заявки и не обращается к LLM: он проверяет уже сохранённые
анонимизированные предсказания RAG/LLM. Исторический JSONL выгружается
отдельным read-only процессом и хранится вне Git.

```bash
# Стабильная секретная соль не должна попадать в репозиторий.
$env:EVAL_EXPORT_SALT = "<secret>"
uv run python core-api/scripts/export_eval_dataset.py --source F:\eval\history.jsonl --output F:\eval\dataset.jsonl

# Датасет должен быть дополнен retrieved_ids, predicted_status_id,
# predicted_action, effective_mode и dlp_safe от проверяемой сборки.
uv run python core-api/scripts/run_offline_eval.py --dataset F:\eval\predictions.jsonl --baseline F:\eval\baseline.json --report F:\eval\report.json
```

Gate возвращает `0` при успехе, `1` при нарушении safety или регрессии более
1 п.п. от baseline, `2` при некорректном датасете. Он требует минимум 100
кейсов и использует временное деление 70/15/15: прошлое — corpus, последнее —
удерживаемый test-набор.
