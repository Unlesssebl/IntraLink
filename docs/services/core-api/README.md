# ⚡ Core API Gateway & Poller Daemon (`core-api`)

Центральный шлюз состояния, правил и интеграции системы **IntraLink** на базе **FastAPI**, **SQLAlchemy 2.0 (Async)**, **pgvector** и **Redis**.

---

## 📌 Зона ответственности

* **Единый источник правды (SSOT):**
  * Проксирование и изоляция вызовов к REST API IntraService.
  * Безопасное хранение зашифрованных учетных данных пользователей (Fernet).
  * Централизованный Rule Engine и хранение канонических шаблонов триажа (PostgreSQL).
  * Двухэтапный Hybrid RAG (`LiteLLM` / `gemini-embedding-2` 3072 dim + `pgvector` HNSW + Cross-Encoder Reranker с адаптивным GPU-ускорением через ONNX Runtime CUDA/DirectML/CPU).
  * Многоконтурный адаптивный AI Hub (LiteLLM Proxy с ротацией ключей, Gemini 3.5 Flash, DLP-маскирование, Redis PII Vault, роутинг RED/YELLOW/GREEN).
  * Адаптивный поиск Ollama и телеметрия GPU: прозрачное подключение к хостовой Ollama (`host.docker.internal:11434`), Docker-сети или локальному порту, автодетект NVIDIA RTX 3050 (CUDA) и AMD (Vulkan/DirectML) в `/api/v1/ai/health`.
  * Защитные механизмы: `Distributed Host Concurrency Locks` (`safety.py`) и `Dead Man's Switch`.
  * Фоновая экспресс-телеметрия хостов с нулевой задержкой (`host_telemetry.py`).
  * Прямое управление доменными объектами Active Directory по протоколу LDAPS (порт 636) через Linux Core API (`active_directory.py`).
  * Хранение конфигурации и зашифрованных учетных данных (Fernet) в таблице `system_settings` (`admin_settings.py`).
  * Резервный Fallback One-Liner генератор для экспресс-установки оборудования (`self_service.py`).
  * Модерация и администрирование базы знаний RAG (просмотр прецедентов, Blacklisting, статистика, дерево услуг) в `kb_admin.py`.
  * Хостинг скомпилированного двухконтурного React 19 SPA (`/operator-panel` и `/admin`).

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
* **Панель системного администратора (`/admin`):** Bearer JWT-токен (`role: "admin"`), выдаваемый по мастер-паролю администратора через `POST /api/v1/admin/auth/login`.
* **Операторская панель (`/operator-panel`):** Доступ к мониторингу очередей 1-й линии и экспресс-действиям.
* **Внешний IntraService:** Basic Auth (`Authorization: Basic <base64>`).

---

## ⚙️ Конфигурация

Все переменные окружения задокументированы с примерами значений в едином файле в корне проекта:
👉 **[`.env.example`](../../../.env.example)**

---

## 🚀 Запуск сервиса

### Через Docker Compose (вместе с litellm, poller, postgres, redis, ollama):
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
