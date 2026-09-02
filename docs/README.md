# 📚 Документация проекта IntraLink

Центральный навигационный справочник по архитектуре, сервисам и руководствам проекта **IntraLink**.

---

## 🗺️ Структура документации

```text
docs/
├── architecture.md          ← Единый источник правды об архитектуре, схемах и Data Flow
├── developer_guide.md       ← Настольная книга разработчика и AI-агентов (инварианты и правила)
│
├── services/                ← Документация по сервисам проекта
│   ├── core-api/            ← Сервис API-шлюза (FastAPI, Web Admin, фоновый воркер)
│   ├── telegram-bot/        ← Сервис Telegram-бота (aiogram 3.x, слушатель Redis Pub/Sub)
│   ├── intra-web/           ← Веб-панель управления и мониторинга (React 19, Vite, Tailwind CSS v4)
│   └── helpdesk-cli/      ← Инженерный кокпит и инструменты автоматизации в AGY
│
└── external/                ← Документация внешних систем (не наш код)
    └── intraservice_api/    ← Справочник по REST API IntraService
```

---

## 🧭 Навигация по разделам

### 1. Архитектура и принципы
* **[Архитектура системы (`docs/architecture.md`)](architecture.md)** — схемы компонентов, слои, каналы связи (HTTP REST, Redis Pub/Sub), форматы полезной нагрузки и принципы надежного исполнения.
* **[Руководство разработчика (`docs/developer_guide.md`)](developer_guide.md)** — ключевые архитектурные инварианты, правила безопасности, межсервисные каналы и команды тестирования.

### 2. Сервисы и компоненты
* **[Core API Gateway](services/core-api/README.md)** — архитектура шлюза, JWT-сессии, шифрование учетных данных, фоновый опрос IntraService и ссылки на Swagger `/docs`.
* **[Telegram Bot](services/telegram-bot/README.md)** — архитектура хендлеров aiogram, обработка FSM и получение уведомлений.
* **[Intra Web (Admin Panel)](services/intra-web/README.md)** — React 19 SPA интерфейс мониторинга очередей, логов SSE и управления AI/RAG.
* **[Helpdesk Agent & Execution Hub](services/helpdesk-cli/README.md)** — модули прямого исполнения (AD WLAN, WinRM), сетевая диагностика хостов и семантический RAG-поиск в AGY.
  * **[Справочник Workflows и команд](services/helpdesk-cli/WORKFLOWS.md)** — слэш-команды (`/triage`, `/task`, `/diag`, `/screen`, `/kb`, `/sync`) и оперативные сценарии.

### 3. Внешние интеграции
* **[Справочник IntraService REST API](external/intraservice_api/IntraService_API_Index.md)** — справочник по внешнему REST API IntraService (`/api/task`, `/api/service`, `/api/tasklifetime`, `/api/taskstatus`).
