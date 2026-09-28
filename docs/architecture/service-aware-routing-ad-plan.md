# План service-aware routing, RedirectPlan и AD onboarding

> **Статус реализации (2026-09-28):** runtime использует catalog-first `TargetServiceResolution` до CaseDecision, затем отдельно строит workflow и проверяет execution binding. Актуальный live-каталог подтверждает сервисы `53` (создание пользователя сети), `59` (установка ПО), `104` (блокировка пользователя), `181` (доступ Wi-Fi), `183` (настройка принтера) и `184` (ремонт принтера). Отсутствие binding больше не скрывает целевой сервис; неподдерживаемый workflow получает явное ручное состояние `unsupported`.

## Цель

Перестроить обработку заявки вокруг разделённых решений:

```text
TicketSnapshot
  → TargetServiceResolution
  → CaseFrame
  → CaseDecision
  → ServiceCompatibilityDecision
       ├─ compatible → WorkflowPlan → ActionPlan
       ├─ mismatch → RedirectPlan
       └─ ambiguous/unknown → OperatorReview
```

Первый полноценный вертикальный срез объединяет проверяемое перенаправление на правильный сервис и создание пользователя AD только из разрешённого сервиса. Переход выполняется без shadow, dual-write и legacy fallback. Рабочий режим остаётся `ASSISTED`.

## 1. Каталог сервисов и ServiceBinding

### 1.1. Фактический каталог IntraService

- Выгрузить актуальные `service_id`, полный путь, родительский раздел, активность, тип формы и обязательные поля.
- Подтвердить точный сервис «01. Учётные записи пользователей → Создание нового пользователя сети».
- Проверить фактическое назначение IDs `55` и `232`.
- Запретить объединение Directum и AD без подтверждённого ServiceBinding.
- Рассчитывать канонический `catalog_hash` для каждой версии каталога.

### 1.2. ServiceCatalogEntry

```python
class ServiceCatalogEntry:
    service_id: int
    service_path: str
    parent_service_id: int | None
    task_type_id: int | None
    is_active: bool
    catalog_hash: str
```

Каталог синхронизируется через IntraService API и сохраняется локально. Неизвестный или неактивный сервис не может разрешать техническое действие.

### 1.3. ServiceRouteBinding

```python
class ServiceRouteBinding:
    key: str
    version: str
    service_ids: tuple[int, ...]
    allowed_case_types: tuple[str, ...]
    default_case_type: str | None
    allowed_workflows: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    required_task_type_id: int | None
    required_fields: tuple[int, ...]
    redirect_strategy: str
    risk: str
```

Для AD создаётся отдельный binding:

```text
binding_key: ad_account_creation
allowed_case_types:
  - employee_onboarding
allowed_workflows:
  - employee_onboarding_workflow
allowed_capabilities:
  - create_ad_user
risk: high
```

Binding активируется только после проверки всех его service IDs по текущему каталогу.

## 2. Разделение решений

### 2.1. CaseDecision

`CaseDecision` отвечает только на вопрос «какой бизнес-процесс требуется заявителю?» и не разрешает исполнение. Перенаправление и несовместимость сервиса не являются CaseType.

### 2.2. ServiceCompatibilityDecision

Состояния: `compatible`, `mismatch`, `ambiguous`, `unknown`, `degraded`.

Контракт содержит source service, binding/version, допустимые CaseType, кандидатов перенаправления, reason codes и `catalog_hash`.

Правила:

- CaseType разрешён текущим binding → `compatible`.
- CaseType запрещён, найден один целевой сервис → `mismatch`.
- Найдено несколько допустимых сервисов → `ambiguous`.
- Целевой сервис не найден → `unknown`.
- Каталог недоступен или binding невалиден → `degraded`.
- Для high-risk workflow состояния `unknown` и `degraded` всегда означают `manual`.

## 3. Доказательный каскад перенаправлений

Catalog-first resolver работает до CaseDecision и рассматривает только активные конечные сервисы синхронизированного каталога. Текущий конечный сервис является сильным исходным кандидатом; буквальное имя сервиса, совпавшие термины и принадлежность выбранному родителю могут предложить другой target. Возвращается не более пяти наблюдаемых кандидатов. После CaseDecision отдельная compatibility-фаза применяет bindings и формирует не более трёх разрешённых redirect-кандидатов.

LLM вызывается только в серой зоне и получает санитизированный публичный текст, CaseDecision, текущий сервис, максимум три существующих кандидата и их официальные описания. Допустимые ответы: `supported`, `contradicted`, `insufficient`.

LLM не может создавать service ID, изменять каталог, разрешать capability, применять redirect или отменять заявку.

## 4. RedirectPlan

### 4.1. Контракт

```python
class RedirectStrategy(str, Enum):
    cancel_and_recreate = "cancel_and_recreate"
    transfer_service = "transfer_service"
    manual = "manual"


class RedirectPlan:
    id: UUID
    task_id: int
    snapshot_hash: str
    case_decision_id: UUID
    compatibility_decision_id: UUID
    source_service_id: int
    target_service_id: int
    target_service_path: str
    strategy: RedirectStrategy
    template_key: str
    rendered_public_comment: str
    catalog_hash: str
    binding_version: str
    plan_hash: str
    state: str
```

Одновременно может существовать либо RedirectPlan, либо ActionPlan.

### 4.2. Первая рабочая стратегия

По действующим шаблонам первой стратегией становится `cancel_and_recreate`:

1. оператор подтверждает RedirectPlan;
2. backend повторно читает заявку;
3. проверяет snapshot, source service и target service;
4. публикует открытый комментарий с точным путём;
5. переводит заявку в статус «Отменена»;
6. сохраняет подтверждение каждого шага.

`transfer_service` разрешается только после проверки поддержки операции API IntraService.

### 4.3. API

```text
POST /tickets/{id}/redirect-plans/approve
POST /tickets/{id}/redirect-plans/correct
POST /tickets/{id}/redirect-plans/reject
POST /tickets/{id}/redirect-plans/manual-takeover
```

Операторская коррекция принимает только service ID из актуального каталога.

## 5. Жёсткая привязка AD provisioning

### 5.1. Compiler gate

`create_ad_user` не попадает в ActionPlan, пока одновременно не выполнены условия:

```text
compatibility.state == compatible
binding.key == ad_account_creation
employee_onboarding ∈ binding.allowed_case_types
employee_onboarding_workflow ∈ binding.allowed_workflows
create_ad_user ∈ binding.allowed_capabilities
service_id ∈ binding.service_ids
task_type соответствует binding
```

Название заявки, текст и ответ LLM сами по себе не разрешают `create_ad_user`.

### 5.2. ActionPlan binding

В ActionPlan добавляются и включаются в канонический hash: `source_service_id`, `service_binding_key`, `service_binding_version`, `catalog_hash`.

### 5.3. Dispatcher gate

Перед LDAP-операцией dispatcher по свежей заявке проверяет service ID, binding/version, разрешение capability, task type, snapshot/plan hashes, approval, bot assignment, preflight и отсутствие частичного предыдущего создания.

Машинные ошибки:

```text
ad_service_not_authorized
service_binding_changed
service_catalog_changed
task_type_not_authorized
ad_required_form_fields_missing
```

### 5.4. Обязательное поведение

- Правильный AD-сервис и полные данные → ActionPlan.
- Правильный AD-сервис и неполные данные → clarification.
- Правильный AD-сервис и противоречивые данные → manual.
- Чужой сервис и AD-намерение → RedirectPlan.
- Неизвестный сервис и AD-намерение → manual.
- Directum-сервис никогда не разрешает `create_ad_user` без явного binding.

## 6. Workflow уточнений для AD

```text
intake
  → awaiting_facts
  → ready_for_approval
  → executing
  → verifying
  → completed
  → needs_review
```

При недостатке данных система формирует один комментарий, ставит заявку в «Приостановлена», ждёт новое публичное событие, создаёт новый snapshot и повторно компилирует workflow. После двух неудачных циклов заявка переходит в `manual`. Обязательные факты определяются формой подтверждённого AD-сервиса, а не только свободным текстом.

## 7. API и UI

`TicketAutomationDTO` расширяется:

```text
case_frame
case_decision
service_compatibility
workflow_plan
redirect_plan
action_plan
approval
execution
```

UI показывает текущий сервис, CaseType, compatibility, причину mismatch, кандидатов, evidence, target service, стратегию, комментарий и отдельное approval. Для AD явно отображается либо разрешивший binding, либо причина блокировки provisioning.

## 8. Хранение и миграция

Добавить таблицы:

```text
service_catalog_entries
service_catalog_versions
service_route_bindings
service_compatibility_decisions
redirect_plans
redirect_feedback
```

Расширить `action_plans` полями source service, binding key/version и catalog hash.

Так как проект не находится в production, текущая незакоммиченная миграция ADR 0006 заменяется чистой миграцией. Перед пересозданием dev-БД сохраняется обезличенный исследовательский экспорт решений.

## 9. Feedback и datasets

Разделить versioned datasets:

1. TicketSnapshot + catalog → TargetServiceResolution.
2. TicketSnapshot + target service → CaseDecision.
3. CaseDecision + source/target service + bindings → compatibility.
4. Confirmed CaseFrame + compatible binding → ActionPlan.

Feedback хранится отдельно для CaseType, compatibility, target service, facts, ActionPlan и execution. Он не меняет активную policy автоматически.

Метрики:

- CaseType accuracy;
- redirect top-1/top-3 accuracy;
- лишние redirects;
- `unknown_target` rate;
- выдуманный LLM service ID — конструктивно невозможен;
- `create_ad_user` из неподходящего сервиса — строго 0.

## 10. Проверки

### AD

- Правильный сервис: полные, неполные, пустые и противоречивые данные.
- Чужой и неизвестный сервис с точным AD-намерением.
- Directum-заявка с фразой «создать пользователя».
- Изменение сервиса, binding или catalog после approval.
- Частично созданный AD-объект и ошибка записи Field1488/Field1489.
- Пароль отсутствует во всех persisted/logged контрактах.

### Redirect

- Один и несколько допустимых targets.
- LLM подтверждает только кандидата из каталога.
- Неизвестный или удалённый target.
- Изменение source service перед применением.
- Duplicate delivery и частичный результат комментарий/статус.
- Operator correction только на существующий target.

Главная security-регрессия:

```text
create_ad_user из неавторизованного сервиса = 0
при любых текстах и LLM-ответах
```

## 11. Последовательность реализации

1. Выгрузить каталог и подтвердить канонический AD-сервис.
2. Добавить ServiceCatalog и ServiceRouteBinding.
3. Разделить CaseDecision и ServiceCompatibilityDecision.
4. Реализовать constrained RedirectResolver.
5. Подключить LLM-verifier только для серой зоны.
6. Реализовать RedirectPlan, approval и `cancel_and_recreate`.
7. Добавить service binding в ActionPlan hash.
8. Добавить compiler/dispatcher gates для `create_ad_user`.
9. Реализовать clarification lifecycle AD workflow.
10. Обновить API, UI, feedback и datasets.
11. Удалить `non_it_redirect_workflow` и redirect-заглушки.
12. Выполнить offline replay, security regression и полный clean rebuild.
13. Проверить реальные заявки правильного, неправильного и неоднозначного сервиса.

## Критерии завершения

- Текст или LLM-ответ не может разрешить `create_ad_user` вне AD ServiceBinding.
- Каждый RedirectPlan содержит существующий target актуального каталога.
- RedirectPlan и ActionPlan подтверждаются оператором раздельно.
- Изменение сервиса, binding или catalog блокирует одобренный план.
- Clarification публикует вопрос, приостанавливает заявку и продолжает workflow после ответа.
- `non_it_redirect_workflow` и фиктивные redirect dispositions отсутствуют.
- Runtime использует только новую цепочку без legacy fallback.
- Offline replay, security tests, полный pytest, Ruff и frontend build проходят.
