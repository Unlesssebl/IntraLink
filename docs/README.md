# 📚 Документация проекта IntraLink

Центральный навигационный справочник по архитектуре, сервисам, регламентам и планам развития платформы **IntraLink**.

---

## 🚀 Активные архитектурные этапы

1. **[Переход на сценарный контур](plans/scenario-orchestration/transition-roadmap.md):** перевод жизненного цикла заявок на конечный автомат `TicketRunOrchestrator`, сбор фактов (`FactBag`) с контролем источника правды (Provenance), компиляция решений `DecisionCompiler` и безопасная раскатка через режимы `shadow` / `canary` / `active`. Спецификация: [сценарный план](plans/scenario-orchestration/execution-plan.md).
2. **[Асинхронная платформа пакетного триажа (ADR 0003)](adr/0003-asynchronous-batch-triage-platform.md):** перевод анализа очередей на неблокирующий шлюз `202 Accepted`, Redis Job Queue, Single-flight lock и стриминг прогресса через SSE (`/api/v1/events/stream`).
3. **[Эволюция модульной Worker Platform](plans/worker-platform-evolution.md):** безопасное исполнение действий, модульный SDK хэндлеров, маршрутизация fleet и пилот удаленной установки принтеров.
4. **[Desktop Companion](services/desktop-companion/README.md):** нативный Windows tray-helper на Tauri 2 для запуска DameWare, LiteManager и RDP из веб-интерфейса по одноразовым deep links Core API.
5. **[Roadmap качества RAG](plans/rag-quality-roadmap.md):** гибридный поиск (pgvector BGE-M3 + FTS Russian tsvector) и Cross-Encoder `BAAI/bge-reranker-v2-m3` на FastEmbed (Recall@5 97.5%, Hit@5 100%).

---

## 🗺️ Структура директории `docs/`

```text
docs/
├── README.md                      ← Центральный навигатор и статусная матрица
├── architecture.md                ← SSOT архитектуры, схемы C4, шины, контуры безопасности
├── developer_guide.md             ← Инварианты, правила и настольная книга инженера/агента
├── brandbook.md                   ← Дизайн-система, токены интерфейса, Zero-Emoji Policy
├── roadmap.md                     ← Стратегический продуктовый план развития 2026–2027
│
├── adr/                           ← Архитектурные решения (Architecture Decision Records)
│   ├── 0001-transactional-command-platform.md
│   ├── 0002-modular-worker-platform.md
│   └── 0003-asynchronous-batch-triage-platform.md
│
├── services/                      ← Паспорта сервисов монорепозитория
│   ├── core-api/                  ← FastAPI Gateway, Poller, Scenario Orchestrator, RAG
│   ├── execution-worker/          ← Windows Headless Daemon (AD WLAN/User, WinRM/Printers)
│   ├── desktop-companion/         ← Нативный Windows tray-helper (Tauri 2, Rust)
│   ├── helpdesk-cli/              ← Машинный Tooling SDK агента Antigravity (AGY)
│   ├── intra-web/                 ← Веб-панель управления и мониторинга (React 19, Vite)
│   ├── shared/                    ← Единый SSOT пакет (нормализация, экспресс-диагностика)
│   └── telegram-bot/              ← Мобильный пейджер и HITL-согласования (aiogram 3.x)
│
├── plans/                         ← Планы развития и дорожные карты
│   ├── scenario-orchestration/    ← Переход на модульный сценарный контур
│   ├── worker-platform/           ← Эволюция платформы исполнения воркеров
│   ├── rag-quality-roadmap.md     ← План и верификация качества RAG
│   ├── intent-classification.md   ← Трехуровневый гибридный каскад классификации
│   ├── desktop-companion-blueprint.md ← Концептуальный блупринт Desktop Companion
│   └── archive/                   ← Архив реализованных планов и чекпоинтов
│
├── runbooks/                      ← Инструкции по эксплуатации и безопасность
│   ├── identity-bootstrap.md      ← Инициализация сервисной учетной записи AD
│   ├── triage-lifecycle.md        ← Регламент ANALYSIS_REVISION и кэширования решений
│   └── production-readiness-ai.md ← Границы автономности, DLP и offline eval-gate
│
├── web-autopilot/                 ← Регламенты пилота и приёмки операторского веб-пульта
│   ├── product-concept.md
│   ├── lifecycle.md
│   ├── operations.md
│   ├── staging-acceptance.md
│   └── stabilization-plan.md
│
├── reports/                       ← Чекпоинты модернизации и отчеты эвалов
│   ├── 2026-09-07-decisioning-checkpoint.md
│   └── evals/                     ← Логи прогонов оценки точности
│
├── presentations/                 ← Презентации и материалы для демонстрации
│   └── SPEAKER_GUIDE.md
│
└── external/                      ← Документация сторонних систем (не наш код)
    └── intraservice_api/          ← Справочник REST API IntraService (49 спецификаций)
```

---

## 📊 Матрица актуальности документации

| Раздел | Документ | Статус | Назначение |
|---|---|:---:|---|
| **Ядро** | [architecture.md](architecture.md) | 🟢 Актуален | Целевая архитектура, схемы C4, шины, контуры DLP |
| **Ядро** | [developer_guide.md](developer_guide.md) | 🟢 Актуален | Настольная книга архитектурных инвариантов и правил |
| **Ядро** | [brandbook.md](brandbook.md) | 🟢 Актуален | Манифест информации, токены и Zero-Emoji Policy |
| **Ядро** | [roadmap.md](roadmap.md) | 🟢 Актуален | Стратегические горизонты 2026–2027 |
| **ADR** | [ADR 0001](adr/0001-transactional-command-platform.md) | 🟢 Принят | Транзакционная шина команд и Transactional Outbox |
| **ADR** | [ADR 0002](adr/0002-modular-worker-platform.md) | 🟢 Принят | Модульная архитектура воркеров и Functional Core |
| **ADR** | [ADR 0003](adr/0003-asynchronous-batch-triage-platform.md) | 🟢 Принят | Асинхронная платформа пакетного анализа очереди |
| **Сервисы** | [Core API](services/core-api/README.md) | 🟢 Актуален | Шлюз состояния, Poller, RAG, AI Hub, /admin |
| **Сервисы** | [Execution Worker](services/execution-worker/README.md) | 🟢 Актуален | Windows-демон исполнения доменных задач |
| **Сервисы** | [Desktop Companion](services/desktop-companion/README.md) | 🟢 Актуален | Windows tray-клиент (Tauri 2, DameWare, RDP, LM) |
| **Сервисы** | [Helpdesk CLI](services/helpdesk-cli/README.md) | 🟢 Актуален | Tooling SDK агента Antigravity (AGY) |
| **Сервисы** | [Intra Web](services/intra-web/README.md) | 🟢 Актуален | React 19 веб-пульт оператора и консоль администратора |
| **Сервисы** | [Shared Package](services/shared/README.md) | 🟢 Актуален | Единый источник алгоритмов нормализации и зондов |
| **Сервисы** | [Telegram Bot](services/telegram-bot/README.md) | 🟢 Актуален | Мобильный пейджер и согласования по Redis Streams |
| **Регламенты** | [Triage Lifecycle](runbooks/triage-lifecycle.md) | 🟢 Актуален | Управление `ANALYSIS_REVISION`, Redis-локи и кэши |
| **Регламенты** | [Identity Bootstrap](runbooks/identity-bootstrap.md) | 🟢 Актуален | Настройка сервисной учетной записи и доступов AD |
| **Регламенты** | [AI Production Readiness](runbooks/production-readiness-ai.md) | 🟢 Актуален | Границы автономности ИИ, DLP-фильтры и eval-gate |
| **Планы** | [Scenario Transition](plans/scenario-orchestration/transition-roadmap.md) | 🟡 В работе | 5-этапный роадмап выкатки сценарного контура |
| **Планы** | [Scenario Execution Plan](plans/scenario-orchestration/execution-plan.md) | 🟡 В работе | Спецификация FSM `TicketRunOrchestrator` |
| **Планы** | [Worker Platform Evolution](plans/worker-platform-evolution.md) | 🟡 В работе | План масштабирования и модульности воркеров |
| **Планы** | [RAG Quality Roadmap](plans/rag-quality-roadmap.md) | 🟢 Реализован | Внедрение FastEmbed Cross-Encoder и FTS |
| **Планы** | [Intent Classification](plans/intent-classification.md) | 🟡 В работе | Трехуровневый каскад классификации намерений |

---

## 🧭 Навигация по разделам

### 1. Архитектура и стандарты разработки
* **[Архитектура системы (`architecture.md`)](architecture.md)** — схемы компонентов, слои, каналы связи (HTTP REST, Redis Streams), контуры безопасности DLP и принципы надежного исполнения.
* **[Руководство разработчика (`developer_guide.md`)](developer_guide.md)** — ключевые архитектурные инварианты («ПОЧЕМУ»), правила безопасности, межсервисные шины и стандарты кода.
* **[Дорожная карта развития (`roadmap.md`)](roadmap.md)** — стратегические горизонты развития 2026–2027 (AIOps, Hybrid RAG, Outage Detection).
* **[Брендбук и дизайн-система (`brandbook.md`)](brandbook.md)** — манифест информации, цветовые токены и Zero-Emoji Policy.

### 2. Архитектурные решения (ADR)
* **[ADR 0001: Transactional Command Platform](adr/0001-transactional-command-platform.md)** — транзакционный Outbox, идемпотентность и гарантированная доставка команд.
* **[ADR 0002: Modular Worker Platform](adr/0002-modular-worker-platform.md)** — модульные хэндлеры действий, паттерн Functional Core / Imperative Shell.
* **[ADR 0003: Asynchronous Batch Triage Platform](adr/0003-asynchronous-batch-triage-platform.md)** — неблокирующий анализ очередей (HTTP 202, Redis Hash/Set, SSE).

### 3. Паспорта сервисов монорепозитория
* **[Core API Gateway & Poller](services/core-api/README.md)** — центральный шлюз, Poller с Leader Lock, RAG, AI Hub, сценарный оркестратор и Swagger `/docs`.
* **[Execution Worker](services/execution-worker/README.md)** — Windows-демон исполнения, Consumer Group, DLQ, Heartbeat и Human-in-the-Loop.
* **[Desktop Companion](services/desktop-companion/README.md)** — нативный Windows tray-helper (Tauri 2) для локального запуска DameWare, LiteManager и RDP.
* **[Helpdesk CLI](services/helpdesk-cli/README.md)** — машинный Tooling SDK агента AGY при обработке слэш-команд.
  * **[Справочник Workflows и команд](services/helpdesk-cli/WORKFLOWS.md)** — сценарии взаимодействия инженера с AI-агентом (`/triage`, `/task`, `/diag`, `/screen`, `/kb`, `/sync`).
* **[Shared Package (SSOT)](services/shared/README.md)** — единый источник правды для нормализации оборудования, сетевых экспресс-зондов и сериализации.
* **[Telegram Bot](services/telegram-bot/README.md)** — тонкий мобильный клиент, гарантированная доставка Redis Streams (`XAUTOCLAIM`).
* **[Intra Web (Operator & Admin Panel)](services/intra-web/README.md)** — React 19 интерфейс оператора очереди и консоль мониторинга.

### 4. Регламенты эксплуатации (Runbooks)
* **[Triage Lifecycle Operations](runbooks/triage-lifecycle.md)** — регламент управления ревизией логики анализа (`ANALYSIS_REVISION`), Redis-локи и правила повторного анализа.
* **[Identity Bootstrap](runbooks/identity-bootstrap.md)** — регламент инициализации доменной сервисной учетной записи и проверки прав Active Directory.
* **[AI Production Readiness](runbooks/production-readiness-ai.md)** — границы автономности ИИ-модулей, защитные инварианты DLP и offline eval-gate.

### 5. Концепция и пилот веб-пульта (`web-autopilot/`)
* **[Единая концепция продукта](web-autopilot/product-concept.md)** — роли Rule Engine, RAG и AI, два режима, путь заявки и Web UI.
* **[Карта доведения](web-autopilot/concept-alignment.md)** — анализ расхождений реализации и следующий этап работ.
* **[Спецификация поведения и жизненный цикл](web-autopilot/lifecycle.md)** — режимы, запуск, остановка, причины отмены и обязательные ограничения.
* **[План стабилизации](web-autopilot/stabilization-plan.md)** — проверки перед пилотом, отказоустойчивость и эксплуатационные границы.
* **[Staging-приёмка](web-autopilot/staging-acceptance.md)** — проверка на выделенных тестовых заявке, компьютере и принтере.
* **[Настройка и запуск](web-autopilot/operations.md)** — сервисная учётная запись, шаблоны, политики, включение и восстановление.

### 6. Внешние интеграции
* **[Справочник IntraService REST API](external/intraservice_api/IntraService_API_Index.md)** — спецификация контрактов внешнего REST API IntraService (`/api/task`, `/api/service`, `/api/tasklifetime`, `/api/taskstatus`).
