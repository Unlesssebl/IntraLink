# Действующий движок автоматизации заявок

Актуальная runtime-архитектура IntraLink реализует ADR 0006 и использует единственную цепочку:

```text
TicketSnapshot → TargetServiceResolution → CaseFrame → CaseDecision → WorkflowPlan
                                              ↓
                                ServiceCompatibilityDecision
                                  ├─ compatible → ActionPlan → ExecutionPermit → ActionCommand
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
- `AutonomyRun` — durable-жизненный цикл одного assignment epoch сервисной учётной записи.
- `ExecutionPermit` — короткоживущая техническая привязка run/action/preflight к plan/action/snapshot/params hashes.
- `ActionCommand` — ровно одна capability, создаваемая по permit; старые authorization records читаются только при дренировании legacy-команд.
- `RedirectPlan` — самостоятельный approval-bound план перенаправления; он не является CaseType, disposition или capability.

Intake никогда не выбирает `reset_print_spooler`, `disable_ad_user` или другую техническую команду. Сначала определяется целевой сервис, затем смысл обращения; техническое действие появляется только после readiness и диагностики внутри workflow. Отсутствие зарегистрированного workflow даёт явный `unsupported_workflow` с ручным disposition, не стирая найденный сервис и CaseType.

## Мандат на автономное решение

Единственный бизнес-триггер — переход `bot_absent → bot_present` в `ExecutorIds`, когда каталог IntraService подтверждает `TaskStatus.IsFinal=false`. Poller транзакционно увеличивает assignment epoch и создаёт ровно один `AutonomyRun`; `Changed`, повторный poll, рестарт и скрытые комментарии не создают новый run. На первом production-проходе существующие назначения записываются как baseline без запуска.

Операторского approval, режимов `ASSISTED/FULL_AUTO`, capability policy и ручного kill switch в новом пути нет. Ручной перехват — только снятие сервисной учётной записи с заявки. Коррекции фактов сохраняются, инвалидируют старый plan/permit и продолжают тот же run.

При нехватке фактов движок публикует детерминированный публичный вопрос из `WorkflowFactSpec`. Допустимы два clarification round. Возобновляют run только новые публичные человеческие события; private, system и bot-authored события игнорируются. LLM извлекает только grounded-факты с буквальным `text_span` и не выбирает capability, retry либо безопасность мутации.

## Защитные проверки исполнения

Перед побочным эффектом worker заново читает заявку и проверяет наличие бота, `TaskStatus.IsFinal=false`, неизменность snapshot, точное соответствие assignment/plan/action/capability/params hashes, свежий `passed|not_applicable` preflight, service binding, single-use permit, idempotency, circuit breaker и владение распределённой lease. `IsFixed` не определяет конечность: статус 29 «Выполнена» остаётся незавершённым. Неизвестный либо каталог статусов старше 15 минут блокирует действие и переводит run в `retry_wait`.

Для `create_ad_user` дополнительно заново проверяются source service, task type, активный catalog hash, binding key/version, обязательные поля формы, delivery policy и отсутствие `succeeded`, `partial` либо `unknown_outcome` предыдущего создания.

Каталог синхронизируется read-only через `POST /api/v2/autopilot/service-catalog/sync`. Пустой или недоступный ответ не активирует новую версию. Binding `ad_account_creation` создаётся неактивным и становится исполнимым только после подтверждения фактического AD-сервиса по текущему каталогу.

Следующая capability публикуется только после подтверждённого успеха предыдущей. Transient-сбои до мутации повторяются через 30 секунд, 2 и 10 минут. После входа в мутацию permit помечается использованным; потеря lease или неопределённый ответ становятся `unknown_outcome` и никогда автоматически не повторяются.

Terminal failure создаёт одну санитизированную private-заметку через durable outbox и не меняет статус или исполнителей заявки. Перед повторной доставкой outbox проверяет lifetime по детерминированному тексту, поэтому timeout после фактической публикации не создаёт дубликат. Удаление бота и переход в `IsFinal=true` отменяют run без комментария.

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
