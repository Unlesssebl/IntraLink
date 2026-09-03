# 📚 Документация проекта IntraLink

Центральный навигационный справочник по архитектуре, сервисам и руководствам проекта **IntraLink**.

---

## 🗺️ Структура документации

```text
docs/
├── architecture.md          ← Единый источник правды об архитектуре, схемах и Data Flow
├── developer_guide.md       ← Настольная книга разработчика и AI-агентов (инварианты и правила)
├── roadmap.md               ← Стратегический план развития (Product & AI Roadmap)
├── brandbook.md             ← Корпоративная дизайн-система и правила UI
│
├── services/                ← Паспорта сервисов проекта
│   ├── core-api/            ← Центральный шлюз (FastAPI), Poller с Leader Lock, RAG, AI Hub, /admin
│   ├── execution-worker/    # Windows Headless Daemon исполнения задач (AD WLAN, WinRM, SMB)
│   ├── helpdesk-cli/        ← Машинный SDK и tooling исключительно для AI-агента Antigravity (AGY)
│   ├── shared/              ← Единый пакет общих алгоритмов (SSOT): normalizer, diagnostics
│   ├── telegram-bot/        ← Мобильный пейджер и HITL-согласования (aiogram 3.x, Redis Streams)
│   └── intra-web/           ← Веб-панель управления и мониторинга (React 19, Vite, Tailwind CSS v4)
│
└── external/                ← Документация внешних систем (не наш код)
    └── intraservice_api/    ← Справочник по REST API IntraService
```

---

## 🧭 Навигация по разделам

### 1. Архитектура и стандарты разработки
* **[Архитектура системы (`architecture.md`)](architecture.md)** — схемы компонентов, слои, каналы связи (HTTP REST, Redis Streams), контуры безопасности DLP и принципы надежного исполнения.
* **[Руководство разработчика (`developer_guide.md`)](developer_guide.md)** — ключевые архитектурные инварианты («ПОЧЕМУ»), правила безопасности, межсервисные шины и стандарты кода.
* **[Дорожная карта развития (`roadmap.md`)](roadmap.md)** — горизонты развития 2026–2027 (AIOps, Hybrid RAG, Outage Detection).
* **[Брендбук и дизайн-система (`brandbook.md`)](brandbook.md)** — манифест информации, цветовые токены и Zero-Emoji Policy.

### 2. Паспорта сервисов и пакетов
* **[Core API Gateway & Poller](services/core-api/README.md)** — архитектура шлюза, Leader Lock, RAG, AI Hub и ссылки на Swagger `/docs`.
* **[Execution Worker](services/execution-worker/README.md)** — Windows-демон, очереди Consumer Group, DLQ, Heartbeat и Human-in-the-Loop.
* **[Helpdesk CLI](services/helpdesk-cli/README.md)** — машинный Tooling SDK агента AGY при обработке слэш-команд.
  * **[Справочник Workflows и команд](services/helpdesk-cli/WORKFLOWS.md)** — сценарии взаимодействия инженера с AI-агентом (`/triage`, `/task`, `/diag`, `/screen`, `/kb`, `/sync`).
* **[Shared Package (SSOT)](services/shared/README.md)** — единый источник правды для нормализации оборудования, сетевых экспресс-зондов и сериализации.
* **[Telegram Bot](services/telegram-bot/README.md)** — тонкий клиент, гарантированная доставка Redis Streams (`XAUTOCLAIM`).
* **[Intra Web (Admin Panel)](services/intra-web/README.md)** — React 19 Single-File SPA интерфейс мониторинга очередей и логов.

### 3. Внешние интеграции
* **[Справочник IntraService REST API](external/intraservice_api/IntraService_API_Index.md)** — контракт внешнего REST API IntraService (`/api/task`, `/api/service`, `/api/tasklifetime`, `/api/taskstatus`).
