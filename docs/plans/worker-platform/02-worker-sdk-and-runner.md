# Инкремент 2: Worker SDK и доверенный PowerShell Runner

Статус: запланирован  
Зависимости: [Инкремент 1](01-contract-and-core-safety.md)  
Целевые компоненты: `execution-worker/sdk/`, `execution-worker/core_api_client.py`

---

## 1. Цель инкремента

Создать модульный Worker SDK с типизированным контрактом жизненного цикла (7 фаз), реестром обработчиков умений (`HandlerRegistry`), двухконтурным доверенным PowerShell Runner (stdin/tempfile + `-ArgumentList`) и фоновым `LeaseRenewer` с механизмом best-effort cooperative cancellation.

---

## 2. Задачи к реализации

### 2.1. Модели данных SDK (`sdk/models.py`)
- `RiskClass(str, Enum)`: `SAFE_RETRY`, `RECONCILE_BEFORE_RETRY`, `NEVER_AUTO_RETRY`.
- `ExecutionPhase(str, Enum)`: `VALIDATE`, `PREFLIGHT`, `PREPARE`, `EXECUTE`, `VERIFY`, `RECONCILE`, `CLEANUP`.
- `HandlerContext`:
  - `command_id: str`, `task_id: int`, `node_id: str`
  - `redis: Redis`, `api_client: CoreApiClient`
  - `cancellation_token: asyncio.Event`
  - `log: list[str]`
  - `evidence: dict[str, Any]`
- `ActionResult`: типизированный результат (`success`, `message`, `error`, `log`, `payload`, `failure_kind`, `failure_code`, `verified_failure`).

### 2.2. Базовый класс `ActionHandler` (`sdk/base.py`)
Интерфейс с обязательными фазами:
1. `validate(ctx, params: TInput) -> tuple[bool, str]` — строгая валидация Pydantic-модели и нормализованных сущностей.
2. `preflight(ctx, params: TInput) -> tuple[bool, str, dict[str, Any]]` — **строго read-only** проверка хоста, служб, портов. Возвращает `preflight evidence` для формирования `plan_hash`.
3. `prepare(ctx, params: TInput) -> tuple[bool, str]` — подготовительные действия после approval (например, WMI bootstrap WinRM, подготовка staging-каталога).
4. `execute(ctx, params: TInput) -> ActionResult` — непосредственное выполнение целевого действия.
5. `verify(ctx, params: TInput, result: ActionResult) -> tuple[bool, str, bool, str | None]` — проверка примененного состояния по точному совпадению.
6. `reconcile(ctx, params: TInput) -> tuple[bool, str]` — проверка фактического состояния перед возможным повтором.
7. `cleanup(ctx, params: TInput) -> None` — гарантированная зачистка временных файлов и откат измененного состояния служб (всегда в `finally`).

### 2.3. Реестр обработчиков (`sdk/registry.py`)
- `HandlerRegistry`:
  - `register(handler: ActionHandler)`
  - `get(action_id: str) -> ActionHandler | None`
  - `list_capabilities() -> set[str]`
  - `list_actions() -> dict[str, str]` (action -> version)

### 2.4. Двухконтурный безопасный раннер PowerShell (`sdk/powershell.py`)
Исключение строковой интерполяции на обеих границах:
1. **Контур 1 (Python -> Local Runner):**
   - Запуск `powershell.exe -NoProfile -NonInteractive -EncodedCommand <FixedRunnerBase64>`.
   - Входные параметры передаются через стандартный ввод `stdin` или временный файл с правами только текущего процесса (`0600` / restricted ACL). Командная строка параметров не содержит.
2. **Контур 2 (Local Runner -> Remote WinRM):**
   - Runner выполняет `Invoke-Command -ComputerName $Target -ScriptBlock $FixedScript -ArgumentList @($Param1, $Param2)`.
   - Текст `ScriptBlock` фиксирован и параметризован через `param(...)`. Никаких f-строк.
3. **Cancellation Support:**
   - Мониторинг `ctx.cancellation_token`. При активации — немедленный `process.kill()` локального процесса.

### 2.5. Фоновый `LeaseRenewer` (`sdk/lease_renewer.py`)
- Асинхронная корутина во время выполнения фаз `prepare`, `execute`, `verify`:
  - Каждые 10-15 секунд вызывает `CoreApiClient.renew_command_lease_v2()`.
  - Продлевает `lock:host:<pc_name>` в Redis через атомарный compare-and-expire Lua-скрипт.
  - При ошибке 409 или потере блокировки активирует `cancellation_token`.

---

## 3. Критерии приемки (Definition of Done)

1. Полный контрактный тестовый набор (`core-api/tests/test_worker_sdk.py`), проверяющий соблюдение всех 7 фаз.
2. Тест раннера: попытка передачи зловредных строк (`; Remove-Item`, `$(whoami)`, непарные кавычки) передается строго как буквальные строковые данные без выполнения команд.
3. Тест отмены: при выставлении `cancellation_token.set()` локальный PowerShell-процесс принудительно завершается.
