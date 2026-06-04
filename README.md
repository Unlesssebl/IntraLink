# IntraBot-gemini 🤖💼

Современный асинхронный Телеграм-бот и API-шлюз для мониторинга, уведомлений и управления заявками в Helpdesk-системе **IntraService**. 

Проект построен на базе микросервисной архитектуры, разделяющей слой представления (Telegram-бот) и слой бизнес-логики/хранения данных (FastAPI Gateway).

---

## 🏗 Архитектура системы

Система построена на событийно-ориентированном подходе и состоит из четырех основных компонентов, разворачиваемых в единой сети:
1. **PostgreSQL** — реляционная база данных для хранения учетных данных пользователей IntraService (в зашифрованном виде) и состояния опроса (`last_task_id`, `last_check_time`).
2. **Redis** — шина сообщений (Pub/Sub). Обеспечивает асинхронную доставку уведомлений от Core API к Telegram-боту в реальном времени.
3. **Core API (FastAPI)** — защищенный микросервис-шлюз. Он управляет базой данных, взаимодействует с IntraService API, а также запускает встроенный фоновый **Worker** (`APScheduler`), который опрашивает IntraService и публикует новые события в Redis Pub/Sub.
4. **Telegram Bot (aiogram 3.x)** — легкий интерфейсный сервис, который принимает команды пользователей и запускает фоновый **Redis Listener** для получения событий из шины Redis и мгновенной отправки уведомлений в Telegram.

> [!NOTE]
> Схемы последовательности выполнения запросов (Data Flow) и подробная архитектурная диаграмма компонентов доступны в документе [architecture_schema.md](docs/architecture_schema.md).

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
*   **SQLAlchemy 2.0 (Async)** + **asyncpg** — асинхронная работа с базой данных PostgreSQL.
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
│   │   │   └── db.py          # Инициализация БД PostgreSQL / SQLite
│   │   ├── models/            # Схемы валидации Pydantic
│   │   │   └── schemas.py
│   │   ├── routers/           # Контроллеры FastAPI (endpoints)
│   │   │   ├── auth.py        # /auth/login, /auth/logout
│   │   │   ├── deps.py        # Зависимости (проверка API-ключа, сессии)
│   │   │   ├── tasks.py       # /tasks (заявки, комментарии, жизненный цикл)
│   │   │   └── users.py       # /users (профиль пользователя)
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
├── docs/                      # Документация по API IntraService и архитектуре
├── docker-compose.yml         # Оркестрация контейнеров (Postgres, Redis, Core API, Bot)
├── pyproject.toml             # Конфигурация для пакетного менеджера uv
└── uv.lock                    # Блокировка зависимостей uv
```

---

## ⚙️ Установка и запуск

### Способ 1. Запуск через Docker Compose (Рекомендуемый)

Для быстрого развертывания всей инфраструктуры (БД Postgres, API-шлюз и Telegram-бот) в контейнерах:

1. **Подготовьте файлы конфигурации**:
   Создайте `.env` в папке `./bot` на основе `bot/.env.example`:
   ```env
   BOT_TOKEN=ваш_токен_телеграм_бота
   CORE_API_URL=http://core-api:8000/api/v1
   BOT_API_KEY=надежный_ключ_для_авторизации_бота
   INTRAService_URL=https://ваш-домен.intraservice.ru/api/
   POLLING_INTERVAL=10
   ```

   Создайте `.env` в папке `./core-api` на основе `core-api/.env.example`:
   ```env
   INTRASERVICE_URL=https://ваш-домен.intraservice.ru/api/
   DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/intraservice
   BOT_API_KEY=надежный_ключ_для_авторизации_бота
   SSL_VERIFY=False
   # Ключ шифрования (сгенерируйте: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
   ENCRYPTION_KEY=ваш_сгенерированный_ключ
   ```

   > [!WARNING]
   > Значение `BOT_API_KEY` в обоих файлах конфигурации должно совпадать!

2. **Запустите службы**:
   Выполните команду в корне проекта:
   ```bash
   docker compose up --build -d
   ```
   Эта команда соберет Docker-образы для Core API и Бота, поднимет контейнер PostgreSQL и автоматически применит миграции (создаст необходимые таблицы) при первом запуске FastAPI.

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
