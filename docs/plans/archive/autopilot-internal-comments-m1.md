# 🎯 Микроплан реализации: Milestone 1 — Модель данных, схема и миграции

**Родительский план:** [`docs/plans/autopilot-internal-comments-plan.md`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/docs/plans/autopilot-internal-comments-plan.md)  
**Область:** `core-api` (SQLAlchemy, Alembic, Pydantic, TicketRunService)  
**Статус:** Готов к реализации  

---

## 1. Цель этапа

Создать надёжный фундамент хранения конфигурации скрытых служебных комментариев автопилота:
1. Добавить глобальные параметры по умолчанию в таблицу `autopilot_settings`.
2. Обеспечить поддержку сценарных переопределений в JSON-конфигурации `AutopilotScenario.config_json`.
3. Обеспечить строгую Pydantic-валидацию и бесшовную миграцию существующей базы данных.

---

## 2. Задачи этапа (Work Items)

### Task 1.1: Модификация модели SQLAlchemy (`AutopilotSetting`)
* **Файл:** [`core-api/app/database/db.py`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/core-api/app/database/db.py)
* **Действие:**
  * В модель `AutopilotSetting` добавить поля:
    ```python
    internal_comments_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    internal_comments_depth: Mapped[str] = mapped_column(
        String(16), nullable=False, default="applied", server_default="applied"
    )
    ```
  * В `__table_args__` добавить ограничение целостности:
    ```python
    CheckConstraint(
        "internal_comments_depth IN ('applied', 'technical')",
        name="ck_autopilot_settings_comments_depth"
    )
    ```

---

### Task 1.2: Создание Alembic-миграции
* **Файл:** `core-api/migrations/versions/20260923_0014_autopilot_internal_comments_settings.py`
* **Действие:**
  * `revises = "20260910_0013"`
  * `upgrade()`:
    * `op.add_column('autopilot_settings', sa.Column('internal_comments_enabled', sa.Boolean(), server_default='true', nullable=False))`
    * `op.add_column('autopilot_settings', sa.Column('internal_comments_depth', sa.String(length=16), server_default='applied', nullable=False))`
    * `op.create_check_constraint('ck_autopilot_settings_comments_depth', 'autopilot_settings', "internal_comments_depth IN ('applied', 'technical')")`
  * `downgrade()`:
    * Удаление ограничения и колонок.

---

### Task 1.3: Расширение Pydantic-схем и эндпоинтов API
* **Файл:** [`core-api/app/routers/ticket_runs.py`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/core-api/app/routers/ticket_runs.py)
* **Действие:**
  * Обновить `AutopilotSettingRequest`:
    ```python
    class AutopilotSettingRequest(BaseModel):
        enabled: bool
        expected_version: int = Field(ge=1)
        reason: str = Field(min_length=3, max_length=500)
        internal_comments_enabled: bool | None = None
        internal_comments_depth: Literal["applied", "technical"] | None = None
    ```
  * Вспомогательная функция `serialize_settings`:
    * Добавить поля `"internal_comments_enabled": setting.internal_comments_enabled` и `"internal_comments_depth": setting.internal_comments_depth`.
  * Валидация в `AutopilotScenarioRequest`:
    * Добавить проверку словаря `config`: если переданы ключи `internal_comments_enabled` (должен быть `bool`) и `internal_comments_depth` (строго `"applied"` или `"technical"`).

---

### Task 1.4: Обновление бизнес-логики в `TicketRunService`
* **Файл:** [`core-api/app/services/ticket_runs.py`](file:///C:/Users/belikov.a/Desktop/Акты,%20документы/Work/!Projects/intralink/core-api/app/services/ticket_runs.py)
* **Действие:**
  * В методе `update_global_setting`:
    * Принимать опциональные аргументы `internal_comments_enabled: bool | None` и `internal_comments_depth: str | None`.
    * Сохранять новые значения в `AutopilotSetting` и увеличивать версию записи.
  * В методе `upsert_scenario`:
    * Гарантировать сохранение и передачу переопределений в `config_json`.

---

### Task 1.5: Модульные тесты для M1
* **Файл:** `core-api/tests/test_autopilot_internal_comments_schema.py`
* **Действие:**
  * Тест дефолтных значений при инициализации `AutopilotSetting`.
  * Тест обновления глобальных настроек (включение/выключение, смена глубины на `technical`).
  * Тест сохранения и чтения переопределений в `AutopilotScenario.config_json`.
  * Тест валидации: отказ при передаче некорректного значения глубины (например, `"verbose"` или `"debug"`).

---

## 3. Критерии приёмки (Definition of Done для M1)

1. [ ] Модель `AutopilotSetting` в `db.py` содержит новые поля с проверкой констрейнта.
2. [ ] Alembic-миграция `0014` создана и готова к накатыванию.
3. [ ] Роутер `/api/v1/ticket-runs/settings` принимает и возвращает параметры скрытых комментариев.
4. [ ] Сервисный слой корректно обновляет и кэширует параметры.
5. [ ] Все тесты из `test_autopilot_internal_comments_schema.py` и существующие тесты `test_ticket_runs.py` проходят успешно.
