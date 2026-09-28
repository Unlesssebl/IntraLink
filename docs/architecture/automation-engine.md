# Действующий движок автоматизации заявок

Актуальная runtime-архитектура IntraLink реализует ADR 0006 и использует единственную цепочку:

```text
TicketSnapshot → TargetServiceResolution → CaseFrame → CaseDecision → WorkflowPlan
                                              ↓
                                ServiceCompatibilityDecision
                                  ├─ authorized → ActionPlan → ActionCommand
                                  ├─ mismatch → RedirectPlan
                                  └─ binding unavailable → OperatorReview
```

## Границы ответственности

- `TicketSnapshot` — неизменяемый санитизированный снимок заявки. Закрытые комментарии, содержимое вложений и секретные поля в него не входят.
- `TargetServiceResolution` — раннее catalog-first решение о целевом сервисе. Оно не зависит от CaseType и binding и сохраняет top-кандидатов с доказательствами.
- `CaseFrame` — утверждения, отрицания, сущности и буквальные ссылки на исходный текст.
- `CaseDecision` — только тип обращения: `selected`, `multi_intent`, `ambiguous`, `unknown` или `degraded`.
- `ServiceCompatibilityDecision` — отдельная проверка текущего сервиса по активному каталогу и versioned binding.
- `WorkflowPlan` — бизнес-процесс первой линии, недостающие факты, диагностика и disposition.
- `ActionPlan` — упорядоченный список разрешённых capabilities с каноническим hash.
- `ActionCommand` — ровно одна capability, связанная с plan/action/snapshot/params hashes.
- `RedirectPlan` — самостоятельный approval-bound план перенаправления; он не является CaseType, disposition или capability.

Intake никогда не выбирает `reset_print_spooler`, `disable_ad_user` или другую техническую команду. Сначала определяется целевой сервис, затем смысл обращения; техническое действие появляется только после readiness и диагностики внутри workflow. Отсутствие зарегистрированного workflow даёт явный `unsupported_workflow` с ручным disposition, не стирая найденный сервис и CaseType.

## Роль оператора

Текущий cutover полностью полуавтоматический:

1. Оператор явно запускает анализ заявки.
2. Проверяет исходный и целевой сервисы, доказательства маршрутизации, CaseFrame, CaseDecision, workflow, недостающие факты и ActionPlan.
3. При необходимости отдельно исправляет тип обращения либо параметры ActionPlan.
4. Назначает сервисную учётную запись исполнителем заявки.
5. Подтверждает конкретный ActionPlan.
6. Worker повторяет только технические guards и исполняет capability.

Poller не запускает анализ или инфраструктурную мутацию. Назначение сервисной учётной записи само по себе ничего не исполняет. `FULL_AUTO`, shadow, canary, dual-write и legacy fallback в действующем runtime отсутствуют.

## Защитные проверки исполнения

Перед побочным эффектом worker проверяет неизменность snapshot, точное соответствие plan/action/capability/params hashes, свежий успешный preflight, назначение сервисной учётной записи, нетерминальный статус заявки, отсутствие terminal feedback и владение распределённой lease. Для `create_ad_user` дополнительно заново проверяются source service, task type, активный catalog hash, binding key/version, обязательные поля формы и отсутствие успешного либо частичного предыдущего создания.

Каталог синхронизируется read-only через `POST /api/v2/autopilot/service-catalog/sync`. Пустой или недоступный ответ не активирует новую версию. Binding `ad_account_creation` создаётся неактивным и становится исполнимым только после подтверждения фактического AD-сервиса по текущему каталогу.

Следующая capability публикуется только после подтверждённого успеха предыдущей. `failed` и `unknown_outcome` переводят план в `needs_review`; заявка не закрывается.

## LLM

Сначала проверяется актуальный каталог и выбирается целевой сервис. Активный конечный source service либо единственный сильный текстовый кандидат поступает в детерминированный intake как доказательство. Если это уже даёт прямое решение, LLM не вызывается. Только для `unknown` и серой зоны LLM получает санитизированный публичный текст, возвращает grounded assertions из закрытого словаря, после чего evidence пересчитывается и максимум три существующих кандидата передаются verifier. LLM не создаёт service ID, case type или capability, не разрешает выполнение, не подтверждает результат и не закрывает заявку.

Gateway владеет provider retries и fallback; OpenAI SDK не выполняет второй слой retry. Deadline extractor составляет 30 секунд и покрывает медленный локальный fallback. При сбое сохраняется детерминированный результат с точной причиной (`extractor_timeout`, `extractor_unavailable`, `extractor_invalid_json` или `extractor_invalid_response`).

## Пароли

Пароль создаваемого пользователя записывается provisioning-сервисом в защищённое поле IntraService `Field1489`; логин — в `Field1488`. Пароль не возвращается в CaseFrame, ActionPlan, command result, feedback, комментарии или логи.

## Изоляция данных

Таблицы IntraLink находятся в PostgreSQL database `intraservice`. LiteLLM использует
отдельную database `litellm` в том же экземпляре PostgreSQL: Prisma-миграции LiteLLM
не имеют доступа к схеме IntraLink и не могут изменить или удалить её таблицы.

## Проверка новой версии

Версионированные наборы находятся в `datasets/automation/<version>`: intake snapshot → CaseDecision и подтверждённые факты → ActionPlan/disposition.

```powershell
.venv\Scripts\python.exe -m core.automation.replay --dataset-root datasets/automation/v1 --min-accuracy 1.0
```

После успешной приёмки новая версия атомарно заменяет активную. Параллельная эксплуатация старого и нового движков не предусмотрена.
