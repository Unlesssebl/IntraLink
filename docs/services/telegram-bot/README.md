# Telegram Bot Service (aiogram 3.x)

Интерфейсный слой Telegram-бота проекта **IntraLink** на базе **aiogram 3.x**.

---

## 📌 Назначение
Бот выступает тонким интерфейсным клиентом для сотрудников технической поддержки и пользователей IntraService:
1. Авторизация пользователей по учетным данным IntraService.
2. Получение мгновенных Push-уведомлений о событиях (новые заявки, комментарии, смены статусов) через шину Redis Pub/Sub (`task_events`).
3. Интерактивный просмотр списка активных заявок с inline-пагинацией («Мои заявки»).
4. Согласование и подтверждение задач установки принтеров.

---

## 🏗 Архитектура и структура

```
telegram-bot/
├── handlers/              # Обработчики апдейтов Telegram (роутеры)
│   ├── auth.py            # Авторизация (/login, /logout, удаление сообщений с паролями)
│   ├── start_help.py      # Команды /start, /help, главное меню и клавиатуры
│   ├── tickets.py         # Список «Мои заявки» с инлайн-пагинацией
│   └── printer_approvals.py # Интерактивные кнопки подтверждения установки принтеров
├── services/
│   ├── api_client.py      # Асинхронный HTTP-клиент к Core API (заголовок X-Bot-Api-Key)
│   └── redis_listener.py  # Фоновый слушатель событий Redis Pub/Sub (канал task_events)
├── config.py              # Загрузка настроек через Pydantic Settings
├── main.py                # Точка входа, lifespan сессий и запуск поллинга aiogram
├── utils.py               # Очистка HTML, форматирование Markdown для Telegram
├── Dockerfile             # Контейнеризация сервиса
└── requirements.txt       # Зависимости (aiogram, redis, aiohttp)
```

---

## 🔐 Безопасность и хранение секретов

> [!IMPORTANT]
> **Принцип изоляции секретов:**
> Бот **не хранит пароли** пользователей локально и не взаимодействует с базой данных напрямую. При вводе логина и пароля бот пересылает их по защищенному REST API в `Core API` (FastAPI), где они шифруются Fernet и сохраняются в PostgreSQL.
> Сообщение пользователя, содержащее пароль, **немедленно удаляется из истории чата Telegram** сразу после считывания хендлером.

---

## 📡 Взаимодействие с Core API и Redis

1. **HTTP REST (`telegram-bot` $\rightarrow$ `core-api`):**
   - Запросы авторизации (`POST /api/v1/auth/login`), проверка профиля (`GET /api/v1/users/me`), получение заявок (`GET /api/v1/tasks`).
   - Защита заголовком: `X-Bot-Api-Key: <BOT_API_KEY>`.
2. **Redis Pub/Sub (`core-api` $\rightarrow$ `telegram-bot`):**
   - Слушатель `redis_listener.py` подписан на канал событий `task_events`.
   - При появлении события парсит `tg_user_id` и мгновенно отправляет форматированное сообщение пользователю через `Bot.send_message()`.

---

## 🚀 Запуск сервиса

### Через Docker Compose:
```bash
docker compose --profile with-bot up --build -d bot
```

### Локально для разработки:
```bash
cd telegram-bot
uv venv
uv pip install -r requirements.txt
uv run python main.py
```
