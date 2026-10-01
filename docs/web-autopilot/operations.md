# Эксплуатация движка автоматизации ADR 0006

## Режим работы

В runtime нет режимов `ASSISTED/FULL_AUTO` и операторского approval. Назначение сервисной учётной записи исполнителем незавершённой заявки является мандатом на один durable `AutonomyRun`.

Штатный поток:

1. Оператор назначает сервисную учётную запись исполнителем заявки.
2. Poller подтверждает `TaskStatus.IsFinal=false`, фиксирует assignment epoch и создаёт run.
3. Движок анализирует заявку; при нехватке данных задаёт один детерминированный публичный вопрос.
4. После plan и preflight создаётся короткоживущий `ExecutionPermit`.
5. Worker повторно читает заявку, проверяет bot assignment, `IsFinal`, hashes, binding, permit, lease и предыдущие результаты, затем исполняет capability.
6. Для ручного перехвата оператор снимает сервисную учётную запись из `ExecutorIds`.

## Состояния, требующие внимания

- `ambiguous`, `unknown`, `degraded`: run завершается безопасным blocker и private-заметкой без смены статуса.
- `awaiting_facts`: требуется получить перечисленные факты; допускается не более двух циклов уточнения.
- `awaiting_diagnostics`: причина ещё не доказана, техническая команда не сформирована.
- `unknown_outcome`: результат мутации нельзя подтвердить; автоматический retry запрещён.
- `retry_wait`: безопасный pre-mutation retry ожидает 30 секунд, 2 или 10 минут.
- `printer_executor_unavailable`: установка принтера недоступна и не должна публиковать фиктивный успех.

## Пароли и секреты

Для создания пользователя provisioning-сервис записывает логин в `Field1488`, пароль в защищённое поле `Field1489`. Пароль не попадает в модель, публичные или скрытые комментарии, CaseFrame, планы, feedback, результаты команд и журналы.

## Offline-приёмка

Перед атомарной заменой версии профилей, extraction или workflow выполните:

```powershell
.venv\Scripts\python.exe -m core.automation.replay --dataset-root datasets/automation/v1 --min-accuracy 1.0
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check .
cd web
npm run build
```

Replay не обращается к IntraService, LDAP, WinRM или LLM и не является shadow-режимом. Новая версия активируется только после закрытой offline-приёмки.

## Ошибка исполнения

При terminal failure `AutonomyFailureReporter` ставит в outbox одну private-заметку вида `[AUTO:<run_id>] ... Статус заявки не изменён.` Публикация выполняется без `StatusId` и `ExecutorIds`, с backoff и проверкой lifetime от дубликатов. Автоматический повтор потенциально необратимой capability запрещён, если исход предыдущей попытки неизвестен.
