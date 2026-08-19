# 📚 Документация проекта IntraLink

Центральный навигационный справочник по архитектуре, микросервисам и руководствам проекта **IntraLink**.

---

## 🗺️ Структура документации

```text
docs/
├── architecture.md          ← Единый источник правды об архитектуре, схемах и Data Flow
├── developer_guide.md       ← Настольная книга разработчика (для AI-агентов и инженеров)
│
├── services/                ← Документация по микросервисам проекта
│   ├── core-api/            ← Сервис API-шлюза (FastAPI, Web Admin, Worker)
│   ├── bot/                 ← Сервис Telegram-бота (aiogram 3.x, Redis Listener)
│   ├── ai-worker/           ← AI-классификатор и автоответчик (RAG + pgvector + LLM)
│   ├── printer-worker/      ← Автоустановка принтеров (WMI, WinRM, SMB)
│   └── helpdesk_agent/      ← Инструменты техподдержки и RAG в среде Antigravity (AGY)
│
└── external/                ← Документация внешних систем (не наш код)
    └── intraservice_api/    ← Справочник по REST API IntraService
```

---

## 🧭 Навигация по разделам

### 1. Архитектура и принципы
* **[Архитектура системы (`docs/architecture.md`)](architecture.md)** — схемы компонентов, слои, каналы связи (HTTP REST, Redis Pub/Sub), форматы полезной нагрузки и диаграммы последовательности (Data Flow).
* **[Руководство разработчика (`docs/developer_guide.md`)](developer_guide.md)** — ключевые архитектурные инварианты, правила безопасности, запуск юнит-тестов и отладка воркеров через `debug_tools`.

### 2. Сервисы и компоненты
* **[Core API Gateway](services/core-api/README.md)** — эндпоинты, JWT-сессии, шифрование учетных данных, фоновый опрос IntraService.
  * **[Справочник API](services/core-api/api_reference/README.md)** — спецификации REST эндпоинтов Core API.
* **[Telegram Bot](services/bot/README.md)** — архитектура хендлеров aiogram, обработка FSM и получение уведомлений.
* **[AI Worker](services/ai-worker/README.md)** — пайплайн интеллектуальной маршрутизации заявок с помощью RAG и LLM.
* **[Printer Worker](services/printer-worker/README.md)** — стратегии установки драйверов принтеров, WMI Bootstrap и сетевая валидация.
* **[Helpdesk Agent (AGY-Native)](services/helpdesk_agent/README.md)** — инструментарий I/O, сетевая диагностика хостов и семантический RAG-поиск в AGY.

### 3. Внешние интеграции
* **[Справочник IntraService REST API](external/intraservice_api/IntraService_API_Index.md)** — подробные спецификации по ресурсам IntraService (`/api/task`, `/api/service`, `/api/tasklifetime`, `/api/taskstatus`).
