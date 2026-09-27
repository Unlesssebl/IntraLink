# Действующий движок автоматизации заявок

Актуальная runtime-архитектура IntraLink реализует ADR 0006 и использует единственную цепочку:

```text
TicketSnapshot → CaseFrame → CaseDecision → WorkflowPlan → ActionPlan → ActionCommand
```

## Границы ответственности

- `TicketSnapshot` — неизменяемый санитизированный снимок заявки. Закрытые комментарии, содержимое вложений и секретные поля в него не входят.
- `CaseFrame` — утверждения, отрицания, сущности и буквальные ссылки на исходный текст.
- `CaseDecision` — только тип обращения: `selected`, `multi_intent`, `ambiguous`, `unknown` или `degraded`.
- `WorkflowPlan` — бизнес-процесс первой линии, недостающие факты, диагностика и disposition.
- `ActionPlan` — упорядоченный список разрешённых capabilities с каноническим hash.
- `ActionCommand` — ровно одна capability, связанная с plan/action/snapshot/params hashes.

Intake никогда не выбирает `reset_print_spooler`, `disable_ad_user` или другую техническую команду. Сначала определяется смысл обращения; техническое действие появляется только после readiness и диагностики внутри workflow.

## Роль оператора

Текущий cutover полностью полуавтоматический:

1. Оператор явно запускает анализ заявки.
2. Проверяет CaseFrame, CaseDecision, workflow, недостающие факты и ActionPlan.
3. При необходимости отдельно исправляет тип обращения либо параметры ActionPlan.
4. Назначает сервисную учётную запись исполнителем заявки.
5. Подтверждает конкретный ActionPlan.
6. Worker повторяет только технические guards и исполняет capability.

Poller не запускает анализ или инфраструктурную мутацию. Назначение сервисной учётной записи само по себе ничего не исполняет. `FULL_AUTO`, shadow, canary, dual-write и legacy fallback в действующем runtime отсутствуют.

## Защитные проверки исполнения

Перед побочным эффектом worker проверяет неизменность snapshot, точное соответствие plan/action/capability/params hashes, свежий успешный preflight, назначение сервисной учётной записи, нетерминальный статус заявки, отсутствие terminal feedback и владение распределённой lease.

Следующая capability публикуется только после подтверждённого успеха предыдущей. `failed` и `unknown_outcome` переводят план в `needs_review`; заявка не закрывается.

## LLM

Сначала выполняется детерминированный intake. Если service/lexical evidence уже даёт прямое решение, LLM не вызывается. Только для `unknown` и серой зоны LLM получает санитизированный публичный текст, возвращает grounded assertions из закрытого словаря, после чего evidence пересчитывается и максимум три существующих кандидата передаются verifier. LLM не создаёт case type или capability, не разрешает выполнение, не подтверждает результат и не закрывает заявку.

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
