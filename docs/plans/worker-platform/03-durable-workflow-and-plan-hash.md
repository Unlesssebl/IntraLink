# Инкремент 3: Durable Workflow & Approval с plan_hash

Статус: запланирован  
Зависимости: [Инкремент 1](01-contract-and-core-safety.md), [Инкремент 2](02-worker-sdk-and-runner.md)  
Целевые компоненты: `core-api/app/services/command_service.py`, `core-api/app/database/db.py`, `core-api/app/routers/commands_v2.py`

---

## 1. Цель инкремента

Разделить ответственность между Core API (workflow заявки, approvals, durable-фазы) и воркером (инфраструктурное действие). Реализовать связывание подтверждения оператора с неизменяемым снимком плана (`plan_hash`), preflight evidence и сроком годности, исключив исполнение устаревших или подмененных команд.

---

## 2. Задачи к реализации

### 2.1. Формирование Execution Plan и `plan_hash`
- При создании команды или прохождении read-only preflight формируется неизменяемый снимок плана:
  ```json
  {
    "action": "install_printer",
    "version": 1,
    "target": {"pc_name": "PC-001"},
    "parameters": {"printer_name": "HP-LJ-M428", "port_ip": "192.168.1.50"},
    "preflight_evidence": {
      "host_online": true,
      "spooler_running": true,
      "tcp_9100_open": true,
      "driver_matched": "HP Universal Printing PCL 6",
      "driver_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "winrm_bootstrap_required": true
    },
    "expires_at": "2026-09-07T12:00:00Z"
  }
  ```
- `plan_hash = sha256(canonical_json(execution_plan))`.

### 2.2. Привязка `CommandApproval` к `plan_hash`
- В таблице `command_approvals` фиксируются:
  - `command_id`
  - `command_version`
  - `plan_hash`
  - `operator` / `approver_principal_id`
  - `expires_at` (срок годности результатов preflight)
- При вызове `POST /api/v2/commands/{id}/approval`:
  1. Core API сверяет текущую версию команды с переданной.
  2. Сверяет `plan_hash`.
  3. Проверяет, что срок годности `expires_at` не истек.
  4. При любом расхождении (изменился IP, сменился профиль драйвера, истек тайм-аут) approval отклоняется с ошибкой `409 Conflict: plan_drift_detected`.

### 2.3. Durable-фиксация фаз в PostgreSQL
- В `command_events` и таблице команды фиксируются этапы:
  - `preflight_passed` (со снимком evidence)
  - `plan_frozen` (`plan_hash`)
  - `approved`
  - `prepare_started` / `prepare_completed`
  - `execute_started`
  - `verified` (с подтвержденными очередью, портом, драйвером)
  - `cleanup_completed` / `cleanup_failed`
- Финализация тикета в Helpdesk разрешается только при наличии durable события `verified`.

---

## 3. Критерии приемки (Definition of Done)

1. Тест: изменение любого параметра команды после preflight приводит к инвалидации `plan_hash` и отказу в approval.
2. Тест: попытка утвердить команду с истекшим сроком годности preflight возвращает 409 с требованием повторить диагностику.
3. Тест: финализация команды без доказательства `verified` аппаратно блокируется Core API.
