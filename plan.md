# План модернизации Decisioning/Rule Engine IntraLink

## Краткое решение

Перестроить контур по модели **Functional Core + Imperative Shell**, сохранив существующие `TicketRun`, `DecisionRecord`, Command Bus и Worker SDK:

```text
Ticket → Facts → Validation → Typed Outcome
       → Resolution Policy / Approval
       → Command → Worker validate/preflight/execute/verify
       → Verified Result → Template → Ticket status
```

Логика правил остаётся типизированным Python-кодом. PostgreSQL становится SSOT для версионируемых шаблонов и resolution policies. Локальная Ollama используется только как fallback для извлечения фактов из свободного текста. `triage_rules`, собственный JSON DSL, Temporal, CEL и LangGraph в эту реализацию не входят.

## Ключевые изменения

### 1. Общая доменная модель

- Создать в `shared/domain` строгие Pydantic-типы:
  - `TicketFacts`, `PersonCandidate`, `ValidPerson`;
  - `Evidence`, `ValidationError`;
  - `ClarificationRequired`, `ActionProposed`, `ManualReviewRequired`, `NoMatch`;
  - discriminated union `DecisionOutcome`;
  - типизированные параметры и результаты каждого action.
- Настроить `extra="forbid"` и версионирование схем.
- Вынести в shared нормализацию и валидацию ФИО, подразделения, ПК, принтера и идентификаторов.
- Core API валидирует данные при принятии решения; Worker независимо повторяет критическую валидацию перед side effect.
- Правила возвращают только typed outcome и evidence. Текст, статус, трудозатраты и утверждение о выполнении из правил убрать.
- Зафиксировать порядок правил явным ordered registry; отказаться от двусмысленного числового `priority`.

### 2. Шаблоны и resolution policies

- Создать таблицу `response_templates`:
  - immutable `key`;
  - `version`, `template_text`, `required_variables`;
  - `is_active`, автор и timestamps.
- Создать таблицу `resolution_policies`:
  - `outcome_key`, `version`, `outcome_kind`;
  - FK на активный template;
  - допустимый `target_status_id`, `expenses`, `action_id`;
  - `risk_level`, `requires_approval`, `is_active`.
- Ввести уникальность активной версии и DB constraints для outcome kind, risk и статусов.
- Перенести существующие `triage_templates` в обе новые таблицы Alembic-миграцией; `account_details_clarify` добавить idempotent data migration.
- Использовать sandboxed Jinja с `StrictUndefined`, allowlist-переменными и лимитом размера результата. [Рекомендации Jinja Sandbox](https://jinja.palletsprojects.com/en/stable/sandbox/).
- В production запретить JSON fallback для обязательного решения: отсутствие policy/template переводит обработку в `system_error` или `manual_review`.
- Сохранять в `DecisionRecord` версии rule, outcome schema, policy, template и итоговый отрендеренный текст.
- На один совместимый релиз оставить старые `/templates` и `templates-catalog`; затем перевести UI на `/api/v2/rules-admin/response-templates`, `/resolution-policies` и удалить поля-дубликаты.

### 3. Credentials и безопасное создание AD-пользователя

- Реализовать валидатор структурной правдоподобности ФИО:
  - фамилия и имя обязательны;
  - минимум два буквенных символа;
  - кириллица, пробел и дефис по корпоративному контракту;
  - нормализация регистра и пробелов;
  - запрет цифр, служебных значений и стоп-слов `test`, `тест`, `asdf`, `null`, `none`, `-`.
- Заявка #140479 должна давать:
  - `ClarificationRequired`;
  - outcome `account_details_invalid`;
  - шаблон `account_details_clarify`;
  - статус 35;
  - отсутствие команды `create_user`.
- Валидная заявка должна давать только `ActionProposed(action="create_user")`, без статуса 29 и текста «успешно создан».
- Добавить `CreateUserHandler` в Worker SDK:
  - typed input;
  - `validate`: повторная доменная проверка;
  - `preflight`: OU, организация, подразделение, коллизии ФИО/login, доступность DC;
  - `execute`: адаптер над существующим AD executor;
  - `verify`: read-after-write, Enabled, DN, UPN и необходимые группы;
  - `reconcile`: поиск фактического результата перед повтором;
  - `RiskClass.NEVER_AUTO_RETRY`.
- Оставить `create_user` строго в режиме `CONFIRM`; отметить его `implemented=True` только после регистрации handler и публикации capability Worker.
- Статус 29 и `user_created` разрешать исключительно после `verified_success`.
- Не сохранять временный пароль в decision/event/log payload. Передавать его через зашифрованный краткоживущий secret artifact, использовать один раз при публикации ответа и оставлять в аудите только metadata.

### 4. Локальная LLM как extraction fallback

- Порядок источников:
  1. структурированные поля IntraService;
  2. детерминированный parser;
  3. Ollama только для отсутствующих фактов из текста;
  4. доменная валидация;
  5. rules-as-code.
- Ввести Pydantic-схему `ExtractedTicketFacts` с nullable-значениями, evidence spans и ambiguities.
- Передавать её JSON Schema в Ollama structured outputs; schema-constrained ответы поддерживаются Ollama напрямую. [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs).
- LLM не получает права выбирать статус, создавать command или исправлять явно невалидное структурированное поле.
- При timeout, invalid JSON, отсутствии evidence или конфликте источников возвращать deterministic fallback/`ManualReviewRequired`.
- Запускать LLM-ветку под feature flag сначала в shadow mode; включать в основной контур только после offline eval.
- Не выбирать новую модель заранее: сравнить доступные локальные модели на обезличенном наборе закрытых заявок по precision, abstention rate, latency и RAM/VRAM.

## Реализация по фазам

1. **Safety fix:** shared-валидатор, `account_details_clarify`, regression #140479 и запрет невалидного `create_user`.
2. **Typed decisions:** добавить discriminated outcomes и адаптер старого `RuleDecision`; включить shadow comparison без изменения рекомендаций.
3. **SSOT migration:** создать templates/policies, backfill, строгий resolver и v2 API; старый каталог временно собирать из новых таблиц.
4. **Credentials cutover:** переключить `CredentialsRule` на typed outcome и включить status 35 для невалидных данных.
5. **Worker v2:** подключить `CreateUserHandler`, HITL, secret artifact и verified completion; legacy AD entrypoint закрыть от прямого обхода.
6. **LLM extraction:** shadow eval, canary и ограниченное включение fallback.
7. **Остальные правила:** последовательно перевести printer/offline/redirect/device/file-lock/RAG, сравнивая решения со старым движком.
8. **Cleanup:** удалить `TriageRule`, `/rules`, `templates.json` production fallback, legacy `RuleDecision`, старые поля и совместимые endpoints после одного релизного окна.

## Тесты и критерии приёмки

- Табличные и property-based тесты валидаторов; добавить Hypothesis для генерации Unicode, пробелов, дефисов, стоп-слов и мусорных значений. [Документация Hypothesis](https://hypothesis.readthedocs.io/en/latest/).
- Contract-тест: каждый `DecisionOutcome` проходит Pydantic round trip; недопустимые комбинации полей отвергаются.
- Regression #140479: статус 35, корректный комментарий, ноль AD-команд.
- Валидный create-user: `awaiting_approval → preflight → execute → verify → status 29`.
- Duplicate user, неизвестный OU, timeout DC, потерянный ответ и verification mismatch: статус 29 не выставляется; результат `needs_review`/`verified_failure`.
- Template tests: отсутствующая переменная, template, policy или неверный статус дают fail-closed.
- Migration tests на чистой и заполненной PostgreSQL; повторный upgrade не создаёт дубликаты.
- API compatibility tests для старого каталога и нового v2.
- LLM eval: обязательные поля принимаются только при наличии evidence и прохождении детерминированной валидации; LLM-сбой не влияет на доступность deterministic-контура.
- Shadow metrics:
  - расхождение old/new decision;
  - clarification rate;
  - manual-review rate;
  - invalid-action prevention;
  - template/policy errors;
  - Worker verification failures.
- Cutover допускается при нулевых unsafe action в тестовом наборе и ручной проверке всех расхождений high-risk сценариев.

## Принятые допущения

- Выбрана поэтапная модернизация без big-bang.
- Локальная LLM используется только как extraction fallback.
- Административное редактирование условий правил пока не требуется.
- В план включён полноценный безопасный `create_user` v2.
- Шаблоны и resolution policies разделяются сразу логически, но старый API сохраняется на одно окно совместимости.
- Все изменяющие AD-действия остаются HITL; автономность можно рассматривать только отдельным ADR после накопления production evidence.
