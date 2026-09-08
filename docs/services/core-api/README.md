# ⚡ Core API Gateway & Poller Daemon (`core-api`)

Центральный шлюз состояния, правил и интеграции системы **IntraLink** на базе **FastAPI**, **SQLAlchemy 2.0 (Async)**, **pgvector** и **Redis**.

---

## 📌 Зона ответственности

* **Единый источник правды (SSOT):**
  * Проксирование и изоляция вызовов к REST API IntraService.
  * Безопасное хранение зашифрованных учетных данных пользователей и инфраструктуры (Fernet) через единый Credentials Vault (`vault.py` + PostgreSQL `system_settings` + авто-прогрев Redis).
  * Декомпозированный сервис триажа очереди `TriageService` и менеджер сессий `TriageSessionManager` (`core-api/app/services/`).
  * Единая шина команд (Command Bus) и декларативный реестр действий `ActionRegistry` с Pydantic JSON-схемами и типами целей.
  * Динамический движок политик `PolicyEngine` с аппаратным Killswitch (`disabled` -> HTTP 403), HitL (`confirm`) и автоматическим режимом (`auto`).
  * Централизованный Rule Engine и хранение канонических шаблонов триажа в PostgreSQL (`triage_templates` + `rules_admin.py`).
  * Двухэтапный Hybrid RAG (`bge-m3` 1024 dim + `pgvector` HNSW + нативный PostgreSQL FTS `tsvector russian` со `setweight` и GIN-индексом + Cross-Encoder `BAAI/bge-reranker-v2-m3` на FastEmbed ONNX Runtime с прогревом в Docker).
  * Строгий гейтинг источников рекомендаций `is_valid_solution_source` (отсечение статуса 30 «Отменена» и неинформативных отписок) и устранение обходов порогов реранкера.
  * Инвалидация кэша выдачи RAG по ревизии корпуса `kb:corpus:revision` в Redis.
  * Офлайн-модуль оценки качества поиска и безопасности действий `evals.py` (Recall@5, Hit@5, MRR@5, no_match_accuracy, Release Gate).
  * Многоконтурный адаптивный AI Hub (LiteLLM Proxy с ротацией ключей, Gemini 3.5 Flash, DLP-маскирование, Redis PII Vault, роутинг RED/YELLOW/GREEN).
  * Адаптивный поиск Ollama и телеметрия GPU: прозрачное подключение к хостовой Ollama (`host.docker.internal:11434`), Docker-сети или локальному порту, автодетект NVIDIA RTX 3050 (CUDA) и AMD (Vulkan/DirectML) в `/api/v1/ai/health`.
  * Защитные механизмы: `Distributed Host Concurrency Locks` (`safety.py`) и `Dead Man's Switch`.
  * Фоновая экспресс-телеметрия хостов с нулевой задержкой (`host_telemetry.py`).
  * Прямое управление доменными объектами Active Directory по протоколу LDAPS (порт 636) через Linux Core API (`active_directory.py`).
  * Хранение конфигурации и зашифрованных учетных данных (Fernet) в таблице `system_settings` (`admin_settings.py` + `vault.py`).
  * Резервный Fallback One-Liner генератор для экспресс-установки оборудования (`self_service.py`).
  * Модерация и администрирование базы знаний RAG (просмотр прецедентов, Blacklisting, статистика, дерево услуг) в `kb_admin.py`.
  * Хостинг скомпилированного двухконтурного React 19 SPA (`/operator-panel` и `/admin` с единой вкладкой управления доступом и экспресс-диагностикой WinRM/LDAPS).

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

### Через Docker Compose (вместе с litellm, poller, postgres, redis):
```bash
docker compose up -d
```
> По умолчанию проект подключается к хостовой Ollama (`http://host.docker.internal:11434`). Если требуется запустить Ollama внутри Docker, используйте соответствующий оверлей (`docker-compose.ollama-cpu.yml`, `docker-compose.ollama-nvidia.yml` или `docker-compose.ollama-vulkan.yml`).

### Локально для разработки:
```bash
cd core-api
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Запуск тестов:
```bash
uv run pytest core-api/tests/ -v
```
