# Core API Gateway & Background Worker

Сервис `core-api` — центральный шлюз системы IntraLink на базе **FastAPI**, **SQLAlchemy (Async)** и **APScheduler**.

---

## 📌 Основные функции

1. **API Gateway к IntraService:**
   * Проксирование и инкапсуляция запросов к REST API IntraService.
   * Безопасное хранение зашифрованных учетных данных пользователей (Fernet).
   * Авторизация входящих запросов от Telegram-бота по заголовку `X-Bot-Api-Key`.
   * Авторизация веб-панели управления по JWT-сессиям (`admin_session` cookie).

2. **Фоновый воркер опроса (`app/services/worker.py`):**
   * Периодический опрос IntraService от имени сервисного аккаунта (`check_updates`).
   * Кэширование каталога услуг в Redis (`worker:service_catalog`).
   * Публикация событий о новых задачах и изменениях статусов в Redis Pub/Sub (`task_events`).
   * AI-классификация новых заявок и автоматическая маршрутизация.
   * Управление процессами автоматической установки принтеров.

3. **Раздача встроенной панели управления (Admin UI):**
   * Хостинг скомпилированного SPA (`/admin`) из папки `static/admin/`.

---

## 📖 Контракт API (OpenAPI / Swagger)

Актуальный контракт API генерируется автоматически фреймворком FastAPI:
* **Интерактивная документация Swagger UI:** `http://localhost:8000/docs`
* **Спецификация OpenAPI JSON:** `http://localhost:8000/openapi.json`
* **ReDoc:** `http://localhost:8000/redoc`

Pydantic-схемы запросов и ответов находятся в `app/models/schemas.py`, а роутеры — в `app/routers/`:
* `auth.py` — авторизация пользователей (`/api/v1/auth/login`, `/logout`).
* `tasks.py` — операции с заявками (`/api/v1/tasks`, комментарии, статусы, трудозатраты).
* `users.py` — профиль пользователя (`/api/v1/users/me`).
* `service_tasks.py` — сервисные операции (`/api/v1/service/...`).
* `admin.py` — API веб-панели администратора (`/admin/api/...`).
* `ai_worker.py` — управление RAG-базой и AI-классификацией (`/admin/api/ai-worker/...`).

---

## ⚙️ Конфигурация

Все параметры конфигурации описаны в `app/config.py` и считываются из переменных окружения или `.env`:
* `INTRASERVICE_URL` — базовый URL инсталляции IntraService.
* `DATABASE_URL` — строка подключения PostgreSQL (`postgresql+asyncpg://...`).
* `REDIS_URL` — URL Redis брокера (`redis://localhost:6379/0`).
* `BOT_API_KEY` — pre-shared ключ для взаимодействия с Telegram-ботом.
* `ENCRYPTION_KEY` — ключ симметричного шифрования Fernet для паролей в БД.
* `INTRASERVICE_SERVICE_LOGIN` / `INTRASERVICE_SERVICE_PASSWORD` — сервисный аккаунт для фонового опроса.
* `JWT_SECRET` — ключ для подписи токенов администратора.
* `LITELLM_BASE_URL` / `LITELLM_API_KEY` — параметры шлюза LLM и эмбеддингов.

---

## 🧪 Запуск тестов

```bash
cd core-api
python -m pytest tests/ -v
```
