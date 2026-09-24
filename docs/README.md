# 📚 Документация проекта IntraLink

Центральный навигационный справочник по архитектуре, сервисам, регламентам и планам развития платформы **IntraLink**.

---

## 🚀 Активные архитектурные этапы

1. **[Переход на сценарный контур](plans/archive/scenario-orchestration/transition-roadmap.md):** перевод жизненного цикла заявок на конечный автомат `TicketRunOrchestrator`, сбор фактов (`FactBag`) с контролем источника правды (Provenance), компиляция решений `DecisionCompiler` и безопасная раскатка через режимы `shadow` / `canary` / `active`. Спецификация: [сценарный план](plans/archive/scenario-orchestration/execution-plan.md).
2. **[Асинхронная платформа пакетного триажа (ADR 0003)](adr/0003-asynchronous-batch-triage-platform.md):** перевод анализа очередей на неблокирующий шлюз `202 Accepted`, Redis Job Queue, Single-flight lock и стриминг прогресса через SSE (`/api/v1/events/stream`).
3. **[Модульная Worker Platform (5 инкрементов)](plans/archive/worker-platform/README.md):** безопасное исполнение действий, модульный SDK хэндлеров, маршрутизация fleet и пилот удаленной установки принтеров.
4. **[Desktop Companion](services/desktop-companion/README.md):** нативный Windows tray-helper на Tauri 2 для запуска DameWare, LiteManager и RDP из веб-интерфейса по одноразовым deep links Core API.
5. **[Roadmap качества RAG](plans/archive/rag-quality-roadmap.md):** гибридный поиск (pgvector BGE-M3 + FTS Russian tsvector) и Cross-Encoder `BAAI/bge-reranker-v2-m3` на FastEmbed (Recall@5 97.5%, Hit@5 100%).

---

## 🗺️ Структура директории `docs/`

```text
docs/
├── README.md                      ← Центральный навигатор и статусная матрица
├── architecture.md                ← SSOT архитектуры, схемы C4, шины, контуры безопасности
├── developer_guide.md             ← Инварианты, правила и настольная книга инженера/агента
├── brandbook.md                   ← Дизайн-система, токены интерфейса, Zero-Emoji Policy
│
├── adr/                           ← Архитектурные решения (Architecture Decision Records)
│   ├── 0001-transactional-command-platform.md
│   ├── 0002-modular-worker-platform.md
│   └── 0003-asynchronous-batch-triage-platform.md
│
├── architecture/                  ← Архитектурные спецификации v2
│   ├── v2-architecture-blueprint.md ← Генеральный план VSA и LiteLLM Gateway
│   ├── v2-automation-pipeline.md    ← Спецификация конвейера автопилота
│   ├── v2-contracts-and-schemas.md  ← Схемы БД, Taskiq DTO и REST API
│   └── domain-model-and-contracts.md← Доменные инварианты Helpdesk и Zero-Stub Policy
│
├── adr/                           ← Архитектурные решения (Architecture Decision Records)
│   ├── 0001-transactional-command-platform.md
│   ├── 0002-modular-worker-platform.md
│   ├── 0003-asynchronous-batch-triage-platform.md
│   └── 0004-vertical-slice-architecture-and-litellm.md
│
├── plans/                         ← Планы реализации и спринты
│   ├── v2-automation-implementation-plan.md ← 5-спринтовый план конвейера автопилота
│   ├── roadmap.md                 ← Стратегический продуктовый план развития 2026–2027
│   └── archive/                   ← Архив реализованных планов и чекпоинтов
│
├── runbooks/                      ← Инструкции по эксплуатации и верификации
│   ├── v2-automation-verification.md ← 7 эталонных приемочных сценариев автопилота
│   ├── identity-bootstrap.md      ← Инициализация сервисной учетной записи AD
│   └── triage-lifecycle.md        ← Регламент ANALYSIS_REVISION и кэширования решений
│
└── external/                      ← Документация сторонних систем
    └── intraservice_api/          ← Справочник REST API IntraService (49 спецификаций)
```

---

## 📊 Матрица актуальности документации

| Раздел | Документ | Статус | Назначение |
|---|---|:---:|---|
| **Архитектура v2** | [Blueprint v2](architecture/v2-architecture-blueprint.md) | 🟢 Актуален | VSA, LiteLLM Gateway, модули `core/`, `api/`, `worker/`, `web/` |
| **Автопилот** | [Automation Pipeline](architecture/v2-automation-pipeline.md) | 🟢 Актуален | Двухконтурная модель, Taskiq шина, матрица автономии, Anti-Loop |
| **Контракты** | [Contracts & Schemas](architecture/v2-contracts-and-schemas.md) | 🟢 Актуален | DTO, схемы БД (`SystemState`, `AutopilotPolicy`, `CommandRecord`) |
| **Домен** | [Domain Model](architecture/domain-model-and-contracts.md) | 🟢 Актуален | Инварианты Helpdesk, сервисы 05/09, Zero-Stub Policy |
| **ADR** | [ADR 0004](adr/0004-vertical-slice-architecture-and-litellm.md) | 🟢 Принят | Переход на Vertical Slice Architecture и LiteLLM |
| **Планы** | [Automation Plan](plans/v2-automation-implementation-plan.md) | 🟢 В работе | 5 спринтов интеграции Taskiq и автономного конвейера |
| **Верификация** | [Automation Verification](runbooks/v2-automation-verification.md) | 🟢 Актуален | Приемочные тест-кейсы (Circuit Breaker, Anti-Loop, Lock) |

---

## 🧭 Навигация по архитектуре v2

### 1. Архитектурные спецификации v2
* **[V2 Architecture Blueprint (`architecture/v2-architecture-blueprint.md`)](architecture/v2-architecture-blueprint.md)** — вертикальные срезы `api/src/features/*`, чистое ядро `core/`, фоновый воркер `worker/` и веб-клиент `web/`.
* **[Конвейер автопилота (`architecture/v2-automation-pipeline.md`)](architecture/v2-automation-pipeline.md)** — двухконтурная обработка заявок (Ко-пилот в UI vs Автопилот в Taskiq), шлюз релевантности, автономный диалоговый цикл.
* **[Схемы данных и контракты (`architecture/v2-contracts-and-schemas.md`)](architecture/v2-contracts-and-schemas.md)** — таблицы `system_state`, `autopilot_policies`, `command_records`, DTO очередей Taskiq.
* **[Доменная модель (`architecture/domain-model-and-contracts.md`)](architecture/domain-model-and-contracts.md)** — доменные инварианты Helpdesk, регламенты разделов 05 (Directum) и 09 (ЭЦП), Zero-Stub Policy.

### 2. Архитектурные решения (ADR)
* **[ADR 0004: Vertical Slice Architecture & LiteLLM](adr/0004-vertical-slice-architecture-and-litellm.md)** — ликвидация зоопарка зависимостей, переход на модульный монорепозиторий v2.
* **[ADR 0001: Transactional Command Platform](adr/0001-transactional-command-platform.md)** — транзакционный Outbox, идемпотентность и гарантированная доставка команд.

### 3. Регламенты эксплуатации (Runbooks)
* **[V2 Automation Verification Runbook](runbooks/v2-automation-verification.md)** — сценарии верификации конвейера автопилота и приемочные тесты.
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
