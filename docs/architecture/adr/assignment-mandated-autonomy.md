# ADR: assignment-mandated autonomy

Статус: accepted, 2026-09-30.

## Решение

Назначение сервисной учётной записи в `ExecutorIds` при `TaskStatus.IsFinal=false` является единственным business admission trigger автономного решения. На один переход назначения создаётся один durable `AutonomyRun`; повторные polling, `Changed` и комментарии не создают новый run.

Режимы `ASSISTED/FULL_AUTO`, операторское approval, capability policies, `auto_execution_eligible` и ручные deployment kill switches не участвуют в новом runtime. Legacy authorization records остаются только read-only compatibility binding для команд, созданных до cutover, и удаляются отдельной миграцией после подтверждённого дренирования.

## Инварианты исполнения

Непосредственно перед side effect worker обязан подтвердить свежую заявку, присутствие сервисной учётной записи, `IsFinal=false`, assignment/snapshot/plan/action/params hashes, свежий успешный preflight, активный service binding, single-use permit, idempotency, lease, circuit breaker и отсутствие опасного предыдущего результата. `IsFixed` не является признаком конечности.

`create_ad_user` дополнительно требует исходный service, task type, form fields, delivery policy и запрет повтора после `succeeded`, `partial` или `unknown_outcome`.

## Ошибки и уточнения

Недостающие факты приводят к одному детерминированному публичному вопросу на round; максимум два round. Run возобновляет только новый публичный человеческий event. LLM возвращает только grounded facts с буквальным source span и не может выбирать capability, retry или менять permit.

Transient failure до мутации допускает три retry: 30 секунд, 2 минуты и 10 минут. После consumption permit неопределённый результат и потеря lease никогда не повторяются автоматически.

Terminal failure публикуется через durable private-comment outbox. Текст детерминирован, санитизирован и не меняет status/executors. Удаление бота либо переход в конечный статус отменяет run без комментария.

## Rollout

Первый production poll записывает текущие назначения как baseline. Уже назначенная заявка запускается только после снятия и повторного назначения бота. Старые команды завершаются compatibility path; удаление legacy policy/approval tables выполняется лишь после проверки отсутствия недренированных команд.
