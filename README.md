# IntraLink

Современный асинхронный Телеграм-бот, API-шлюз, панель администратора и автономные инструменты автоматизации для мониторинга, уведомлений и выполнения заявок в Helpdesk-системе **IntraService**.

Проект построен на базе модульного монорепозитория с единым ядром исполнения: интерфейсные слои (Telegram-бот, встроенный SPA `intra-web`), шлюз интеграции (`core-api`), автономный инженерный кокпит (`helpdesk_agent`) и локальный AI-инференс (`ollama`).

---

## 🏗 Архитектура системы

Система построена на событийно-ориентированном подходе и состоит из следующих компонентов:

1. **PostgreSQL 16 (с pgvector)** — база данных для хранения учетных данных пользователей, состояния мониторинга и семантической базы знаний RAG.
2. **Redis 7** — шина сообщений (Pub/Sub) для оперативной доставки push-уведомлений в Telegram.
3. **Core API (FastAPI)** — защищенный шлюз к REST API IntraService, управление базой данных, фоновый воркер опроса (`APScheduler`) и хостинг встроенной веб-панели управления `/admin`.
4. **Intra Web (`intra-web`)** — современная веб-панель управления (Single-File SPA на React 19, Vite, Tailwind CSS v4), компилируемая на этапе Multi-stage Docker build и раздаваемая прямо из Core API (`/admin`).
5. **Telegram Bot (aiogram 3.x)** — мобильный интерфейс инженера и заявителей для просмотра заявок и мгновенного получения уведомлений.
6. **Ollama (Qwen2.5:1.5B)** — локальный легковесный AI-инференс в Docker для мгновенной суммаризации переписок и помощи инженеру.
7. **Helpdesk Agent & Execution Hub** — автономный инженерный кокпит в среде Antigravity (AGY): детерминированный Rule Engine, экспресс-диагностика сети (ICMP/DNS/SMB), автоматизация Active Directory (`WLAN-WORKNET`) и удаленная установка принтеров (WinRM/WMI).

> [!NOTE]
> Схемы последовательности выполнения запросов (Data Flow) и подробная архитектурная диаграмма доступны в документе [`docs/architecture.md`](docs/architecture.md).

---

## 🚀 Основные возможности

*   **Персональная авторизация**: Безопасный вход под учетными данными IntraService.
*   **Безопасность данных**: Данные авторизации (логин/пароль) шифруются с помощью Fernet (`cryptography`) и сохраняются в PostgreSQL. Бот не хранит пароли локально.
*   **Событийный фоновый мониторинг (Redis Pub/Sub)**:
    *   Мгновенные уведомления обо всех **новых заявках** в системе IntraService.
    *   Персональные уведомления о **новых комментариях** и **сменах статусов** для заявок, где пользователь является назначенным исполнителем.
*   **📋 Мои заявки в Telegram**: Просмотр списка активных задач пользователя с поддержкой пагинации.
*   **⚡ Автоматизация Active Directory**: Автоматическая выдача Wi-Fi доступа (`WLAN-WORKNET`) с Single-DC Affinity, нормализацией инициалов и двухэтапным закрытием тикета (`31 ➔ 27 ➔ 29`).
*   **🧠 Локальная нейросеть (Qwen2.5:1.5B)**: Мгновенная AI-суммаризация цепочки комментариев инцидента за 1.2–1.5 сек (`helpdesk_tool.py summary <ID>`).
*   **🤖 Автономный Helpdesk-кокпит с RAG**: Прямой пакетный триаж очереди 1-й линии (`/triage`), поиск дубликатов (`/duplicates`), отмена ошибочных сервисов (`/redirect`), сетевая диагностика хостов (ICMP/DNS/SMB/WinRM) и семантический RAG-поиск по базе решений (`search-kb`).

---

## 🛠 Технологический стек

### Core API & База данных
*   **FastAPI** — веб-фреймворк для создания REST API.
*   **SQLAlchemy 2.0 (Async)** + **asyncpg** + **pgvector** — асинхронная работа с базой данных PostgreSQL и векторным поиском.
*   **APScheduler** — встроенный планировщик для периодического опроса IntraService.
*   **redis.asyncio** — асинхронная публикация событий в Redis.
*   **cryptography** — симметричное Fernet-шифрование учетных данных.
*   **aiohttp** — асинхронные запросы к REST API IntraService.

### Telegram Bot (aiogram)
*   **aiogram 3.x** — асинхронный фреймворк для Telegram Bot API.
*   **redis.asyncio** — асинхронное прослушивание событий Redis Pub/Sub.
*   **aiohttp** — взаимодействие с Core API.

### Helpdesk Agent & Execution Hub
*   **FastEmbed** — локальная ONNX-векторизация текста для мгновенного семантического поиска решений.
*   **Active Directory PowerShell** — автоматизация управления доменными группами и учетными записями.
*   **pywinrm / SMB** — удаленная установка и диагностика оргтехники.
*   **Ollama Client** — асинхронный клиент к локальной модели Qwen2.5:1.5B с Pydantic JSON-схемами.

---

## 📂 Структура проекта

```text
├── intra-web/                 # Фронтенд панели администратора (React 19, Tailwind CSS v4, Vite)
│   ├── src/                   # Исходный код React SPA
│   ├── package.json
│   └── vite.config.ts
│
├── core-api/                  # Сервис API-шлюза (FastAPI)
│   ├── app/
│   │   ├── database/          # Слой базы данных (SQLAlchemy модели, pgvector)
│   │   ├── routers/           # Контроллеры FastAPI (auth, tasks, admin, users)
│   │   ├── services/          # Интеграция с IntraService, фоновый worker, шифрование
│   │   └── static/admin/      # Скомпилированный SPA Admin UI (index.html)
│   ├── knowledge_base/        # Справочники драйверов принтеров (JSON)
│   ├── tests/                 # Автоматические тесты (pytest)
│   └── Dockerfile             # Multi-stage сборка (Node.js -> Python)
│
├── telegram-bot/              # Сервис Telegram-бота (aiogram 3.x)
│   ├── handlers/              # Обработчики сообщений и коллбеков
│   ├── services/              # HTTP-клиент Core API и подписчик Redis Pub/Sub
│   ├── main.py                # Точка входа в бота
│   └── Dockerfile
│
├── helpdesk_agent/            # Автономный Helpdesk-кокпит инженера в AGY
│   ├── executors/             # Исполнители в инфраструктуре (AD WLAN, WinRM Printers)
│   ├── llm/                   # Клиент локальной нейросети Ollama (Qwen2.5:1.5B)
│   ├── rules/                 # Детерминированный Rule Engine триажа (Wi-Fi, 1C, Пароли, Дубликаты)
│   ├── helpdesk_tool.py       # Главный CLI-инструмент (batch, wlan, summary, apply, diag, kb)
│   ├── kb.py                  # Семантический RAG-поиск (FastEmbed + pgvector)
│   ├── diagnostics.py         # Сетевая экспресс-диагностика хостов (ICMP, DNS, SMB:445, WinRM)
│   ├── intraservice_api.py    # Асинхронный клиент IntraService API
│   └── templates.json         # Утвержденные корпоративные шаблоны ответов
│
├── docs/                      # Документация проекта
│   ├── architecture.md        # Системная архитектура и Data Flow
│   └── services/              # Документация по сервисам (core-api, telegram-bot, helpdesk_agent)
│
├── docker-compose.yml         # Оркестрация контейнеров (postgres, redis, ollama, core-api, telegram-bot)
├── pyproject.toml             # Конфигурация для пакетного менеджера uv
└── uv.lock                    # Блокировка зависимостей uv
```

---

## 🚀 Быстрый запуск

### 1. Запуск инфраструктуры в Docker:
```bash
docker compose up -d
```

### 2. Использование Helpdesk CLI:
```bash
# Пакетный триаж открытых заявок 1-й линии:
uv run python helpdesk_agent/helpdesk_tool.py batch --limit 5

# Автоматическая выдача Wi-Fi доступа в AD и закрытие заявки:
uv run python helpdesk_agent/helpdesk_tool.py wlan 135713

# AI-суммаризация переписки инцидента через локальную нейросеть:
uv run python helpdesk_agent/helpdesk_tool.py summary 138512
```
