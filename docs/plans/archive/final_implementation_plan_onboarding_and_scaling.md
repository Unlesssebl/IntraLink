# Фундаментальный план онбординга и масштабирования по ADR 0006

- **Статус:** Draft — готов к реализации после архитектурного согласования
- **Архитектурный стандарт:** [ADR 0006: CaseFrame, прикладные процессы и технические capabilities](docs/architecture/adr/case-workflow-capability-architecture.md)
- **Область первой поставки:** безопасный `employee_onboarding_workflow` для подтверждённого сервиса создания пользователя сети
- **Режим:** `ASSISTED`, fail-closed, без `FULL_AUTO`, параллельного legacy-движка и автоматического обучения на единичной обратной связи
- **Инженерный принцип:** исключить условные ветви по конкретным `service_id` из доменной логики, не подменяя их невалидированной конфигурацией

---

## 1. Контекст и цель

Дефекты, обнаруженные на заявке №142250, связаны не с одним экраном, а с разрывами между каталогом услуг, workflow, preflight, уточнениями и исполнением:

- физические ID полей IntraService находятся внутри AD provisioning;
- readiness и UI частично привязаны к `employee_onboarding_workflow`;
- неоднозначный выбор OU представлен ошибкой, а не устойчивым Human-In-The-Loop состоянием;
- активное clarification может продолжать блокировать workflow после появления фактов;
- универсальный endpoint коррекции ActionPlan не является безопасным контрактом выбора OU;
- результат AD mutation и результат доставки credentials не образуют одну транзакцию и требуют явной saga-модели.

Целевой конвейер:

```text
TicketSnapshot
    -> CaseFrame
    -> CaseDecision
    -> ServiceCompatibilityDecision
    -> validated ServiceRouteBinding
    -> WorkflowPlan
    -> ActionPlan
    -> Preflight
         -> HumanActionRequest, если нужен ограниченный выбор оператора
         -> новый ActionPlan и новый plan_hash после выбора
    -> Approval
    -> ActionCommand
    -> fail-closed dispatcher revalidation
    -> capability execution
    -> protected credential delivery
    -> verified completion или needs_review
```

Семантическое распознавание, LLM, текст заявки и операторская коррекция сами по себе не разрешают `create_ad_user`. Разрешение даёт только активный и валидированный binding текущего сервиса, task type, workflow и capability.

---

## 2. Инварианты безопасности

Следующие условия обязательны во всех слоях — compiler, API, worker и executor:

1. `create_ad_user` разрешается только через активный `ad_account_creation` binding, который одновременно подтверждает:
   - текущий `source_service_id`;
   - `employee_onboarding`;
   - `employee_onboarding_workflow`;
   - `create_ad_user`;
   - task type и обязательные поля, если они заданы;
   - текущий валидированный `catalog_hash`.
2. Для сервисов Directum `55` и `232` вызов `create_ad_user` равен строго нулю. Их поля и будущая автоматизация оформляются отдельным binding/workflow/capability и не входят в первую поставку.
3. Изменение snapshot, catalog, binding, ActionPlan, params или HITL generation после preflight/approval блокирует исполнение.
4. Исполняемыми статусами preflight остаются только `passed` и `not_applicable`.
5. Plaintext-пароль не попадает в:
   - `AccountProvisioningReceipt`;
   - `CapabilityExecution.proof`;
   - API DTO и UI;
   - `plan_json`, feedback и audit records;
   - комментарии, логи и exception messages;
   - контекст агента.
6. Неопределённый исход внешней mutation никогда не запускает автоматический повтор или компенсацию.
7. Кнопки и server-driven actions в UI не являются механизмом авторизации: каждый API endpoint повторяет проверку snapshot/hash/state/permissions.

---

## 3. Архитектурные решения

| Область | Отклонённый вариант | Принятое решение | Причина |
|---|---|---|---|
| Доставка credentials | `dict[str, int]` и возврат пароля из provisioning | Типизированная versioned `CredentialDeliveryPolicy`; секрет передаётся только внутреннему delivery port | Исключает утечку секрета и неоднозначность полей |
| Выбор OU | Произвольный `target_ou_dn` через общий ActionPlan correction | Durable `HumanActionRequest` с bounded candidates | Защищает от подмены DN и stale-выбора |
| Clarification | Массовый `UPDATE` по `task_id` | Generation-aware CAS по workflow/snapshot/fingerprint | Не закрывает чужие и устаревшие запросы |
| Delivery failure | Безусловная деактивация AD-аккаунта | Saga с `succeeded`, `definitely_failed`, `unknown_outcome` | Не выполняет опасную компенсацию после timeout |
| UI | Workflow-specific панель | Server-driven presentation contract и ограниченный registry renderer-ов | Масштабирование без логики service ID на фронтенде |

---

## 4. Каталог услуг и защищённая доставка

### 4.1. Типизированная delivery policy

Вместо неструктурированного `delivery_fields: dict[str, int]` вводятся контракты:

```python
class DeliverySensitivity(str, Enum):
    public = "public"
    secret = "secret"


class CredentialDeliveryField(FrozenModel):
    key: str
    field_id: int = Field(gt=0)
    sensitivity: DeliverySensitivity
    required: bool = True


class CredentialDeliveryPolicy(FrozenModel):
    channel: Literal["intraservice_custom_fields"]
    fields: tuple[CredentialDeliveryField, ...]
    verify_after_write: bool = True


class ServiceRouteBinding(FrozenModel):
    # существующие поля
    delivery_policy: CredentialDeliveryPolicy | None = None
```

Для `ad_account_creation` первой поставки:

```text
service_ids = [53]
user_login      -> field 1439, sensitivity=public
initial_password -> field 1440, sensitivity=secret
```

Binding активируется только после проверки, что сервис 53 существует в текущем каталоге, активен, имеет ожидаемый task type и содержит поля 1439/1440 подходящего типа. Конфигурация входит в versioned binding; изменение policy требует новой версии binding и инвалидирует старые планы.

Сервисы 55/232 не добавляются в `ad_account_creation`. Для них отдельная delivery policy будет проектироваться вместе с Directum capability.

### 4.2. Граница секрета

`AccountProvisioningService` не возвращает пароль. Он получает типизированную policy и внутренний `CredentialDeliveryPort`, создаёт пароль, выполняет LDAP mutation и передаёт секрет непосредственно delivery port в пределах одного доверенного вызова.

Публичная квитанция:

```python
@dataclass(frozen=True)
class AccountProvisioningReceipt:
    sam_account_name: str
    upn: str
    user_dn: str
    full_name: str
    credentials_written: bool
    delivery_receipt_id: str | None = None
```

Внутренний credential envelope запрещено сериализовать, логировать или помещать в Pydantic/SQLAlchemy модели. После доставки локальная ссылка на plaintext очищается в `finally`; это уменьшает время жизни секрета, но не считается криптографическим стиранием памяти Python.

### 4.3. Saga доставки

Результат доставки классифицируется:

- `succeeded`: защищённые поля записаны и результат подтверждён допустимым ответом/read-back;
- `definitely_failed`: сервер однозначно отклонил запись до её применения;
- `unknown_outcome`: timeout, разрыв соединения или неоднозначный ответ после отправки.

Правила:

- `succeeded` разрешает завершение workflow;
- `definitely_failed` переводит workflow в `needs_review`; автоматическая деактивация допускается только отдельной утверждённой policy и должна подтверждаться read-back;
- `unknown_outcome` всегда переводит workflow в `needs_review`, запрещает повторное создание и автоматическую компенсацию;
- повторная доставка или disable выполняются отдельной операторской recovery-командой после проверки фактического состояния заявки и AD.

---

## 5. Clarification Convergence

`ClarificationConvergence` становится универсальным сервисом, но не выполняет массовое закрытие записей по `task_id`.

Для схождения требуется совпадение:

- `task_id`;
- `workflow_plan_id`;
- `workflow_key`;
- актуального `snapshot_hash` или явно сохранённой generation;
- `request_fingerprint` активного clarification;
- ожидаемого состояния `pending_publish` или `published`.

Алгоритм:

1. Пересобрать CaseFrame и WorkflowPlan из актуального snapshot.
2. Определить отсутствующие факты только из актуального `workflow_plan.missing_facts`.
3. Если факты всё ещё отсутствуют — создать или переиспользовать idempotent clarification текущей generation.
4. Если факты полны — выполнить compare-and-set только для связанного активного clarification.
5. Записать терминальное состояние `resolved`, `resolved_at`, `resolution_source="facts_converged"` и hash/generation фактов, вызвавших схождение.
6. Если CAS обновил `0` строк, перечитать состояние и не считать это ошибкой: другой процесс мог уже разрешить или supersede запрос.

Для `resolution_source` и generation добавляются явные поля модели и миграция. Опубликованный комментарий IntraService не удаляется; изменяется только внутреннее состояние и readiness.

`_build_readiness()` не восстанавливает blockers из устаревшего clarification. Источник истины для отсутствующих фактов — актуальный WorkflowPlan; clarification используется только для отображения состояния публикации текущей generation.

---

## 6. Human-In-The-Loop как доменная сущность

### 6.1. Durable контракт

Добавляется `HumanActionRequest`:

```python
class HumanActionRequest(FrozenModel):
    id: UUID
    task_id: int
    workflow_plan_id: UUID
    action_plan_id: UUID
    action_id: str
    snapshot_hash: str
    plan_hash: str
    generation: int
    type: Literal["single_choice"]
    target_param: str
    prompt: str
    candidates: tuple[HumanActionCandidate, ...]
    state: Literal["pending", "resolved", "expired", "superseded"]
    expires_at: datetime
```

Кандидат содержит непрозрачный `candidate_id`, отображаемый label, score и evidence. Значение `target_ou_dn` хранится на сервере и не принимается от клиента как свободная строка.

OU candidates формируются только из read-only LDAP-поиска внутри разрешённого `users_root_ou`. Универсальный порог `0.85` не фиксируется для всех resolver-ов: каждый resolver имеет versioned calibration policy и отдельные acceptance-метрики.

### 6.2. Разрешение выбора

Выбор выполняется отдельным endpoint:

```text
POST /api/v2/autopilot/tickets/{ticket_id}/human-actions/{human_action_id}/resolve
```

Запрос содержит `candidate_id`, `snapshot_hash`, `plan_hash` и `generation`. Сервер:

1. Блокирует запись `HumanActionRequest` через `SELECT ... FOR UPDATE`.
2. Проверяет task, operator permission, state, expiry, snapshot, plan hash и generation.
3. Находит candidate только в сохранённом bounded-наборе.
4. Повторно проверяет существование OU и его принадлежность `users_root_ou`.
5. Помечает старый HumanActionRequest `resolved`.
6. Отменяет старый ActionPlan и компилирует новый с новым ID/hash.
7. Запускает новый preflight; старые approval и preflight не переносятся.

Общий `POST /action-plans/correct` не используется для свободной передачи `target_ou_dn`.

---

## 7. Гранулярный preflight

Вводится типизированный check DTO:

```python
class PreflightCheckStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    degraded = "degraded"
    action_required = "action_required"
    not_applicable = "not_applicable"


class PreflightCheck(FrozenModel):
    key: str
    status: PreflightCheckStatus
    reason_code: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)
    human_action_id: UUID | None = None
```

Aggregate `CapabilityPreflight.status` получает `action_required`, но executable allowlist не расширяется: worker по-прежнему принимает только `passed`/`not_applicable` и только неистёкший preflight, связанный с текущими snapshot/plan/params hashes.

Пример:

```json
{
  "status": "action_required",
  "checks": [
    {"key": "ad_available", "status": "passed"},
    {"key": "login_preview", "status": "passed", "details": {"login": "akhmedzh.a"}},
    {"key": "dn_absent", "status": "passed"},
    {"key": "target_ou", "status": "action_required", "human_action_id": "..."}
  ]
}
```

Секретные значения запрещены в `details` и проходят существующую sanitization перед persistence.

---

## 8. Server-driven UI

`OnboardingWorkflowPanel.tsx` заменяется `WorkflowExecutionCard.tsx`, но UI остаётся типизированным и не пытается рендерить произвольную серверную разметку.

`TicketAutomationDTO` расширяется:

- `readiness.stages` — порядок и состояние этапов;
- `readiness.actions` — разрешённые UI-действия;
- `readiness.preflight.checks` — типизированные проверки;
- `human_actions` — активные HITL-запросы;
- `presentation.workflow_kind` и `presentation.labels` — ключи для ограниченного registry renderer-ов.

Frontend:

- не вычисляет завершённость через `index < current_index`;
- не содержит ветвей по `service_id`;
- не отправляет произвольный DN;
- показывает только bounded candidates;
- использует `candidate_id` при разрешении HITL;
- после любого выбора полностью обновляет automation DTO;
- не отображает secret fields даже в raw technical JSON.

Компоненты первой поставки:

- `WorkflowExecutionCard`;
- `WorkflowStageList`;
- `HumanActionSlot`;
- `HitlCandidateSelector`;
- `PreflightCheckList`;
- `WorkflowActionToolbar`.

---

## 9. Миграции и совместимый порядок развёртывания

Текущий migration head — `0008_clarification_event_identity`. Создаётся следующая Alembic revision, условно `0009_onboarding_delivery_hitl`.

Миграция:

1. Добавляет `delivery_policy_json` в `service_route_bindings` с безопасным пустым default.
2. Создаёт `human_action_requests` с индексами по task/state, уникальностью generation/fingerprint и FK на WorkflowPlan/ActionPlan.
3. Добавляет в `clarification_requests` поля `generation`, `resolution_source`, `resolved_snapshot_hash`.
4. Не активирует binding автоматически.
5. Имеет проверяемый downgrade для schema-only изменений; production data rollback выполняется отдельным runbook.

Необходимо обновить все пути чтения и записи binding:

- `ServiceRouteBinding`;
- `ServiceRouteBindingRecord`;
- `ServiceCatalogRepository.current()`;
- `validate_and_store_binding()`;
- seed/admin API;
- сериализацию, tests и catalog validation.

Порядок развёртывания без параллельного второго движка:

1. Применить backward-compatible migration.
2. Развернуть API/core, умеющие читать пустую и новую policy; выполнение AD оставить выключенным.
3. Развернуть worker с новыми binding/HITL/preflight gates.
4. Создать, провалидировать и активировать новую версию binding сервиса 53.
5. Развернуть frontend.
6. Выполнить offline replay и staging acceptance.
7. Включить `AD_ONBOARDING_EXECUTION_ENABLED` только для утверждённого production canary.

Rollback до шага 7: деактивировать новую версию binding и вернуть предыдущие приложения. После первой реальной LDAP mutation автоматический schema/application rollback не выполняется; используется recovery runbook по фактическому состоянию AD и заявки.

---

## 10. Этапы реализации

### Этап 1. Контракты и миграция

- Реализовать `CredentialDeliveryPolicy`, `HumanActionRequest`, granular preflight DTO.
- Добавить migration `0009` и repository mappings.
- Включить delivery policy и HITL generation в канонические hashes там, где они влияют на исполнение.
- Добавить отрицательные contract/security tests.

**Выход:** schema и контракты готовы, runtime-поведение не активировано.

### Этап 2. Защищённый AD provisioning

- Вынести физические ID полей из `core/ad/provisioning.py`.
- Реализовать внутренний `CredentialDeliveryPort`.
- Сохранить публичную квитанцию без пароля.
- Реализовать классификацию delivery outcome и recovery states.
- Добавить явную поддержку подтверждённого `target_ou_dn` с containment/read-back проверкой.

**Выход:** executor работает в тестах/staging, production flag выключен.

### Этап 3. Convergence и HITL

- Реализовать generation-aware `ClarificationConvergence`.
- Создавать durable `HumanActionRequest` при неоднозначном OU.
- Реализовать endpoint разрешения выбора и пересборку ActionPlan.
- Инвалидировать старые preflight/approval после любого изменения параметров.

**Выход:** backend lifecycle завершён и проверен конкурентными тестами.

### Этап 4. Универсальный readiness и UI

- Убрать onboarding-only guard из `_build_readiness()`.
- Строить стадии из workflow/action graph и активных human actions.
- Заменить `OnboardingWorkflowPanel` универсальной карточкой.
- Удалить raw JSON, способный раскрыть чувствительные поля.

**Выход:** UI следует серверному состоянию и не содержит service-specific authorization logic.

### Этап 5. Offline и staging verification

- Запустить полный набор тестов, обнаруженный test runner; не фиксировать ожидаемое число тестов в плане.
- Выполнить закрытый offline replay на versioned dataset.
- Проверить migration upgrade/downgrade на копии схемы.
- Выполнить staging flow на тестовой заявке и тестовом AD-контуре.
- Подтвердить отсутствие пароля в логах, DTO, БД, comments и traces.

**Выход:** код реализован и протестирован, но ещё не production-active.

### Этап 6. Production canary и activation

- На реальной заявке №142250 разрешены только read-only reanalysis/preflight, пока не назначено отдельное change window и операторское approval.
- Первый mutating canary выполняется на специально созданной production-заявке и контролируемой тестовой учётной записи.
- Перед mutation повторно проверяются service binding, task assignment, snapshot, plan hash, preflight expiry, approval и отсутствие предыдущего неоднозначного результата.
- После выполнения проверяются AD object, защищённые поля заявки, audit trail и отсутствие secret leakage.
- Production flag расширяется только после успешного canary и ручного sign-off.

**Выход:** состояние `production-active` фиксируется отдельно от `implemented/tested`.

---

## 11. Матрица обязательных тестов

### Авторизация и безопасность

- сервис 53 + активный валидированный binding разрешает формирование `create_ad_user`;
- сервисы 55/232 и любой неизвестный сервис дают `0` LDAP mutations;
- неактивный, невалидированный или устаревший binding блокирует compiler и dispatcher;
- изменение catalog/binding после approval блокирует команду;
- пароль отсутствует во всех сериализуемых объектах, persistence, логах и комментариях.

### HITL и concurrency

- произвольный или чужой `candidate_id` отклоняется;
- свободный `target_ou_dn` не принимается;
- OU вне `users_root_ou` отклоняется;
- stale snapshot/plan/generation возвращает conflict;
- двойной выбор одного HumanActionRequest идемпотентен: побеждает один CAS;
- resolved/expired/superseded request нельзя использовать повторно.

### Clarification

- заполнение фактов разрешает только clarification текущего workflow/generation;
- clarification другого workflow или старого snapshot не изменяется;
- повторный analyze не создаёт дубликат активного запроса;
- неудачная публикация и последующее схождение фактов не оставляют ложный blocker.

### Preflight и execution

- `action_required`, `failed`, `degraded` и expired preflight не исполняются;
- выбор кандидата создаёт новый plan hash и требует нового preflight/approval;
- dispatcher повторяет binding и snapshot gates непосредственно перед LDAP;
- предыдущий `succeeded`, `partial` или `unknown_outcome` запрещает повторное создание.

### Delivery saga

- успешная запись обоих полей завершается только после verification;
- definite failure переводит workflow в контролируемое recovery-состояние;
- timeout/unknown outcome не вызывает retry или автоматический disable;
- recovery-команда требует отдельного approval и подтверждает результат read-back.

### UI

- этапы и toolbar соответствуют server DTO;
- UI не содержит ветвей по `service_id`;
- candidate selector отправляет только `candidate_id` и binding hashes;
- secret fields отсутствуют в technical/raw представлении.

---

## 12. Критерии готовности

### Implemented

- миграция и контракты добавлены;
- backend, worker и UI реализованы;
- targeted security tests и полный regression suite проходят;
- offline replay и staging acceptance задокументированы.

### Production-ready

- binding сервиса 53 подтверждён по актуальному live-каталогу;
- deployment order выполнен, health checks зелёные;
- production canary успешен;
- secret-leak audit чист;
- recovery runbook проверен;
- оператор дал явный sign-off на activation.

### Не является доказательством production readiness

- успешная сборка image;
- unit tests без migration/runtime проверки;
- read-only preflight на заявке №142250;
- наличие UI-кнопки;
- документация без трассировки compiler → approval → dispatcher → executor → verification.

---

## 13. Ожидаемый архитектурный эффект

После реализации первой поставки:

- ни один AD executor не знает физические ID полей конкретного сервиса;
- plaintext credentials не покидают доверенную provisioning/delivery границу;
- неоднозначность OU является устойчивым, аудируемым HITL-состоянием;
- clarification сходится по актуальной generation без чужих side effects;
- UI отображает серверный workflow contract без условных ветвей по service ID;
- новый сценарий подключается декларативно в пределах уже поддерживаемых типов facts, capabilities, HITL и presentation; новые технические классы поведения по-прежнему требуют реализации, тестов и versioned rollout;
- соответствие ADR 0006 подтверждается не формулировкой, а fail-closed контрактами и сквозными тестами.
