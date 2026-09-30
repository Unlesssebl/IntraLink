# 📋 Микроплан Milestone 3: Интеграция в сценарный оркестратор и Runner

## 🎯 Цель этапа
Интегрировать `AutopilotReporter` в жизненный цикл выполнения сценариев автопилота (`TicketRunRunner` и `TicketRunOrchestrator`), обеспечив автоматическую, идемпотентную и отказоустойчивую отправку скрытых служебных комментариев (`is_private=True`) в IntraService с соблюдением каскада настроек и барьеров безопасности (`shadow`/`canary`).

---

## 🧱 Задачи Milestone 3

### 1. Каскад разрешения настроек (`resolve_internal_comments_config`)
* **Расположение:** `core-api/app/services/ticket_runs.py` / `core-api/app/services/autopilot_reporter.py`
* **Логика разрешения:**
  ```python
  def resolve_internal_comments_config(
      global_setting: AutopilotSetting,
      scenario: AutopilotScenario | None,
  ) -> tuple[bool, str]:
      scenario_cfg = (scenario.config_json or {}) if scenario else {}
      enabled = scenario_cfg.get("internal_comments_enabled")
      if enabled is None:
          enabled = getattr(global_setting, "internal_comments_enabled", True)

      depth = scenario_cfg.get("internal_comments_depth")
      if depth is None:
          depth = getattr(global_setting, "internal_comments_depth", "applied")

      return bool(enabled), str(depth)
  ```

### 2. Защитные барьеры (Guard Rails)
* **Shadow Mode Guard:** Если сценарий работает в режиме `shadow`, любые служебные комментарии в IntraService **категорически блокируются**.
* **Canary Mode Guard:** Если сценарий в режиме `canary`, отправка комментариев разрешена **только если тикет отобран в канареечный бакет** (`canary_selected(task_id, canary_percent)`).
* **Fault Tolerance Guard:** Любой вызов `add_task_comment` оборачивается в блок `try/except Exception`, чтобы сбои сети IntraService не блокировали выполнение шагов сценария и не откатывали транзакцию БД.

### 3. Точки триггеров в оркестраторе (`scenario_orchestrator.py`)
* **Триггер `on_start` (Старт обработки):**
  - Точка вызова: первый запуск `advance` для активного `TicketRun` (когда события `internal_comment:on_start` еще нет в `TicketRunEvent`).
  - Форматирование через `AutopilotReporter.format_start_comment`.
  - Отправка через `add_task_comment(..., is_private=True)`.
  - Запись события `TicketRunEvent(event_type="internal_comment_sent", event_key="internal_comment:on_start", ...)`.
* **Триггер `on_pause_or_error` (Остановка / Сбой):**
  - Точка вызова: переход `run.state` в `PAUSED` или `SYSTEM_ERROR` (нехватка данных, ошибка выполнения команды, ожидание ручной проверки).
  - Форматирование через `AutopilotReporter.format_pause_comment`.
  - Отправка и запись события `TicketRunEvent(event_type="internal_comment_sent", event_key="internal_comment:on_pause:...", ...)`.
* **Триггер `on_complete` (Успешное завершение):**
  - Точка вызова: переход `run.state` в `COMPLETED` при верифицированном исполнении задачи.
  - Форматирование через `AutopilotReporter.format_complete_comment`.
  - Отправка и запись события `TicketRunEvent(event_type="internal_comment_sent", event_key="internal_comment:on_complete:...", ...)`.

### 4. Идемпотентность и защита от повторной отправки
* Проверка наличия `TicketRunEvent` по уникальному ключу `event_key`:
  - `internal_comment:on_start:{run_id}`
  - `internal_comment:on_pause:{run_id}:{pause_reason}`
  - `internal_comment:on_complete:{run_id}`
* Исключает повторные комментарии при частых опросах пуллера или повторных тиках оркестратора.

### 5. Комплексные интеграционные тесты (`core-api/tests/test_autopilot_internal_comments_integration.py`)
* Тест 1: Успешная отправка комментариев `on_start`, `on_pause_or_error`, `on_complete` в мокированный IntraService.
* Тест 2: Проверка блокировки отправки комментариев в режиме `shadow`.
* Тест 3: Проверка селективности в режиме `canary` (отправка только для выбранного бакета).
* Тест 4: Проверка каскада настроек (переопределение `depth="technical"` в конфигурации сценария при глобальном `depth="applied"`).
* Тест 5: Идемпотентность (повторный `advance` не дублирует комментарий).
* Тест 6: Отказоустойчивость при сетевой ошибке IntraService API (run продолжает выполняться).

---

## 🔍 Критерии приёмки Milestone 3
1. Все три триггера корректно встроены в жизненный цикл `TicketRunOrchestrator`.
2. Тесты интеграции и регрессионный сьют проходят на 100% против тестового PostgreSQL.
3. Код закоммичен в Git с чистой историей.
