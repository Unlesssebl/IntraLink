# ADR 0005: Evidence-Based Routing Cascade (Доказательный каскад маршрутизации)

- **Статус:** Accepted / implementation in progress
- **Дата:** 2026-09-25
- **Авторы:** IntraLink Core Architecture Team
- **Уточнение:** ADR 0006 заменяет определение объекта маршрутизации и расширяет допустимую роль LLM. Модель доказательств, fail-closed арбитраж и immutable provenance из этого ADR сохраняются.
- **Связанные документы:**
  - `docs/architecture/adr/case-workflow-capability-architecture.md` (ADR 0006)
  - `docs/architecture/v2-architecture-blueprint.md` (ADR 0004)
  - `docs/architecture/domain-model-and-contracts.md`
  - `docs/architecture/v2-automation-pipeline.md`
  - `docs/architecture/v2-universal-scenario-core.md`

---

## 1. Контекст и проблематика (Context)

В текущей версии рантайма выбор сценария выполняется компонентом `ScenarioRouter` на основе аддитивной скоринговой модели (Factor A — service_id, Factor B — keywords, Factor C — regex, Factor D — custom fields, Factor E — cosine similarity прототипов).

Несмотря на работоспособность на базовых кейсах, аддитивный скоринг обладает фундаментальными архитектурными ограничениями:
1. **Скрытые компромиссы и размытие ответственности:** Аддитивная сумма ($Score = A + B + C + D + E$) может приводить к ложноположительным срабатываниям («галлюцинациям сходства»), когда высокий семантический скор перевешивает критические фактологические противоречия (например, несовпадение сервиса или явное указание на другую проблему).
2. **Смешение фаз жизненного цикла:** Текущий роутер частично пытается валидировать готовность параметров и доступность хоста в фазе маршрутизации, смешивая определение *намерения пользователя* (routing), *полноту данных* (readiness) и *выполнение сценария* (execution).
3. **Отсутствие строгой доказательной базы:** Объект `ScenarioMatch` содержит числовые веса и массив строковых причин, но не предоставляет строгого формализованного графа свидетельств (evidence vs contradictions) с указанием источников и силы влияния.
4. **Невозможность надёжного арбитража серой зоны:** В ситуациях с пограничной уверенностью аддитивный роутер либо слепо рискует, либо падает в общий fallback (`rag_consultation`), вместо контролируемой точечной верификации кандидатов.

---

## 2. Архитектурное решение (Decision)

Принято решение о поэтапном переходе на **доказательный каскад маршрутизации (Evidence-Based Routing Cascade)** со следующими ключевыми принципами:

### 2.1. Разделение фаз: Routing vs Readiness vs Execution
- **Routing (Маршрутизация):** Определение целевого сценария на основе наблюдаемых свидетельств (`TicketSnapshot`). Роутер отвечает на вопрос: *«О каком сценарии идёт речь?»*.
- **Readiness (Готовность):** Синтез параметров и проверка предусловий (`preconditions`). Отвечает на вопрос: *«Достаточно ли данных для запуска и доступна ли инфраструктура?»*. Если данных не хватает — перевод в `needs_clarification` (Статус 6 «Приостановлена»).
- **Execution (Исполнение):** Выполнение идемпотентных команд сценария. Отвечает на вопрос: *«Как безопасно применить изменения?»*.

### 2.2. Доказательная база (Evidence Model)
Каждый кандидат сопровождается атомарными фактами (`RoutingEvidence`):
- **Источник (`EvidenceSource`):** `service_id`, `service_name`, `title`, `description`, `comment`, `custom_field`, `semantic`.
- **Полярность (`EvidencePolarity`):** `supports` (подтверждает) vs `contradicts` (опровергает).
- **Сила (`EvidenceStrength`):** `exact`, `strong`, `weak`.

### 2.3. LLM только как Verifier серой зоны
LLM не выбирает сценарий «с чистого листа» из свободного текста. Генеративная модель привлекается **исключительно как верификатор (`CandidateVerification`) в серой зоне** (когда детерминированные провайдеры дают конкуренцию кандидатов или пограничный скор):
- Проверяет текстовые спаны на предмет противоречий.
- Формирует строгий вердикт: `supported`, `contradicted`, `insufficient`.
- Если LLM недоступен — каскад безопасно деградирует в `degraded` с явной причиной, не совершая разрушительных действий.

### 2.4. `RoutingDecision` как единый Immutable Source of Truth
Каждое решение каскада фиксируется в неизменяемом объекте `RoutingDecision`:
- Хранит детерминированный `snapshot_hash` (SHA-256 нормализованного канонического снимка тикета).
- Фиксирует состояние (`selected`, `needs_clarification`, `ambiguous`, `unmatched`, `degraded`).
- Содержит полный список кандидатов, привязанных свидетельств, вердиктов верификатора и отсутствующих фактов.
- Является единственным контрактом, передаваемым из анализа в `AgentPlan` и далее в журнал операторского подтверждения (`routing_feedback`).

---

## 3. Статус реализации этапов (Implementation Status)

### 3.1. Этап 1: Доменные контракты и персистентность (Completed)
1. Пакет доменных контрактов `core/routing`:
   - `core/routing/contracts.py`: строгие Pydantic v2 модели и строковые перечисления с проверкой инвариантов на уровне валидаторов (`extra="forbid"`, `frozen=True`).
   - Усиленные инварианты `RoutingDecision`: уникальность `RoutingEvidence.id` и `ScenarioCandidate.scenario_key`, валидация ссылок `evidence_ids` и `contradiction_ids`, соответствие `candidate.sources` источникам supporting evidence.
   - `core/routing/snapshot.py`: безопасная фабрика `TicketSnapshotFactory` с маскированием структурированных паролей/секретов и вычислением канонического SHA-256 хеша.
   - `core/routing/__init__.py`: экспорт публичных контрактов без сторонних зависимостей.
2. Модели базы данных в `core/database/models.py`:
   - `RoutingDecisionRecord` (таблица `routing_decisions`).
   - `RoutingFeedbackRecord` (таблица `routing_feedback`).
3. Alembic-миграция `0004_add_routing_cascade_tables` с проверенным циклом `upgrade -> downgrade -> upgrade`.

### 3.2. Этап 2: Routing-профили сценариев, CandidateProvider и CandidateGenerator (Completed)
1. Канонические routing-профили:
   - `core/routing/profiles.py`: неизменяемая модель `ScenarioRoutingProfile` и канонические профили для 9 сценариев default `ScenarioRegistry` (включая `consultation_only=True` для `rag_consultation`).
   - `core/routing/profile_registry.py`: `RoutingProfileRegistry` с индексацией по `scenario_key` и `exact_service_ids`, валидацией уникальности ключей.
2. Детерминированные идентификаторы свидетельств:
   - `core/routing/evidence_id.py`: детерминированный SHA-256 генератор `compute_evidence_id` от канонического набора параметров сигнала.
3. Пакет независимых провайдеров свидетельств `core/routing/providers/`:
   - `base.py`: контракт `CandidateProvider`.
   - `catalog.py`: `CatalogCandidateProvider` (точный `service_id` и нормализованный `service_name`).
   - `lexical.py`: `LexicalCandidateProvider` (проверка `title`, `description` и публичных комментариев с контролем границ слов и точным исходным `text_span`).
   - `semantic.py`: `SemanticCandidateProvider` (векторный retrieval прототипов с ограничением `top_k=3` и техническим фильтром полноты `retrieval_floor=0.45`). Семантическое сходство используется **исключительно для retrieval** и не превращается в вероятности или уверенность.
4. Оркестратор кандидатов `core/routing/candidate_generator.py`:
   - Параллельный запуск провайдеров через `asyncio.gather`.
   - Дедупликация идентичных свидетельств и обнаружение конфликтов (`provider_invalid_result`).
   - Изоляция сбоев: падение отдельного провайдера фиксируется в `degraded_providers` коротким машинным кодом (`provider_timeout`, `provider_unavailable`, `provider_invalid_result`, `provider_error`) без утечки промптов, секретов или текстов тикетов.
   - Детерминированная сортировка кандидатов и свидетельств.
   - Отсутствие выбора победителя, вызова readiness и искусственного RAG fallback.
5. Регрессия #142135:
   - Подтверждено сохранение кандидата `grant_wlan` по `service_name` при отсутствии ServiceId и пустых сущностях без досрочного выбора победителя.

### 3.3. Этап 3: Безопасный LLM-verifier серой зоны с DLP/privacy routing (Completed)
1. **Назначение и границы ответственности:**
   - Верификатор **НЕ является классификатором** (не классифицирует заявку «с нуля» и не выбирает итоговый сценарий).
   - Не создаёт новых кандидатов, не вычисляет readiness, не разрешает исполнение и не возвращает статус заявки.
   - `DecisionPolicy` на данном этапе **отсутствует** (арбитраж и выбор победителя вынесены в этап 4).
   - Верификатор получает строго 1..3 кандидата от `CandidateGenerator` и проверяет наличие подтверждающих или опровергающих свидетельств в санитизированном тексте.
2. **Privacy Routing Matrix и DLP-санитизация:**
   - **RED Zone:**
     - Условие: обнаружен пароль/токен либо среди кандидатов присутствуют сценарии `account_create`, `account_lock`, `grant_wlan`.
     - Маршрутизация: **строго и только `helpdesk-local`**.
     - Инвариант: **Fallback в Cloud для RED-контура СТРОГО ЗАПРЕЩЁН**. При недоступности локальной модели возвращается `degraded/verifier_unavailable`.
   - **YELLOW Zone:**
     - Условие: обнаружены персональные (`[USER]`, `[EMAIL]`, `[PHONE]`) или внутренние инфраструктурные данные (`[INTERNAL_IP]`, `[HOST]`).
     - Маршрутизация: после детерминированной санитизации допустимо использование облачного шлюза (`helpdesk-fast`).
   - **GREEN Zone:**
     - Условие: чувствительные признаки отсутствуют. Использование `helpdesk-fast`.
   - **DLP-маскирование:** Любой свободный текст проходит маскирование: `[PASSWORD]`, `[TOKEN]`, `[EMAIL]`, `[PHONE]`, `[INTERNAL_IP]`, `[HOST]`, `[USER]` (включая известные сущности из `snapshot.entities`).
   - Исходные `custom_fields`, `entities`, `attachments` и пароли **никогда не передаются в LLM payload**.
3. **Версионирование промпта и санитизатора:**
   - `PROMPT_VERSION = "candidate-verifier-v1"`
   - `SANITIZER_VERSION = "dlp-verifier-v1"`
   - `sanitized_input_hash`: детерминированный SHA-256 хеш санитизированного канонического JSON.
4. **Машинные коды деградации (`ALLOWED_DEGRADATION_CODES`):**
   - `verifier_timeout`: превышение явного таймаута транспортного вызова;
   - `verifier_unavailable`: шлюз/локальная модель недоступны или вернули ошибку соединения;
   - `verifier_invalid_json`: ответ модели не является валидным JSON (даже после repair-попытки);
   - `verifier_invalid_schema`: ответ модели нарушает Pydantic-схему (наличие неизвестных полей, confidence, status, etc.);
   - `verifier_candidate_mismatch`: модель вернула неизвестные, пропущенные или дублирующие `scenario_key`;
   - `verifier_invalid_evidence`: галлюцинация `evidence_span` (нет в санитизированном тексте), невалидный `referenced_evidence_id` или `supported` без доказательств;
   - `verifier_policy_blocked`: нарушение входных инвариантов (кол-во кандидатов outside [1..3], неизвестные сценарии, невалидные ссылки evidence).
   - **Строгий инвариант:** `degradation_reason` содержит **исключительно машинный код** и никогда не содержит exception text, stacktrace или текст промпта.
5. **Механизм исправления формата (Single Repair Attempt):**
   - Разрешена ровно одна попытка исправления только для `invalid_json` и `schema_validation_error`.
   - Repair payload содержит только машинный код ошибки и исходный санитизированный JSON, без утечки трейсов Python.
6. **Регрессия #142135:**
   - Подтверждено: заявка #142135 (`grant_wlan` по `service_name`) направляется в RED-контур на `helpdesk-local`, спан валидируется, итоговое решение не принимается до этапа 4.

### 3.4. Этап 4: DecisionPolicy, Fact Readiness и персистентность решений

1. **Изолированный доменный каскад (`RoutingCascade`):**
   - Зафиксирована версия: `ROUTER_VERSION = "evidence-router-v1"`.
   - Полный конвейер: `TicketSnapshot` → `CandidateGenerator` → `GreyZonePolicy` → `GreyZoneVerifier` (при необходимости) → `DecisionPolicy` → `FactReadinessPolicy` → `RoutingDecision`.
   - Полный запрет вероятностей, confidence scores и аддитивных эвристик: решение принимается строго на основе классов доказательств (`exact`, `strong`, `semantic`, `weak`) и непротиворечивости.

2. **Матрица решений `GreyZonePolicy` и арбитраж:**
   - **Прямой выбор (без LLM-верификатора):**
     - Единственный доминирующий кандидат с доказательством `service_id` (`exact`) без противоречий, при условии что остальные кандидаты имеют только `semantic`/`weak` свидетельства.
     - Единственный доминирующий кандидат, подтвержденный $\ge 2$ различными несемантическими источниками (например, `service_name` + `lexical`) без противоречий, а остальные — только `semantic`/`weak`.
     - `consultation_only` (RAG) **никогда** не выбирается напрямую.
     - Кандидаты с `exact` или `strong` противоречиями безусловно исключаются. Кандидат с `weak` противоречием переводится в серую зону.
   - **Переход в серую зону (Grey Zone):**
     - 1..3 допустимых кандидата направляются в `GreyZoneVerifier`.
     - $> 3$ неразрешимых кандидатов: прямой вердикт `ambiguous` с кодом `candidate_set_too_broad` без обращения к LLM.
     - 0 кандидатов при здоровых провайдерах: `unmatched` с кодом `no_candidates_found`.
     - Отсутствие кандидатов при деградации любого из провайдеров: `degraded` с кодом `candidate_generation_degraded`.

3. **Арбитраж вердиктов верификатора (`DecisionPolicy`):**
   | Ситуация после верификатора | Итоговое состояние | Reason Code |
   |---|---|---|
   | Ровно 1 кандидат `supported` | Переход в Fact Readiness | `single_candidate_supported` |
   | $> 1$ кандидатов `supported` | `ambiguous` | `multiple_supported_candidates` |
   | 0 `supported`, но есть $\ge 1$ `insufficient` | `ambiguous` | `insufficient_evidence` |
   | Все кандидаты `contradicted` | `unmatched` | `all_candidates_contradicted` |
   | Верификатор деградировал (`is_degraded=True`) | `degraded` | Машинный код верификатора (`degradation_reason`) |

   *Инвариант:* Верификатор не имеет права генерировать новые кандидаты или переопределять доказательства.

4. **Разделение Fact Readiness и Live Preflight:**
   - **Fact Readiness (Чистая проверка полноты данных):** Выполняется `FactReadinessPolicy` в памяти без сетевых вызовов, LDAP, WinRM, socket probe или RAG I/O.
     - `install_printer`: обязателен `pc_name`; для явного USB (`printer_connection_type == "usb"`) сетевой адрес не требуется, иначе обязателен `printer_address`. Если тип не указан и адреса нет — возвращаются оба missing facts: `printer_address`, `printer_connection_type`.
     - `printer_spooler_restart`, `offline_host`: обязателен `pc_name`.
     - `default_printer_fix`: обязателен `pc_name` и хотя бы один из `printer_address`/`printer_model`.
     - `grant_wlan`, `account_lock`: альтернатива `target_user` / `user_name`, наружу возвращается строго канонический missing fact `target_user`.
     - `account_create`: `first_name` + `last_name` либо разбор `user_name` $\ge 2$ частей, а также `department` и `title`.
     - `service_redirect`, `rag_consultation`: обязательных фактов нет.
     - Заглушки (`-`, `—`, `нет`, `не указано`, `n/a`, `none`, `null`) и whitespace считаются пустыми.
     - При нехватке фактов формируется состояние `needs_clarification` с отсортированным списком `missing_facts`.
   - **Live Preflight (Сетевая диагностика окружения):** Живые проверки доступности портов (ping, TCP 445/5985), реверс-DNS, Active Directory LDAP existence check и RAG retrieval вынесены строго в read-only preflight Этапа 5 перед выполнением сценария.

5. **Персистентность и неизменяемость (`RoutingDecisionRepository`):**
   - Решения неизменяемы (append-only в таблицу `routing_decisions`).
   - **Гарантия изоляции каскада:** `RoutingCascade` не изменяет заявку, PostgreSQL, Redis или инфраструктуру исполнения; внешние embedding/LLM-вызовы допустимы и отражаются как контролируемая деградация.
   - `RoutingDecisionService` остаётся единственной границей записи решения:
     - При внешней сессии выполняет `flush()`, не выполняя `commit()`, позволяя объединить сохранение в транзакцию бизнес-логики.
     - При внутренней сессии управляет транзакцией целиком.
     - Ошибки персистентности логируются без SQL и чувствительных данных и поднимают контролируемый `RoutingPersistenceError("routing_persistence_failed")` через exception chaining.
   - Запрещено вызывать запись решений из GET-эндпоинтов (чтение тикета не запускает AI и не создает сайд-эффектов).

### 3.5. ⚠️ Текущие ограничения и статус рантайма
- **Действующий runtime НЕ затронут:** существующий `ScenarioRouter`, worker-инфраструктура и `autopilot_corrections` продолжают функционировать без изменений.
- **Новый каскад НЕ подключен к production пайплайну:** вызовы изолированы в доменном слое `core/routing`.
- **Исполнение и изменение статусов запрещены:** каскад формирует `RoutingDecision`, но не меняет статус тикета и не выполняет сценарии (задача Этапа 5).

---

## 4. Дорожная карта последующих этапов (Roadmap)

- [x] **Этап 1:** Контракты и уровень персистентности (`contracts`, `snapshot`, DB, Alembic).
- [x] **Этап 2:** Профили, детерминированные провайдеры и генератор кандидатов (`profiles`, `providers`, `candidate_generator`).
- [x] **Этап 3:** Безопасный LLM-верификатор серой зоны с Free-Text DLP и строгими структурированными ответами (`verifier`).
- [x] **Этап 4:** Реализация `GreyZonePolicy`, `DecisionPolicy`, `FactReadinessPolicy`, персистентности решений (`RoutingCascade`, `readiness`, `persistence`).
- [ ] **Этап 5:** Интеграция с `AgentPlan` и переключение рантайма с `ScenarioRouter` на `RoutingCascade` (включая read-only preflight).
- [ ] **Этап 6:** Миграция операторского UI на `routing_feedback` и удаление устаревшего `autopilot_corrections`.

