# Инкремент 1: Контракт и Core API Safety

Статус: выполнен  
Зависимости: отсутствуют  
Целевые компоненты: `docs/adr/0002-modular-worker-platform.md`, `core-api/app/services/command_outbox.py`, `core-api/app/services/actions/policy.py`, `core-api/app/routers/commands_v2.py`, `core-api/app/services/command_service.py`

---

## 1. Цель инкремента

Зафиксировать обновленный архитектурный контракт [ADR 0002](../../adr/0002-modular-worker-platform.md) со всеми инвариантами и устранить критические риски безопасности в Core API до внесения изменений в исполняющий воркер:
- Исключить ошибочный перезапуск mutating-команд при истечении lease.
- Заблокировать режим `AUTO` для `install_printer` на период пилота.
- Добавить серверный эндпоинт продления lease команды с возвратом относительного `lease_ttl_seconds`.

---

## 2. Задачи к реализации

### 2.1. Актуализация ADR 0002
- Файл: [`docs/adr/0002-modular-worker-platform.md`](../../adr/0002-modular-worker-platform.md).
- Синхронизировать с обновленными инвариантами:
  - 7 фаз жизненного цикла (`validate`, read-only `preflight`, `prepare`, `execute`, `verify`, `reconcile`, `cleanup`).
  - Привязка approval к `command_version`, `plan_hash` и `expires_at`.
  - Best-effort cooperative cancellation при потере lease/lock.
  - Двухконтурная безопасность PowerShell (локальный runner через stdin/tempfile + удаленный `-ArgumentList`).
  - Перенос WMI bootstrap WinRM строго в фазу `prepare` после approval.
  - Идемпотентная routing quarantine в Core API вместо client-side `XADD+XACK`.

### 2.2. Исправление политики повторов в `command_outbox.py`
- Файл: `core-api/app/services/command_outbox.py`.
- **Проблема:** Строка 96 использует `command.action in AUTO_ELIGIBLE_ACTIONS`, что позволяет автоматически перезапускать изменяющие команды при истечении lease.
- **Решение:** Заменить на `command.action in AUTO_RETRY_ELIGIBLE_ACTIONS`. Если действие не входит в `AUTO_RETRY_ELIGIBLE_ACTIONS`, команда переходит строго в `needs_review` без повторной отправки в Outbox.

### 2.3. Политика автономности и защита от неконтролируемых повторов в `policy.py`
- Файл: `core-api/app/services/actions/policy.py`.
- **Режим исполнения AUTO:** Администратор системы имеет право переводить любые зарегистрированные действия (`create_user`, `install_printer`, `grant_wlan`, `apply_triage`, `diagnose_host`, `rag_sync`) в автономный режим `AUTO` через Skills Hub (`PATCH /api/v1/skills/{id}/policy`).
- **Защита от повторов (Safe Retry Boundary):** Механизм автоматических ретраев при истечении lease в Outbox строго ограничен списком `AUTO_RETRY_ELIGIBLE_ACTIONS = frozenset({"diagnose_host", "rag_sync"})`. Мутирующие действия при сбое исполнения или потере связи никогда не перезапускаются автоматически и переводятся строго в `needs_review`.

### 2.4. Эндпоинт продления lease в `commands_v2.py`
- Файлы: `core-api/app/routers/commands_v2.py`, `core-api/app/services/command_service.py`.
- Добавить эндпоинт:
  ```http
  POST /api/v2/commands/{command_id}/heartbeat
  X-Service-Key-Id: ...
  X-Service-Secret: ...
  Content-Type: application/json

  {
    "worker_id": "win-node-01",
    "claim_token": "<token>",
    "lease_seconds": 120
  }
  ```
- Метод `CommandService.renew_lease`:
  1. Проверяет статус `running` и совпадение хеша `claim_token`.
  2. Если команда в терминальном статусе или токен не совпадает — `409 Conflict`.
  3. Продлевает `command.lease_expires_at = now + timedelta(seconds=lease_seconds)`.
  4. Возвращает `lease_ttl_seconds = lease_seconds` (относительный TTL для защиты от рассинхронизации часов).

---

## 3. Критерии приемки (Definition of Done)

1. ADR 0002 актуализирован и полностью согласован с инвариантами монорепозитория.
2. Тест: при истечении lease команды `install_printer` статус переходит в `needs_review`, повторный Outbox-запись не создается.
3. Тест: при истечении lease команды `diagnose_host` команда корректно перезапускается через задержку 5/30 секунд.
4. Тест: `POST /api/v2/commands/{id}/heartbeat` продлевает lease при валидном `claim_token` и возвращает 409 при чужом токене или истекшем lease.
5. Все тесты `core-api/tests/test_commands_v2.py` проходят успешно.
