# 🤖 Руководство разработчика (AI Agent Developer Guide)

Этот документ является настольной книгой для AI-агентов и разработчиков, работающих над кодовой базой **IntraLink**. В нем зафиксированы архитектурные инварианты, правила межсервисного взаимодействия, команды отладки и стандарты безопасности.

---

## 🗺️ 1. Карта монорепозитория

Проект организован как модульный монорепозиторий со следующими сервисами и пакетами:

```text
IntraLink/
├── core-api/          # FastAPI Gateway: база данных (PostgreSQL+pgvector), AI Hub, фоновый poller, хостинг /admin
├── execution-worker/  # Фоновый Windows Headless Daemon исполнения задач (AD WLAN, WinRM, SMB)
├── helpdesk-cli/      # Машинный SDK и tooling исключительно для AI-агента Antigravity (AGY)
├── shared/            # Единый пакет общих алгоритмов (SSOT): нормализация хостов, экспресс-диагностика
├── telegram-bot/      # Telegram Bot (aiogram 3.x): интерфейс пользователя и подписчик Redis Streams
├── intra-web/         # Фронтенд веб-панели администратора (React 19, Vite, Tailwind CSS v4)
├── tools/             # Утилиты индексации драйверов печати
├── docs/              # Системная документация проекта
└── .agents/           # AI-конфигурация: правила (rules/) и специализированные навыки (skills/)
```

---

## 🔒 2. Ключевые архитектурные инварианты (ПОЧЕМУ так устроено)

### 1. Бот не хранит пароли и секреты
* **Правило:** Бот (`telegram-bot/`) является тонким интерфейсным слоем. Вся ответственность за хранение зашифрованных паролей IntraService и доступ к API лежит на `core-api/`.
* **Причина:** Изоляция секретов. При компрометации бота злоумышленник не получает доступа к корпоративным учетным записям.

### 2. Единый источник правды в shared (SSOT)
* **Правило:** Алгоритмы нормализации оборудования (`normalizer.py`), сетевой экспресс-диагностики (`diagnostics.py`) и сериализации (`json_utils.py`) живут строго в пакете `shared/`.
* **Причина:** Предотвращение дублирования кода и рассинхронизации регулярных выражений парсинга хостов/IP между сервисами.

### 3. Защита от слепого закрытия (Verified Execution Only)
* **Правило:** Статус `29 Выполнена` устанавливается **исключительно** после фактического успешного подтверждения действия в инфраструктуре (добавление в группу AD `WLAN-WORKNET`, успешная установка принтера через WinRM).
* **Причина:** Исключение фиктивных закрытий инцидентов при сбоях в сетевом или доменном слое.

### 4. Изоляция демона опроса (Poller Lifecycle)
* **Правило:** Опрос очереди IntraService выполняется выделенным сервисом `poller` (`app.poller`) под защитой распределенного Leader Lock в Redis (`lock:poller_leader`, TTL 15s).
* **Причина:** Исключение Split-Brain и дублирования запросов к IntraService при горизонтальном масштабировании контейнеров `core-api`.

### 5. Часовые пояса и форматы дат
* **Правило:** Внутри БД и Redis все метки времени хранятся в **UTC**.
* **Причина:** Перед отправкой запроса к IntraService API дата конвертируется в локальный часовой пояс (`settings.INTRASERVICE_TZ`, по умолчанию `Europe/Moscow`). Формат для API: `YYYY-MM-DD HH:MM`. Не передавайте UTC-время напрямую в IntraService!

### 6. WMI Bootstrap для WinRM
* **Правило:** Модуль установки принтеров динамически включает службу WinRM на рабочей станции пользователя через WMI (DCOM/RPC) непосредственно перед установкой принтера, а после завершения (успешного или аварийного) **гарантированно отключает её**.
* **Причина:** Минимизация поверхности сетевых атак в домене и отсутствие необходимости глобального распространения GPO.

### 7. UPN-форматирование доменных учетных записей
* **Правило:** При обращении к SMB/WinRM логин всегда приводится к UPN-формату: `username@domain.com` (вместо `domain\username`).
* **Причина:** Устранение ошибок `STATUS_LOGON_FAILURE` при аутентификации из Linux-контейнеров в Active Directory.

### 8. Распределенные блокировки хостов (Host Concurrency Locks)
* **Правило:** Любые параллельные WinRM/WMI операции к одной рабочей станции защищаются мьютексом `lock:host:<pc_name>` в Redis.
* **Причина:** Предотвращение гонок параллельных сессий и системных сбоев Windows `0x80338029`.

### 9. Адаптивный AI Hub и аппаратное ускорение (Vulkan/DirectML и CUDA без ROCm)
* **Правило:** В системе исключена зависимость от ROCm. Ускорение на видеокартах AMD реализовано через стандартный графический стек **Vulkan** (`/dev/dri`) и **DirectML** (ONNX Runtime). Для карт NVIDIA (RTX 3050) задействуется **CUDA** с изоляцией целевого GPU через `CUDA_VISIBLE_DEVICES=1`. Контейнеры Core API подключаются к хостовой Ollama через сетевой мост `extra_hosts: ["host.docker.internal:host-gateway"]`.
* **Причина:** Устранение проблем совместимости ROCm с потребительскими GPU и версиями ядер Linux. Обеспечение максимальной скорости инференса (~115 tps) без накладных расходов виртуализации.

---

## 📡 3. Межсервисные коммуникации

```mermaid
flowchart LR
    subgraph Clients ["Интерфейсы"]
        TG["Telegram Bot (aiogram)"]
        Web["Admin Web UI (intra-web)"]
        AGY["AI Agent (AGY / helpdesk-cli)"]
    end

    subgraph Core ["Центральный шлюз (SSOT)"]
        CoreAPI["Core API (FastAPI)"]
        Poller["Poller Daemon"]
    end

    subgraph Broker ["Шина сообщений и очередей"]
        Redis[("Redis 7 (Streams / Locks)")]
        PG[("PostgreSQL 16 + pgvector")]
    end

    subgraph Execution ["Слой исполнения"]
        Worker["Execution Worker (Windows Node)"]
    end

    subgraph External ["Внешние системы"]
        IS["IntraService REST API"]
        AD["Active Directory / WinRM"]
    end

    TG -->|HTTP REST (X-Bot-Api-Key)| CoreAPI
    Web -->|HTTP REST (JWT Session)| CoreAPI
    AGY -->|HTTP REST (X-Bot-Api-Key)| CoreAPI

    Poller -->|Leader Lock| Redis
    Poller -->|Poll Tasks| IS
    Poller -->|stream:intraservice_events| Redis

    CoreAPI -->|SQLAlchemy Async| PG
    CoreAPI -->|stream:execution_queue| Redis
    Redis -->|stream:intraservice_events (XAUTOCLAIM)| TG

    Redis -->|stream:execution_queue| Worker
    Worker -->|PowerShell / WinRM| AD
    Worker -->|HTTP REST / Update Status| CoreAPI
```

### Ключевые каналы Redis Streams:
* `stream:intraservice_events` — поток событий очереди (Consumer Group `bot_group`, `XACK`, `XAUTOCLAIM`).
* `stream:execution_queue` — очередь задач на исполнение в домене для Windows-воркера (`execution_group`).
* `stream:execution_failed` — Dead-Letter Queue (DLQ) для аудита аварийных задач.
* `lock:poller_leader` — распределенный мьютекс лидера опроса (TTL 15s).
* `lock:host:<pc_name>` — распределенный мьютекс исключительного доступа к ПК (TTL 30s).

---

## 🛠 4. Инструменты автономной диагностики инженера (`helpdesk.py`)

Для автономной проверки и управления очередью заявок, RAG-поиска и инфраструктурных действий используется CLI-инструмент [`helpdesk-cli/helpdesk.py`](services/helpdesk-cli/README.md):

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

## 🧪 5. Тестирование и учетные данные

```bash
# Запуск всех юнит-тестов Core API:
python -m pytest core-api/tests/ -v

# Проверка сборки фронтенда intra-web:
cd intra-web && npm run build
```
---

## 📝 6. Стандарты написания кода (Backend Guidelines)

1. **Строгая асинхронность:** Все сетевые, дисковые и БД операции выполняются строго через `async`/`await`:
   - База данных: `SQLAlchemy AsyncSession` (`asyncpg`).
   - HTTP: долгоживущие сессии `aiohttp.ClientSession` с закрытием в `finally`.
   - Redis: `redis.asyncio` (`aioredis`) с контекстными менеджерами `async with redis.pubsub() as pubsub:`.
2. **Формат дат и часовые пояса:** 
   - Внутреннее время всегда в **UTC**.
   - При передаче временных фильтров в IntraService API (`CreatedMoreThan`, `ChangedMoreThan`) обязательно конвертировать дату в локальный TZ IntraService (`settings.INTRASERVICE_TZ`, по умолчанию `Europe/Moscow`) в формате `YYYY-MM-DD HH:MM`.
3. **Безопасность:**
   - Pre-Shared Key `X-Bot-Api-Key` проверять строго через `secrets.compare_digest` в `deps.py` для защиты от Timing Attacks.
   - Не слушать на `0.0.0.0` вне Docker-контейнеров (при локальном запуске — только `127.0.0.1`).
   - Доменные учетные данные шифровать Fernet перед сохранением в Redis.
4. **Документация:**
   - Пути в документации — **только относительные** (`docs/architecture.md`, без `file:///...`).
   - Документировать архитектурные решения и инварианты («ПОЧЕМУ»), не дублировать автогенерируемый OpenAPI (`/docs`).
