# Инструкции по работе с IntraService API и архитектурой IntraBot (Микросервисная архитектура)

Этот файл является обязательным руководством для любого AI-агента в данном воркспейсе. Он описывает структуру проекта, особенности интеграции с API IntraService и правила работы с кодовой базой после разделения на Core API и Telegram Bot.

---

## 1. Обзор новой архитектуры монорепо

Проект разделен на два независимых сервиса, взаимодействующих по сети:
1. **Core API Service (`core-api/`)** — FastAPI-микросервис, выступающий шлюзом к API IntraService. Он отвечает за хранение учетных данных пользователей, сессии авторизации и непосредственно отправляет запросы в IntraService.
2. **Telegram Bot Service (`bot/`)** — aiogram 3.x бот. Он отвечает исключительно за интерфейс взаимодействия с пользователем в Telegram и периодический опрос обновлений через планировщик.

```
intraservice-tg-bot/
├── bot/                         # Код Telegram-бота
│   ├── handlers/                # Хэндлеры команд (/start, /login, /mytickets)
│   ├── services/
│   │   ├── api_client.py        # Клиент для связи с Core API
│   │   └── scheduler.py         # Опрос Core API для отправки уведомлений
│   ├── utils.py                 # Общие утилиты бота
│   ├── config.py                # Конфигурация бота
│   └── main.py                  # Точка входа бота
│
├── core-api/                    # Код Core API Service
│   ├── app/
│   │   ├── database/
│   │   │   └── db.py            # База данных SQLAlchemy (async PostgreSQL)
│   │   ├── models/
│   │   │   └── schemas.py       # Pydantic-схемы запросов и ответов
│   │   ├── routers/             # Эндпоинты API (auth, tasks, users)
│   │   ├── services/
│   │   │   └── intraservice.py  # Прямой HTTP-клиент к IntraService API
│   │   ├── config.py            # Конфигурация Core API
│   │   └── main.py              # Точка входа FastAPI
│   ├── Dockerfile
│   └── requirements.txt
│
├── docs/                        # Документация по проекту
└── docker-compose.yml           # Оркестрация сервисов (bot + core-api + postgres + volume)
```

---

## 2. Реализация авторизации и аутентификации

### Взаимодействие Бот -> Core API
Бот общается с Core API по протоколу HTTP REST. Для аутентификации запросов от бота используется pre-shared API-ключ, передаваемый в заголовке:
`X-Bot-Api-Key: <секретный_ключ>`

Проверка ключа на стороне Core API реализована в виде зависимости FastAPI в [deps.py](file:///c:/Users/belikov.a/Desktop/Акты, документы/Work/!Projects/intraservice-tg-bot/core-api/app/routers/deps.py) с использованием безопасного по времени сравнения `secrets.compare_digest` для предотвращения атак по времени (Timing Attacks).

### Авторизация в IntraService
Вся работа с Basic Auth IntraService инкапсулирована в Core API:
- Заголовок Basic Auth (`Authorization: Basic <base64(login:password)>`) формируется в Core API на основе учетных данных пользователя, хранящихся в БД.
- В БД Core API сохраняются `is_login` и `is_password_b64` (зашифрованный токен, содержащий закодированный в base64 пароль). Бот больше не имеет доступа к учетным данным IntraService напрямую.

---

## 3. Формат запросов, ответов и работы с датами

- **Base URL Core API:** Значение берется из переменной окружения `CORE_API_URL` в боте (обычно `http://core-api:8000/api/v1` в Docker или `http://127.0.0.1:8000/api/v1` при локальном тестировании).
- **Формат даты в API:** Даты могут приходить в различных форматах (ISO, UTC, локальные строки).
  - На стороне Core API и бота используется функция `parse_api_date(date_str)` из [utils.py](file:///c:/Users/belikov.a/Desktop/Акты, документы/Work/!Projects/intraservice-tg-bot/bot/utils.py) или [intraservice.py](file:///c:/Users/belikov.a/Desktop/Акты, документы/Work/!Projects/intraservice-tg-bot/core-api/app/services/intraservice.py) для безопасного приведения строк к объектам `datetime`.
  - При передаче временных фильтров в API (параметры `CreatedMoreThan` или `ChangedMoreThan`) используется формат `YYYY-MM-DD HH:MM`.

---

## 4. Спецификация API (Core API Gateway)

Документация Swagger UI автоматически доступна по адресу `http://127.0.0.1:8000/docs`.

### Авторизация
- `POST /api/v1/auth/login` — Принимает `tg_user_id`, `login`, `password`. Проверяет креды в IntraService и сохраняет сессию пользователя.
- `DELETE /api/v1/auth/logout` — Удаляет сессию и креды пользователя из БД по `tg_user_id`.

### Задачи
- `GET /api/v1/tasks` — Получает список задач для пользователя по `tg_user_id` (принимает любые фильтры: `statusId`, `page`, `pagesize`, `CreatedMoreThan` и т.д.).
- `GET /api/v1/tasks/{task_id}/lifetime` — Получает историю изменений и комментарии к задаче с фильтрацией по `tg_user_id`.
- `GET /api/v1/statuses` — Получает справочник статусов IntraService для сопоставления.

### Пользователи и мониторинг
- `GET /api/v1/users` — Возвращает список всех зарегистрированных пользователей. Используется планировщиком бота для периодического опроса.
- `GET /api/v1/users/{tg_user_id}` — Возвращает профиль конкретного пользователя (логин, ID в IntraService, состояние поллинга).
- `PATCH /api/v1/users/{tg_user_id}/state` — Обновляет состояние опроса (`last_task_id`, `last_comment_id`, `last_check_time`).

---

## 5. Структура Базы Данных (SQLAlchemy / PostgreSQL)

База данных PostgreSQL ведется на стороне Core API. Описание полей таблицы `users` ([db.py](file:///c:/Users/belikov.a/Desktop/Акты, документы/Work/!Projects/intraservice-tg-bot/core-api/app/database/db.py)):
- `tg_user_id` (BigInteger, Primary Key) — ID пользователя в Telegram.
- `is_login` (String) — Логин пользователя.
- `is_password_b64` (String) — Зашифрованная с помощью Fernet (`ENCRYPTION_KEY`) строка, содержащая закодированную в Base64 пару `login:password` для отправки Basic Auth.
- `is_user_id` (Integer) — Внутренний ID пользователя в IntraService.
- `last_task_id` (Integer) — ID последней обработанной задачи (для предотвращения дублирования уведомлений).
- `last_comment_id` (Integer) — ID последнего отправленного комментария.
- `last_check_time` (String) — Время последней фоновой проверки в формате ISO/строки.

---

## 6. Рекомендации по разработке и безопасности

1. **Асинхронность:** Все I/O операции (база данных через SQLAlchemy `AsyncSession` и вызовы к API через `aiohttp`) строго должны быть асинхронными с использованием `await`.
2. **Безопасность учетных данных:** Вся ответственность за хранение паролей лежит на Core API. Все входящие сообщения с паролями в боте удаляются из истории чата сразу после считывания в [auth.py](file:///c:/Users/belikov.a/Desktop/Акты, документы/Work/!Projects/intraservice-tg-bot/bot/handlers/auth.py).
3. **API Key:** Запрещено хардкодить `BOT_API_KEY` в коде. При развертывании в продакшене он должен передаваться строго через переменные окружения.
4. **Порты при тестировании:** При локальной проверке сервисы должны слушать только на localhost (`127.0.0.1`). Слушать на `0.0.0.0` разрешается только внутри Docker-контейнеров.

---

## 7. Контейнеризация и Docker Compose

Файл `docker-compose.yml` описывает запуск трех сервисов:
- `postgres` запускает официальный образ `postgres:16-alpine`, хранит данные в именованном томе `postgres_data` и пробрасывает порт `5432`.
- `core-api` собирается из `./core-api`, зависит от `postgres` (`depends_on`), обращается к базе по строке подключения `postgresql+asyncpg://postgres:postgres@postgres:5432/intraservice`.
- `bot` собирается из `./bot`, зависит от `core-api` (`depends_on`), обращается к Core API по внутреннему DNS-имени `http://core-api:8000/api/v1`.
