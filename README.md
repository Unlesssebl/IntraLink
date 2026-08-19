# IntraLink

Современный асинхронный Телеграм-бот, API-шлюз и инструменты автоматизации для мониторинга, уведомлений и управления заявками в Helpdesk-системе **IntraService**. 

Проект построен на базе микросервисной архитектуры, объединяющей интерфейсные слои (Telegram-бот, веб-панель администратора, AI-ассистент в AGY) и сервисы автоматизации (FastAPI Gateway, AI Worker, Printer Worker, Helpdesk Agent).

---

## 🏗 Архитектура системы

Система построена на событийно-ориентированном подходе и состоит из основных компонентов, разворачиваемых в единой сети:
1. **PostgreSQL** — реляционная база данных для хранения учетных данных пользователей IntraService (в зашифрованном виде), состояния опроса (`last_task_id`, `last_check_time`) и базы знаний RAG (с поддержкой pgvector).
2. **Redis** — шина сообщений (Pub/Sub). Обеспечивает асинхронную доставку уведомлений к Telegram-боту, а также передает задачи и команды к фоновым воркерам.
3. **Core API (FastAPI)** — защищенный микросервис-шлюз. Он управляет базой данных, предоставляет веб-панель администратора, взаимодействует с IntraService API, а также запускает фоновый **Worker** (`APScheduler`), который опрашивает IntraService и публикует новые события в Redis Pub/Sub.
4. **Telegram Bot (aiogram 3.x)** — легкий интерфейсный сервис, который принимает команды пользователей и запускает фоновый **Redis Listener** для получения событий из шины Redis и мгновенной отправки уведомлений в Telegram.
5. **AI Worker** — микросервис для умной классификации новых заявок на основе RAG-поиска по базе знаний в PostgreSQL и генерации автоматических ответов с использованием LLM.
6. **Printer Worker** — микросервис автоматической установки сетевых и локальных принтеров через WMI/WinRM/SMB.
7. **Helpdesk Agent (AGY-Native)** — набор быстрых I/O утилит и навык для ведения и разбора очереди инцидентов технической поддержки прямо из среды Antigravity (AGY) в режиме Human-in-the-Loop.

> [!NOTE]
> Схемы последовательности выполнения запросов (Data Flow) и подробная архитектурная диаграмма компонентов доступны в документе [architecture.md](docs/architecture.md).

---

## 🚀 Основные возможности

*   **Персональная авторизация**: Безопасный вход под учетными данными IntraService.
*   **Безопасность данных**: Данные авторизации (логин/пароль) шифруются с помощью Fernet (`cryptography`) и сохраняются в PostgreSQL. Бот не хранит пароли локально, а сообщения с паролями удаляются из истории чата Telegram сразу после отправки.
*   **Событийный фоновый мониторинг (Redis Pub/Sub)**:
    *   Мгновенные уведомления обо всех **новых заявках** в системе IntraService.
    *   Персональные уведомления о **новых комментариях** и **сменах статусов** для заявок, где пользователь является назначенным исполнителем.
    *   Автоматический трекинг состояния опроса для исключения дублирования.
*   **📋 Мои заявки**: Просмотр списка активных задач пользователя с поддержкой пагинации прямо в Telegram.
*   **🤖 Автономный Helpdesk-оператор в AGY**: Прямой анализ инцидентов 1-й линии моделью Antigravity, сетевая диагностика хостов заявителей (ICMP/DNS/SMB), проверка каталога услуг и формирование ответов в стиле инженера Беликова Алена.

---

## 🛠 Технологический стек

### Core API & Воркеры
*   **FastAPI** — веб-фреймворк для создания REST API.
*   **SQLAlchemy 2.0 (Async)** + **asyncpg** + **pgvector** — асинхронная работа с базой данных PostgreSQL и векторным поиском.
*   **APScheduler** — встроенный планировщик для периодического опроса IntraService.
*   **redis.asyncio** — асинхронная публикация событий в Redis.
*   **cryptography** — симметричное шифрование учетных данных в БД.
*   **aiohttp** — асинхронные запросы к REST API IntraService.

### Telegram Bot (aiogram)
*   **aiogram 3.x** — асинхронный фреймворк для Telegram Bot API.
*   **redis.asyncio** — асинхронное прослушивание событий Redis Pub/Sub.
*   **aiohttp** — взаимодействие с Core API.

---

## 📂 Структура проекта

```text
├── ai-worker/                 # Микросервис интеллектуальной классификации и автоответов (RAG + pgvector + LLM)
│   ├── core/                  # Конфигурация и шифрование
│   ├── services/              # Бизнес-логика классификатора, автоответчика и сборщика RAG
│   ├── Dockerfile
│   ├── worker_main.py         # Точка входа в микросервис
│   └── requirements.txt
│
├── bot/                       # Сервис Telegram-бота (aiogram)
│   ├── handlers/              # Обработчики событий Telegram
│   │   ├── auth.py            # Авторизация (вход, выход)
│   │   ├── start_help.py      # Команды /start, /help и меню кнопок
│   │   └── tickets.py         # Пагинация и отображение списка заявок
│   ├── services/
│   │   ├── api_client.py      # HTTP-клиент для работы с Core API
│   │   └── redis_listener.py  # Фоновый слушатель шины сообщений Redis Pub/Sub
│   ├── config.py              # Конфигурация бота
│   ├── main.py                # Точка входа для запуска бота
│   ├── utils.py               # Вспомогательные функции (парсинг HTML, форматирование)
│   └── requirements.txt
│
├── core-api/                  # Сервис API-шлюза (FastAPI)
│   ├── app/
│   │   ├── database/          # Слой базы данных (SQLAlchemy модели и сессии)
│   │   │   └── db.py          # Инициализация БД PostgreSQL / SQLite (с поддержкой pgvector)
│   │   ├── models/            # Схемы валидации Pydantic
│   │   │   └── schemas.py
│   │   ├── routers/           # Контроллеры FastAPI (endpoints)
│   │   │   ├── admin.py       # Панель администратора (/admin, /admin/api/*)
│   │   │   ├── auth.py        # /auth/login, /auth/logout
│   │   │   ├── deps.py        # Зависимости (проверка API-ключа, сессии)
│   │   │   ├── tasks.py       # /tasks (заявки, комментарии, жизненный цикл)
│   │   │   └── users.py       # /users (профиль пользователя)
│   │   ├── static/            # Статические ресурсы веб-панели
│   │   │   └── admin/
│   │   │       └── index.html # HTML/JS/CSS панели администратора
│   │   ├── services/
│   │   │   ├── crypto.py      # Модуль шифрования/дешифрования токенов
│   │   │   ├── intraservice.py# Интеграционный клиент с REST API IntraService
│   │   │   └── worker.py      # Фоновый воркер (опрос IntraService и публикация в Redis)
│   │   ├── config.py          # Загрузка и валидация настроек (Pydantic Settings)
│   │   └── main.py            # Настройка FastAPI приложения и роутеров
│   ├── tests/                 # Юнит-тесты (pytest)
│   └── requirements.txt
│
├── helpdesk_agent/            # Инструменты ввода-вывода Helpdesk для среды Antigravity (AGY)
│   ├── helpdesk_tool.py       # CLI I/O интерфейс (queue, task, diagnose, apply, catalog, history)
│   ├── diagnostics.py         # Сетевая диагностика ПК (ICMP, DNS, SMB)
│   ├── intraservice_api.py    # Асинхронный клиент IntraService API
│   ├── GEMINI.md              # Системная персона и шаблоны инженера Беликова Алена
│   ├── pyproject.toml         # Конфигурация uv пакета
│   └── README.md              # Документация пакета
│
├── .agents/skills/            # Навыки Antigravity (AGY)
│   └── intraservice-helpdesk/ # Навык ведения очереди техподдержки
│
├── debug_tools/               # Набор CLI-утилит для ручной диагностики и тестирования микросервисов
│   ├── cli.py                 # Единая точка входа (команды: ai, printer, approve, redis, stuck, fix)
│   ├── ai_debugger.py         # Отладка AI-классификатора и RAG-поиска (вывод top-5 совпадений pgvector)
│   ├── printer_debugger.py    # Отладка парсинга заявок, SNMP, Fast-Track, Smart-Track
│   ├── batch_runner.py        # Батч-тестирование на выборке заявок с расчетом Accuracy
│   └── core.py                # Загрузка данных заявок с автоматическим fallback на кэш Redis
│
├── printer-worker/            # Микросервис автоустановки принтеров (WMI + WinRM + SMB)
│   ├── executors/             # Низкоуровневые исполнители (WMI, WinRM, SMB)
│   ├── knowledge_base/        # База знаний принтеров (JSON)
│   ├── llm/                   # Модуль интеграции с LLM (Ollama, OpenAI)
│   ├── orchestrator/          # Маршрутизатор, схемы Pydantic, стейт-машина
│   ├── worker_services/       # Клиент Core API, подписчик Redis
│   ├── strategies/            # Стратегии установки (TCP/IP, USB)
│   ├── worker_config.py       # Конфигурация сервиса
│   ├── Dockerfile             # Сборка контейнера
│   ├── worker_main.py         # Точка входа
│   └── requirements.txt
│
├── docs/                      # Документация проекта
│   ├── architecture.md        # Системная архитектура и Data Flow
│   ├── services/              # Документация по сервисам
│   │   ├── core-api/          # Core API
│   │   ├── bot/               # Telegram Bot
│   │   ├── printer-worker/    # Printer Worker
│   │   ├── ai-worker/         # AI Worker
│   │   └── helpdesk_agent/    # Helpdesk Agent
│   └── external/              # Справочники внешних систем
│       └── intraservice_api/  # Документация внешнего API IntraService
├── docker-compose.yml         # Оркестрация контейнеров (Postgres, Redis, Core API, Bot, Printer Worker, AI Worker)
├── pyproject.toml             # Конфигурация для пакетного менеджера uv
└── uv.lock                    # Блокировка зависимостей uv
```

---

## 🤖 Helpdesk-инструменты (`helpdesk_agent/helpdesk_tool.py`)

Инструменты I/O и сетевой диагностики, используемые AGY-агентом для работы с IntraService:

```bash
# Получить открытые заявки очереди 1-й линии (фильтр 984):
uv run python helpdesk_agent/helpdesk_tool.py queue --limit 10

# Детальный просмотр инцидента с кастомными полями:
uv run python helpdesk_agent/helpdesk_tool.py task 139022

# Сетевая проверка доступности ПК заявителя:
uv run python helpdesk_agent/helpdesk_tool.py diagnose TEMPO-PC01

# Просмотр истории заявки:
uv run python helpdesk_agent/helpdesk_tool.py history 139022

# Применить изменения в IntraService (после подтверждения оператором):
uv run python helpdesk_agent/helpdesk_tool.py apply 139022 --status 27 --comment "Ваша заявка принята в работу. По вопросам звоните на номер 49-87." --expenses 15
```

---

## ⚙️ Установка и запуск

### Способ 1. Запуск через Docker Compose (Рекомендуемый)

Для повышения уровня безопасности чувствительные данные (ключи шифрования, пароли) вынесены из переменных окружения в **Docker Secrets**. Перед запуском необходимо подготовить файлы секретов.

#### Шаг 1. Подготовка секретов (Docker Secrets)
Создайте каталог `.secrets` в корне проекта (он добавлен в `.gitignore` и не попадет в репозиторий) и разместите в нем следующие файлы:
1. `.secrets/encryption_key.txt` — симметричный ключ Fernet для шифрования пользовательских паролей в БД. Можно сгенерировать командой (используя виртуальное окружение):
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. `.secrets/service_password.txt` — пароль от сервисного аккаунта IntraService, используемого фоновым воркером для глобального мониторинга заявок.
3. `.secrets/jwt_secret.txt` — случайная строка (ключ подписи сессий JWT для веб-панели администратора).

> [!WARNING]
> **Важная деталь (Docker Compose Gotcha):**
> Указанные файлы секретов (`.secrets/*.txt`) **обязательно должны быть созданы до первого запуска** `docker compose up`.
> Если запустить контейнеры до создания файлов, Docker Compose автоматически создаст на хосте **директории** с такими именами. Это заблокирует работу приложения (оно не сможет прочитать секреты).
> Если вы столкнулись с этим, выполните `docker compose down`, удалите папки-пустышки из `.secrets/`, создайте текстовые файлы и запустите контейнеры снова.

#### Шаг 2. Подготовка файлов конфигурации
Создайте `.env` в папке `./core-api` на основе `core-api/.env.example`. Добавьте логин сервисного аккаунта:
```env
INTRASERVICE_URL=https://ваш-домен.intraservice.ru/api/
INTRASERVICE_SERVICE_LOGIN=логин_сервисного_аккаунта
SSL_VERIFY=False
```

При необходимости запуска Telegram-бота, создайте `.env` в папке `./bot` на основе `bot/.env.example`:
```env
BOT_TOKEN=ваш_токен_телеграм_бота
CORE_API_URL=http://core-api:8000/api/v1
BOT_API_KEY=надежный_ключ_для_авторизации_бота
INTRASERVICE_URL=https://ваш-домен.intraservice.ru/api/
POLLING_INTERVAL=10
```

#### Шаг 3. Запуск служб
Проект использует **Docker Compose Profiles** для разделения запуска компонентов. Бот по умолчанию отключен.

*   **Запуск без Telegram-бота** (только Core API, БД, Redis, Printer Worker и Веб-панель):
    ```bash
    docker compose up --build -d
    ```
*   **Запуск с Telegram-ботом** (полный стек):
    ```bash
    docker compose --profile with-bot up --build -d
    ```

---

## 🛠 Инструменты отладки (CLI-дебаггер)

Для автономной диагностики воркеров (подход LLM-as-judge) в папке `debug_tools` подготовлен инструмент:
```bash
# Тестирование классификатора AI-worker на конкретной заявке (с выводом топ-5 RAG-кейсов)
python -m debug_tools.cli ai --task-id <id>

# Тестирование роутера принтеров (SNMP, Fast-Track, Smart-Track)
python -m debug_tools.cli printer --task-id <id>

# Батч-тестирование на выборке заявок
python -m debug_tools.cli printer --batch 133609,141205,145001

# Диагностические команды:
python -m debug_tools.cli redis    # Состояние Redis
python -m debug_tools.cli stuck    # Поиск зависших заявок
python -m debug_tools.cli fix      # Автоматическое исправление
```

---

## 🧪 Тестирование

```bash
uv run pytest -v
```

---

## 🔒 Безопасность и API-ключ

*   **`BOT_API_KEY`**: Защита внутренних межсервисных запросов.
*   **Шифрование Fernet**: Пароли пользователей IntraService шифруются в PostgreSQL с помощью ключа из Docker Secret `encryption_key`.
