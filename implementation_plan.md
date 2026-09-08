# План запуска и настройки автоматического исполнения заявок (Автопилот и Execution Worker)

Диагностика показала, что заявка [#140628](https://servicedesk-pub.corporate.loc/Task/View/140628) успешно назначена на сервисную учетную запись `alen_assistant` (User ID `10502`), однако автоматическое исполнение не запустилось из-за комбинации системных факторов:
1. **Баг фильтрации в Poller Daemon:** опрос API IntraService использует ключ `"ExecutorId"` вместо `"ExecutorIds"`.
2. **Отключен Автопилот:** глобальный флаг `autopilot_settings.enabled` равен `False`.
3. **Отсутствует привязка сценария:** для сервиса `53` (*Создание нового пользователя сети*) не добавлен сценарий `create_user`.
4. **Остановлен `execution-worker`:** Windows-агент для Active Directory / PowerShell не запущен на хосте.
5. **Неполнота данных в тикете #140628:** отсутствуют обязательные реквизиты (ФИО, должность, отдел).
6. **Отсутствие уведомления об ошибках в заявке (Пользовательское требование):** при возникновении сбоев оркестратор ставил `TicketRun` на паузу без публикации комментария заявителю/оператору в IntraService.

---

## User Review Required

> [!IMPORTANT]
> **1. Формат и видимость комментария об ошибке:**
> При сбое автоматического исполнения (например, ошибка домена AD, недоступность DC, некорректный синтаксис):
> - Комментарий публикуется в IntraService от лица сервисного аккаунта `alen_assistant`.
> - Шаблон: *«При автоматической обработке заявки произошла ошибка: {error_details}. Заявка передана на ручную обработку дежурному инженеру.»*
> - Видимость: публичный комментарий (виден заявителю и инженерам) либо приватный (только для инженеров)? Рекомендуется **публичный**, чтобы заявитель понимал статус своего обращения.
> 
> **2. Режим Автопилота и подтверждение (HITL):**
> При создании учетных записей в Active Directory сценарий `create_user` по умолчанию имеет `risk_level: 2`. При включенном Автопилоте для сервиса 53 система может работать:
> - **Автономно (Unattended):** проверяет реквизиты, при их наличии сразу создает УЗ и закрывает заявку; при нехватке — переводит в статус 35 («Требует уточнения»); при ошибке выполнения — оставляет комментарий об ошибке.
> - **С подтверждением оператора:** формирует карточку в Web-панели и ждет клика.

---

## Proposed Changes

### Core API & Scenario Orchestrator

#### [MODIFY] [worker.py](file:///c:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/core-api/app/services/worker.py)
- В функции `process_autonomous_lifecycle` заменить параметр запроса к IntraService API:
  ```diff
  - "ExecutorId": assistant_user_id,
  + "ExecutorIds": str(assistant_user_id),
  ```
  Это гарантирует получение всех задач, где сервисная учетная запись назначена основным или дополнительным исполнителем.

#### [MODIFY] [scenario_orchestrator.py](file:///c:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/core-api/app/services/scenario_orchestrator.py)
- В методе `_reconcile_command`:
  При переходе команды в статус `failed`, `rejected` или при возникновении `resolution_error`:
  - Извлекать точный текст ошибки из `command.error_message`.
  - Публиковать комментарий в заявку IntraService через `intraservice.add_comment` (или `update_task_full`) с фиксацией текста сбоя.
  - Сохранять событие `command_failure_commented` в `ticket_run_events`.
  - Переводить заявку в статус 35 («Требует уточнения») или оставлять в статусе 27 («В работе») с пометкой для инженера.

#### [MODIFY] [command_delivery.py](file:///c:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/core-api/app/services/command_delivery.py)
- Добавить метод `deliver_failure(command_id, error_message, actor)`:
  - Форматирует понятное сообщение об ошибке для заявки IntraService.
  - Обеспечивает идемпотентность (не дублирует комментарий об ошибке повторно).

---

### Configuration & Database State

#### [EXECUTE] Активация сценария и глобального Автопилота в PostgreSQL
- Создать запись в таблице `autopilot_scenarios` для сервиса `53`:
  - `service_id = 53`
  - `scenario_key = "create_user"`
  - `enabled = True`
  - `rollout_mode = "active"`
  - `config_json = {"require_approval": false, "auto_deliver": true}`
- Включить глобальный флаг в `autopilot_settings`:
  - `enabled = True`
  - `updated_by = "belikov.a"`

---

### Execution Worker (Windows Host)

#### [START] Запуск `execution-worker` на Windows
- Настроить подключение к Redis через проброшенный порт (`REDIS_URL=redis://localhost:6380/0`).
- Запустить процесс воркера:
  ```powershell
  $env:REDIS_URL="redis://localhost:6380/0"
  $env:PYTHONPATH="."
  uv run python execution-worker/worker.py
  ```
- Убедиться, что зарегистрированы обработчики: `CreateUserHandler`, `InstallPrinterHandler`, `GrantWlanHandler`.

---

### IntraService Ticket #140628

#### [UPDATE] Тестирование двух веток жизненного цикла:
1. **Ветка недостающих реквизитов (текущее состояние #140628):**
   - Запустить обработку текущего тикета (где нет ФИО).
   - Убедиться, что агент **не падает молча**, а оставляет комментарий с запросом данных и переводит в статус 35.
2. **Ветка успешного исполнения / ошибки исполнения:**
   - При вводе реквизитов убедиться в генерации команды.
   - В случае ошибки (например, симуляция недоступности AD) проверить, что в заявку немедленно пишется комментарий об ошибке.

---

## Verification Plan

### Automated Tests
1. **Проверка фильтра poller:**
   Вызов `get_tasks` с `ExecutorIds=10502` внутри `intralink_core_api` должен находить заявку #140628.
2. **Тест доставки ошибки в тикет:**
   Юнит-тест / интеграционный тест вызова `deliver_failure` при падении команды в `scenario_orchestrator`.

### Manual Verification
1. **Сквозная проверка тикета #140628:**
   - Наблюдение за логами `intralink_poller` (`docker logs -f intralink_poller`).
   - Проверка появления комментария в тикете IntraService при нехватке реквизитов или сбое.
   - Проверка успешного завершения заявки после заполнения ФИО.
