# Telegram Bot Service (aiogram 3.x)

Документация интерфейсного слоя Telegram-бота проекта **IntraLink**.

---

## 📌 Назначение
Бот выступает тонким интерфейсным клиентом для сотрудников технической поддержки и пользователей IntraService. Он обеспечивает:
1. Авторизацию пользователей по учетным данным IntraService.
2. Получение мгновенных Push-уведомлений о событиях (новые заявки, комментарии, смены статусов) через Redis Pub/Sub.
3. Интерактивный просмотр списка активных заявок с inline-пагинацией.

---

## 🏗 Архитектура и структура

```
bot/
├── handlers/              # Обработчики апдейтов Telegram (роутеры)
│   ├── auth.py            # Авторизация (/login, /logout, удаление сообщений с паролями)
│   ├── start_help.py      # Команды /start, /help, главное меню и клавиатуры
│   └── tickets.py         # Список «Мои заявки» с инлайн-пагинацией
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

1. **HTTP REST (`bot` $\rightarrow$ `core-api`):**
   - Запросы авторизации (`POST /auth/login`), проверка профиля (`GET /users/me`), получение заявок (`GET /tasks`).
   - Защита заголовком: `X-Bot-Api-Key: <BOT_API_KEY>`.
2. **Redis Pub/Sub (`core-api` $\rightarrow$ `bot`):**
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
cd bot
uv venv
uv pip install -r requirements.txt
uv run main.py
```
