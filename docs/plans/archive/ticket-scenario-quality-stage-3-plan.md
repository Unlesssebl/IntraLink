# Технический план этапа 3: предметные сценарии, диагностические планы и достоверные ответы

Дата: 2026-09-10. Статус: готов к реализации после архитектурной ревизии.

Связанные материалы: [roadmap](ticket-scenario-quality-roadmap.md), [план этапа 2](ticket-scenario-quality-stage-2-plan.md), [сравнение replay](../../core-api/tests/fixtures/ticket_quality/replay/comparison.md), [готовность сценарного ядра](archive/scenario-core-readiness.md).

---

## 1. Результат и границы выпуска

### 1.1. Главная цель

Обеспечить предметную глубину и смысловую точность обработки заявок 1-й линии Helpdesk без расширения привилегий и без ложного впечатления, что запланированная проверка уже выполнена.

Этап должен:

1. Формировать структурированный `ExecutionPlan` с последовательностью сбора сведений, проверок, ручных действий и подтверждения результата.
2. Показывать оператору серверную проекцию прогресса, построенную только из фактов, команд и событий с provenance.
3. Формировать публичный ответ заявителю, соответствующий текущей фазе обслуживания: запланировано, запущено, выполняется или подтверждено.
4. Отделять публичный ответ (`response.text`) от внутренней технической сводки (`internal_summary`) и исключать их случайное смешивание.
5. Блокировать недостоверный текст и статус 29 не только при компиляции решения, но и на границе создания фактической команды `apply_triage`.
6. Расширить точные структурированные уточнения на недостающие параметры предметных областей.

### 1.2. Предметные области и сценарии

Шесть предметных областей покрывают девять canonical scenario keys:

1. Печать и МФУ: `install_printer`, `printer_hardware_service`, `printer_print_failure`.
2. Сканирование: `printer_scan_failure`.
3. Производительность ПК: `pc_performance`.
4. Сетевые папки и блокировки файлов: `file_lock`.
5. Периферия: `peripheral_setup`, `peripheral_diagnostics`.
6. Переустановка и восстановление ОС: `os_reinstallation`.

В рамках этапа не вводятся параллельные ключи `scan_issue` и `printer_defect`. Существующие ключи эволюционируют версиями сценария. Старые версии остаются адресуемыми для уже закреплённых `TicketRun`; новые заявки маршрутизируются на актуальные версии.

### 1.3. In scope

- Совместимое расширение моделей `PlanStep`, `ExecutionPlan` и `DecisionEnvelope`.
- Детерминированный `PlanBuilder` и серверная проекция прогресса.
- Версионированные предметные response policies и шаблоны.
- `TruthfulnessGuard` для результата компиляции и фактического command payload.
- Read-only отображение плана в Инспекторе на первой итерации.
- Раздельные черновики публичного и внутреннего комментария.
- Версионированная миграция шаблонов и `resolution_policies`.
- Replay, контрактные, интеграционные и миграционные проверки.
- Shadow/HITL rollout; автономная отправка и новые изменяющие действия не включаются.

### 1.4. Out of scope

- Неконтролируемая генерация ответа внешней LLM без схемы и guard.
- Новые привилегированные инфраструктурные действия.
- Автоматическое выполнение SMART, toner, WIA/TWAIN, управления автозагрузкой или замены расходных материалов, если для них нет зарегистрированного read-only capability/worker contract.
- Полностью автономный фоновый автопилот.
- Редактирование статусов шагов непосредственно в браузере без серверного события и evidence.

---

## 2. Архитектурные инварианты

### 2.1. План не равен прогрессу

`ExecutionPlan` — описание ожидаемого процесса для конкретных `scenario_version` и `fact_revision`. Оно входит в неизменяемый снимок `DecisionEnvelope`.

Текущий статус шага — серверная проекция из доверенных источников:

- `FactObservation` и выбранного `ResolvedFact` для `collect`/`clarify`;
- `CommandRecord` и `CommandEvent` для автоматической проверки или действия;
- `TicketRunEvent` либо нового append-only события ручного подтверждения для `manual`/`verify`;
- свежей диагностики, совпадающей с текущей целью, для уже выполненных read-only проверок.

UI не является источником истины. На этапе 3A чеклист отображается read-only. Если в этапе 3B добавляется ручная отметка, она создаёт серверное событие с `actor`, `observed_at`, `expected_decision_version`, `step_id`, `result`, `evidence_refs` и причиной пропуска; клиент не изменяет envelope напрямую.

### 2.2. Единственный канонический контракт

В JSON и Python-модели хранится только `DecisionEnvelope.execution_plan`. Термин «диагностический план» используется как UI-название. `diagnostic_plan` допускается только как временный read-only compatibility accessor и не сериализуется отдельным полем.

### 2.3. Backward compatibility

Существующие значения `PlanStep.kind` нельзя удалить, пока исторические решения и закреплённые запуски могут их содержать. Этап использует аддитивную модель:

```python
class StepKind(str, Enum):
    # существующие orchestration kinds
    COLLECT = "collect"
    CLARIFY = "clarify"
    DECIDE = "decide"
    APPROVE = "approve"
    DISPATCH = "dispatch"
    WAIT = "wait"
    FINALIZE = "finalize"
    MANUAL_REVIEW = "manual_review"

    # новые diagnostic kinds
    CHECK = "check"
    ACTION = "action"
    MANUAL = "manual"
    VERIFY = "verify"


class StepStatus(str, Enum):
    NOT_STARTED = "not_started"
    WAITING_INPUT = "waiting_input"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"


class PlanStep(StrictModel):
    schema_version: Literal[2] = 2
    id: str
    title: str
    kind: StepKind
    status: StepStatus = StepStatus.NOT_STARTED
    required_for_resolution: bool = False
    capability_id: str | None = None
    executor: Literal["backend", "windows", "engineer"] | None = None
    target_ref: str | None = None
    result_summary: str | None = None
    requires: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    status_source: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    completed_by: str | None = None
    skip_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(StrictModel):
    schema_version: Literal[2] = 2
    scenario_key: str
    scenario_version: int = Field(ge=1)
    fact_revision: int = Field(default=0, ge=0)
    title: str = ""
    phase: Literal[
        "collecting", "ready", "dispatched", "running", "verifying", "completed", "blocked"
    ] = "collecting"
    next_action_description: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
```

`DecisionEnvelope` принимает `execution_plan: ExecutionPlan | LegacyExecutionPlan | None = None` и `internal_summary: str | None = None`. Исторические envelope версии 1 должны продолжать валидироваться и отображаться. Все прямые места создания `DecisionEnvelope`, включая verified-success finalization в `TicketRunOrchestrator`, переводятся на общий factory/compiler path и покрываются тестами.

### 2.4. Инвариант подтверждённого шага

Шаг может иметь статус `completed`, только если:

- его `evidence_refs` не пуст;
- evidence относится к той же цели (`target_ref`) и текущему run;
- автоматический шаг ссылается на успешный terminal `CommandRecord`/диагностическое событие;
- ручной шаг содержит actor и timestamp;
- diagnostic evidence не истёк по TTL и его `collected_at` известен.

`skipped` допустим только для условного шага и требует `skip_reason`. `unsupported` означает отсутствие capability и не должен отображаться как выполняемый автоматически.

---

## 3. Сценарная миграция и динамика диалога

### 3.1. Версионирование существующих сценариев

- `printer_scan_failure` получает новую версию с ветвлением network/USB.
- `printer_hardware_service` получает новую версию для замятия, картриджа и дефектов печати, требующих физического обслуживания.
- `printer_print_failure` сохраняется для очереди печати/Spooler и не смешивается с аппаратным дефектом.
- `install_printer`, `pc_performance`, `file_lock`, `peripheral_setup`, `peripheral_diagnostics`, `os_reinstallation` получают новые версии только при изменении требований или matcher semantics.
- Старые версии остаются зарегистрированными, но их matchers отключаются для новых маршрутизаций по образцу legacy `install_printer` v1.

Обновляются `SCENARIO_DISPLAY_NAMES`, `SCENARIO_SHORT_NAMES`, API/TypeScript unions, настройки rollout и тесты покрытия scenario mapping. Новые aliases добавляются только при наличии реального внешнего legacy key.

### 3.2. Учет комментариев без скрытого re-pin

`_text(context)` получает нормализованный основной текст и ограниченный набор последних содержательных комментариев автора. Комментарии инженеров, системные сообщения, цитаты предыдущего ответа и служебные маркеры не становятся самостоятельным основанием для смены сценария.

Для нового, ещё не закреплённого run комментарии участвуют в обычной маршрутизации. Для существующего pinned run смена выполняется только через явный протокол:

1. Router формирует `scenario_transition_proposed` с прежним и новым ключом, основаниями и оценками.
2. Если нет запущенной/неопределённой изменяющей команды, оператор может подтвердить переход.
3. Старый run получает terminal/superseded event; новый run наследует только совместимые append-only факты.
4. При активной или неизвестно завершившейся команде переход блокируется до reconcile.

Молчаливое изменение `scenario_key` у существующего run запрещено.

---

## 4. Матрица предметных планов

| Сценарий | Условия и обязательные факты | План | Безопасный публичный ответ до запуска |
|---|---|---|---|
| `install_printer` | `pc_name`, `printer_targets`; для network — адрес каждого устройства, для USB — модель и тип подключения | Проверить связность параметров → preflight → approval → install → verify test page | «Данные для установки принтера подготовлены. После подтверждения инженером будет выполнена установка на ПК {pc_name}.» |
| `printer_hardware_service` | устройство/адрес или место установки, `defect_type`; для выезда — location/contact | Определить аппаратный дефект → исключить удалённое решение → назначить ручной осмотр → verify | «Заявка на обслуживание МФУ зарегистрирована. Инженер уточняет условия и необходимость выезда.» |
| `printer_print_failure` | `pc_name`, принтер/очередь | Доступность → Spooler/очередь → ручное устранение при необходимости → пробная печать | «Подготовлен план проверки очереди печати и доступности принтера. Результат сообщим после диагностики.» |
| `printer_scan_failure` | обязательный `connection_type`; network: `printer_address`, `scan_path`; USB: `pc_name`, модель/тип устройства | Network: доступность МФУ и SMB:445 → учётная запись → тест. USB: WIA/TWAIN/драйвер как ручной или unsupported capability → тест | «Уточняем тип подключения сканера и параметры назначения, после чего выполним подходящую проверку.» |
| `pc_performance` | `pc_name` | Доступность → свежие доступные telemetry disk/specs/services → ручная проверка нагрузки/автозагрузки → stability verify | «Подготовлена дистанционная диагностика ПК {pc_name}. Проверка начнётся после подтверждения инженером.» |
| `file_lock` | `file_path`, сервер/шара при наличии | Проверить путь и владельца блокировки → определить сессию → согласовать снятие → verify повторное открытие | «Проверяем, каким процессом или сеансом занят файл. Перед снятием блокировки инженер подтвердит безопасный способ.» |
| `peripheral_diagnostics` | `device_type`, `pc_name`, симптом | Физическое подключение → Device Manager/служба → драйвер → функциональный тест | «Уточняем подключение и симптом устройства, затем инженер проверит настройки на ПК {pc_name}.» |
| `peripheral_setup` | `device_type`, `pc_name`, interface type | Совместимость → подключение/драйвер → default device → инструктаж/verify | «Данные для подключения устройства собраны. Установка будет выполнена после проверки совместимости.» |
| `os_reinstallation` | `pc_name`, `backup_confirmed`, downtime window | Backup evidence → состояние диска, если capability доступен → окно простоя → ручная установка → восстановление → verify | «Перед переустановкой необходимо подтвердить резервную копию и согласовать время недоступности ПК.» |

`PlanBuilder` не обещает запуск отсутствующего capability. Текущая `diagnose_host`/host telemetry может подтверждать только реально возвращаемые поля. SMART, toner, WIA/TWAIN, live CPU/RAM load и иные отсутствующие проверки получают `manual` или `unsupported`, пока для них не появится отдельный согласованный read-only contract.

---

## 5. Truthfulness & Integrity Guard

### 5.1. Структурная проверка

Структурная проверка первична и не зависит от формулировки текста.

Статус 29 разрешён только когда все шаги с `required_for_resolution=true` находятся в `completed` либо допустимом `skipped`, а подтверждения удовлетворяют разделу 2.4. `not_started`, `waiting_input`, `ready`, `running`, `failed`, `unsupported` блокируют закрытие.

Целевой статус берётся из `policy.target_status_id`; строковое состояние `DecisionEnvelope.status` не подменяет статус IntraService.

При нарушении:

- `gates.can_send_response = False` для попытки отправить запрещённую резолюцию;
- `gates.can_execute_action = False`, если нарушение затрагивает изменяющее действие;
- добавляется стабильный reason code, например `unverified_resolution_blocked:step:<step_id>`;
- исходный запрещённый статус не «исправляется» молча: compiler формирует отдельный безопасный fallback outcome со статусом 27 либо требует нового решения;
- факт блокировки записывается в decision steps/audit.

### 5.2. Фазовые шаблоны

Для каждой предметной policy определяются тексты как минимум для фаз:

- `planned`: «подготовлен план», «после подтверждения будет выполнено»;
- `dispatched`/`running`: «проверка запущена» — только при наличии соответствующего события;
- `waiting_input`: точный вопрос о недостающем факте;
- `verified`: утверждение о результате только по terminal evidence;
- `failed`/`needs_review`: нейтральный текст о невозможности подтвердить результат.

Фразы «провожу», «приступаю», «специалист направлен», «проверено», «установлено», «ошибок нет» выбираются только в фазе, которая это подтверждает.

### 5.3. Лексический guard

Лексический анализ остаётся defense-in-depth. Он классифицирует как минимум:

- завершение действия;
- начало/текущее выполнение;
- назначение или выезд специалиста;
- отсутствие ошибок/подтверждение исправления;
- цитаты и инструкции пользователю, которые не являются утверждением сервиса.

Guard получает `phase`, `evidence_refs`, `target_status_id` и проверяемый текст. Простого списка regex без контекста недостаточно.

### 5.4. Enforcement на фактическом payload

Проверка выполняется дважды:

1. В `DecisionCompiler` перед формированием `DecisionGates` — для качества сохранённого envelope.
2. В общем `CommandService`/policy validator перед созданием любой команды `apply_triage` — для фактических `status_id`, `comment`, `is_private`, `decision_id` и `decision_version`.

API router, UI и `TicketRunOrchestrator` используют один валидатор. Обход через прямой вызов `CommandService.create_record()` запрещён. Параметры команды связываются с решением canonical hash либо повторно валидируются после операторского редактирования.

Оператор может изменить безопасный текст без отдельного разрешения, если повторная проверка успешна. Попытка закрытия вопреки плану требует отдельного privileged override с причиной и audit event; обычный HITL-клик не является таким override.

---

## 6. Публичный и внутренний каналы

- `response.text` содержит только публичный текст.
- `internal_summary` содержит техническую сводку без секретов, учётных данных и необработанных worker payloads.
- Публичный и внутренний черновики хранятся раздельно: `ticket + decision_version + mode`.
- Переключение `reply/internal` не перезаписывает пользовательскую правку другого режима.
- `internal_summary` никогда автоматически не подставляется в публичный режим.
- Отправка только внутреннего комментария не наследует целевой статус публичной resolution policy и не может закрыть заявку. Для status 29 требуется отдельная публичная apply-команда либо явно определённый двухкомандный workflow.
- Сервер валидирует соответствие `is_private` выбранному каналу и сохраняет канал в command/audit record.
- Пути к логам и технические идентификаторы проходят sanitizer; секреты не сохраняются в envelope.

На этапе 3A `DiagnosticPlanView` является read-only. Кнопки подтверждения ручных шагов появляются только после реализации событий из раздела 2.1.

---

## 7. Последовательность реализации

### 3A. Контракты и read-only планы

Компоненты: `shared/domain/scenario.py`, `shared/domain/__init__.py`, `intra-web/src/lib/types.ts`, `plan_builder.py`.

1. Добавить совместимый parser для plan v1/v2 и единственное поле `execution_plan`.
2. Обновить все места создания `DecisionEnvelope`; удалить прямое использование устаревших constructor aliases.
3. Реализовать детерминированный `PlanBuilder` для всех scenario keys из раздела 1.2.
4. Вычислять статусы только из свежих facts/diagnostics/events; проверять target binding и TTL.
5. Добавить read-only `DiagnosticPlanView` и отдельные public/internal drafts.

### 3B. Сценарии, политики и evidence

Компоненты: `scenarios/builtin.py`, `scenarios/registry.py`, `resolution_service.py`, миграция Alembic, fixtures replay.

1. Выпустить новые версии существующих сценариев без конкурирующих canonical keys.
2. Добавить conditional requirements для network/USB, hardware/queue и других веток.
3. Реализовать безопасную обработку комментариев и протокол `scenario_transition_proposed`.
4. Добавить versioned response templates и `resolution_policies` через идемпотентную Alembic-миграцию/upsert. Изменение только `templates.json` не считается поставкой в непустую БД.
5. Обновить `templates_seed` для чистых установок, policy snapshot и replay manifest.
6. При необходимости ручного изменения шага добавить append-only endpoint/event с optimistic concurrency и audit.

### 3C. Enforcement и интеграция

Компоненты: `truthfulness_guard.py`, `decision_compiler.py`, `command_service.py`, `commands_v2.py`, `scenario_orchestrator.py`, `UnifiedActionDock.tsx`.

1. Реализовать структурный и лексический guard.
2. Централизовать command payload validation в сервисном слое.
3. Связать plan progress с `CommandRecord`, `CommandEvent`, `TicketRunEvent` и manual evidence.
4. Запретить status 29 при любом незавершённом обязательном шаге.
5. Проверить сохранение каналов и отсутствие утечки `internal_summary`.
6. Повысить `ANALYSIS_REVISION`, сделать прежние решения stale и потребовать явный reanalyze.

---

## 8. Тестирование и критерии приёмки

### 8.1. Контрактные тесты

- Старый envelope с `ExecutionPlan` v1 парсится и отображается.
- Новый envelope сериализует только `execution_plan`, без дублирования `diagnostic_plan`.
- Каждый `completed` step имеет допустимое evidence и target binding.
- `skipped` без причины и ручной `completed` без actor/timestamp отклоняются.
- Прямой verified-success путь оркестратора проходит через актуальный контракт.

### 8.2. Truthfulness и command boundary

- «Принтер успешно установлен» при незавершённой установке блокируется.
- «Убедитесь, что кабель установлен» не считается утверждением сервиса.
- «Приступаю к проверке» без dispatched/running evidence блокируется.
- «Специалист направлен» без события назначения блокируется.
- `running`, `failed`, `unsupported` или незавершённый `verify` блокируют status 29.
- Изменение оператором разрешённого status 27 на 29 повторно проверяется и отклоняется на сервере.
- Изменённый оператором безопасный текст допускается и фиксируется как edit.
- Вызов через router, orchestrator и прямой service path применяет один enforcement.

### 8.3. Сценарии и прогресс

- Все scenario keys из раздела 1.2 имеют отдельные unit cases.
- USB/network scan получают разные conditional requirements и планы.
- Аппаратный дефект, очередь печати и установка принтера не конкурируют как одинаковые сценарии.
- Старый pinned run продолжает использовать свою версию.
- Комментарий с новым запросом создаёт transition proposal, но не меняет active run молча.
- При активной или `needs_review` команде reclassification блокируется до reconcile.
- Диагностика другой цели либо с истёкшим TTL не завершает шаг.
- Отсутствующий capability отображается как `manual`/`unsupported`, а не `running`.

### 8.4. UI

- Публичный и внутренний черновики независимы.
- Переключение режима не уничтожает пользовательскую правку.
- `internal_summary` не отправляется публично ни при смене режима, ни после reanalyze.
- Внутренний комментарий не закрывает заявку и не применяет целевой статус публичной policy.
- Исторический plan v1 и новый plan v2 корректно отображаются.
- Read-only шаг нельзя отметить выполненным из DOM без серверного события.

### 8.5. Replay и миграция

- Replay содержит все 99 исходных заявок, 0 необъяснённых исключений и детерминированный повторный результат.
- Исходный baseline не перезаписывается; candidate, manifest и comparison создаются отдельно.
- Сохраняются 4/4 подтверждённых регрессии. Это формулируется как `4/4 confirmed`, а не как доказанная общая точность.
- Все изменения в 95 `needs_review` перечислены без автоматического объявления правильными.
- Добавляется отдельная размеченная выборка положительных, отрицательных и конфликтных случаев для каждого нового/изменённого сценария.
- Alembic `upgrade -> downgrade -> upgrade` проходит на пустой и заполненной копии PostgreSQL; новые активные policy/template версии присутствуют после миграции.

### 8.6. Definition of Done для кода

1. Все перечисленные сценарии формируют совместимый динамический план.
2. Закрытие и claims защищены на compiler и command boundary.
3. Прогресс строится из доверенных событий; UI не является source of truth.
4. Политики поставляются миграцией и входят в детерминированный replay snapshot.
5. Core API tests, frontend typecheck/lint/tests/build и migration tests завершаются с exit code 0.
6. `ANALYSIS_REVISION` повышен, старые решения признаны stale.

---

## 9. Безопасный rollout

1. **Локальная проверка:** unit/contract/integration tests, frontend build, replay и миграция на изолированной PostgreSQL.
2. **Совместимость:** чтение исторических envelope, продолжение pinned runs, проверка прямых finalization paths.
3. **Развёртывание контрактов:** backup PostgreSQL, миграция на заполненной копии, затем применение к целевой среде.
4. **Shadow:** новые планы и guard вычисляются и журналируются, но не меняют заявку автоматически. Сравниваются scenario, plan phase, target status, текстовые violations и command eligibility.
5. **HITL canary:** ограниченная доля заявок новых версий; все отправки и команды требуют оператора. Отдельно проверяются clarification/resume, duplicate events, timeout, restart, lost worker response и reconcile.
6. **Расширение:** только после отсутствия ложных допусков к статусу 29, утечек internal-текста, несовместимости старых envelope и необъяснённых replay/shadow divergence.
7. **Rollback:** вернуть новые версии сценариев в `shadow`, оставить v1 parser и исторические данные, деактивировать новые policy versions без удаления audit/evidence.

Этап 3 считается реализованным и протестированным после Definition of Done. Он считается готовым к production rollout только после миграции целевой среды и успешного shadow/HITL canary. Переход к полностью автономному `active` этим документом не разрешается.
