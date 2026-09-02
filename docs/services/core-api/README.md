# ⚡ Core API Gateway & Poller Daemon (`core-api`)

Центральный шлюз состояния, правил и интеграции системы **IntraLink** на базе **FastAPI**, **SQLAlchemy 2.0 (Async)**, **pgvector** и **Redis**.

---

## 📌 Зона ответственности

* **Единый источник правды (SSOT):**
  * Проксирование и изоляция вызовов к REST API IntraService.
  * Безопасное хранение зашифрованных учетных данных пользователей (Fernet).
  * Централизованный Rule Engine и хранение канонических шаблонов триажа (PostgreSQL).
  * Двухэтапный Hybrid RAG (`FastEmbed` + `pgvector` HNSW + Cross-Encoder Reranker).
  * Многоконтурный AI Hub (DLP-маскирование, Redis PII Vault, роутинг RED/YELLOW/GREEN).
  * Защитные механизмы: `Distributed Host Concurrency Locks` (`safety.py`) и `Dead Man's Switch`.
  * Фоновая экспресс-телеметрия хостов с нулевой задержкой (`host_telemetry.py`).
  * Хостинг скомпилированного React 19 SPA (`/admin`).

* **Фоновый демон опроса (`app.poller`):**
  * Автономный процесс (отдельный Docker-контейнер), опрашивающий IntraService от сервисного аккаунта.
  * Распределенный Leader Lock в Redis (`lock:poller_leader`, TTL 15s) для защиты от Split-Brain при масштабировании.
  * Гарантированная публикация событий в Redis Streams (`stream:intraservice_events`).

---

## 📖 Контракт API (Self-Documenting)

Актуальный контракт API поддерживается автоматически фреймворком FastAPI на основе Pydantic-схем:
* **Интерактивная документация Swagger UI:** `http://localhost:8000/docs`
* **Спецификация OpenAPI JSON:** `http://localhost:8000/openapi.json`
* **ReDoc:** `http://localhost:8000/redoc`

---

## 🔐 Аутентификация

* **Внутренние сервисы (`telegram-bot`, `helpdesk-cli`):** Pre-shared ключ в заголовке `X-Bot-Api-Key: <key>`.
* **Веб-панель управления (`/admin`):** HTTP-only сессионная cookie с JWT-токеном (`admin_session`), получаемая через `POST /admin/api/login`.
* **Внешний IntraService:** Basic Auth (`Authorization: Basic <base64>`).

---

## ⚙️ Конфигурация

Все переменные окружения задокументированы с примерами значений в файле:
👉 **[`core-api/.env.example`](../../../core-api/.env.example)**

---

## 🚀 Запуск сервиса

### Через Docker Compose (вместе с poller, postgres, redis, ollama):
```bash
docker compose up -d
```

### Локально для разработки:
```bash
cd core-api
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Запуск тестов:
```bash
uv run pytest core-api/tests/ -v
```
