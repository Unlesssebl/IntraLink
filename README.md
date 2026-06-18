# IntraLink

Современный асинхронный Телеграм-бот и API-шлюз для мониторинга, уведомлений и управления заявками в Helpdesk-системе **IntraService**. 

Проект построен на базе микросервисной архитектуры, разделяющей слой представления (Telegram-бот) и слой бизнес-логики/хранения данных (FastAPI Gateway).

---

## 🏗 Архитектура системы

Система построена на событийно-ориентированном подходе и состоит из четырех основных компонентов, разворачиваемых в единой сети:
1. **PostgreSQL** — реляционная база данных для хранения учетных данных пользователей IntraService (в зашифрованном виде), состояния опроса (`last_task_id`, `last_check_time`) и базы знаний RAG (с поддержкой pgvector).
2. **Redis** — шина сообщений (Pub/Sub). Обеспечивает асинхронную доставку уведомлений от Core API к Telegram-боту в реальном времени.
3. **Core API (FastAPI)** — защищенный микросервис-шлюз. Он управляет базой данных, взаимодействует с IntraService API, а также запускает встроенный фоновый **Worker** (`APScheduler`), который опрашивает IntraService и публикует новые события в Redis Pub/Sub.
4. **Telegram Bot (aiogram 3.x)** — легкий интерфейсный сервис, который принимает команды пользователей и запускает фоновый **Redis Listener** для получения событий из шины Redis и мгновенной отправки уведомлений в Telegram.

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

---

## 🛠 Технологический стек

### Core API (FastAPI)
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
│   │   ├── conftest.py        # Настройки тестов и фикстуры
│   │   ├── test_crypto.py     # Тесты шифрования/дешифрования токенов
│   │   ├── test_intraservice.py # Тесты интеграционного клиента IntraService
│   │   └── test_worker.py     # Тесты фонового воркера (process_user и др.)
│   └── requirements.txt
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
│   ├── services/              # Документация по нашим сервисам
│   │   ├── core-api/          # Core API: справочник эндпоинтов
│   │   ├── bot/               # Telegram Bot (в разработке)
│   │   └── printer-worker/    # Микросервис автоустановки принтеров
│   └── external/              # Справочники внешних систем
│       └── intraservice_api/  # Документация внешнего API IntraService
├── docker-compose.yml         # Оркестрация контейнеров (Postgres, Redis, Core API, Bot, Printer Worker)
├── pyproject.toml             # Конфигурация для пакетного менеджера uv
└── uv.lock                    # Блокировка зависимостей uv
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

Команда автоматически соберет образы, настроит Docker Secrets (монтируя их как защищенные файлы /run/secrets/ в памяти контейнера) и запустит сервисы.

---

### Способ 2. Локальный запуск (для разработки)

#### Шаг 1. Настройка Базы Данных
По умолчанию Core API использует PostgreSQL. Вы можете запустить Postgres локально или использовать SQLite для тестирования:
* Для использования SQLite измените `DATABASE_URL` в `core-api/.env`:
  ```env
  DATABASE_URL=sqlite+aiosqlite:///./core_api.db
  ```

#### Шаг 2. Запуск Core API (FastAPI)
Рекомендуется использовать быстрый менеджер пакетов [uv](https://github.com/astral-sh/uv):

1. Перейдите в каталог `core-api`:
   ```bash
   cd core-api
   ```
2. Создайте файл `.env` на основе `core-api/.env.example`.
3. Установите зависимости и запустите сервер:
   ```bash
   uv venv
   # Активируйте виртуальное окружение:
   # На Windows: .venv\Scripts\activate
   # На macOS/Linux: source .venv/bin/activate

   uv pip install -r requirements.txt
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

#### Шаг 3. Запуск Telegram Bot
1. Откройте новый терминал и перейдите в каталог `bot`:
   ```bash
   cd bot
   ```
2. Создайте файл `.env` на основе `bot/.env.example` (укажите `CORE_API_URL=http://127.0.0.1:8000/api/v1`).
3. Установите зависимости и запустите бота:
   ```bash
   uv venv
   # Активируйте виртуальное окружение:
   # На Windows: .venv\Scripts\activate
   # На macOS/Linux: source .venv/bin/activate

   uv pip install -r requirements.txt
   uv run main.py
   ```

---

## 🧪 Тестирование

Для запуска юнит-тестов (написанных с использованием `pytest` и `pytest-asyncio`):

```bash
# Запустить тесты с детальным выводом
uv run pytest -v
```

Конфигурация тестов автоматически изолирует зависимости (базу данных, Redis и API-вызовы в IntraService).

---

## 🔒 Безопасность и API-ключ

*   **`BOT_API_KEY`**: Все запросы от Telegram-бота к Core API защищены заголовком `X-Bot-Api-Key`. При несовпадении ключа Core API возвращает ошибку `401 Unauthorized`.
*   Если `BOT_API_KEY` не задан в окружении Core API, при запуске генерируется случайный временный ключ безопасности (выводится предупреждение в логах). Рекомендуется всегда задавать статический секретный ключ в файлах `.env`.
*   Пароли пользователей хранятся в БД в надежно зашифрованном виде (используется Fernet из библиотеки `cryptography`). При авторизации `login:password` конвертируется в Base64 и шифруется с помощью `ENCRYPTION_KEY`, что предотвращает утечку паролей в открытом виде при доступе к БД.
