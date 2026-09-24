# 📑 Спецификация контрактов и схем данных IntraLink v2

> **Статус:** Нормативный контракт v2  
> **Связанные документы:** [v2-automation-pipeline.md](file:///docs/architecture/v2-automation-pipeline.md), [domain-model-and-contracts.md](file:///docs/architecture/domain-model-and-contracts.md)

---

## 🗄️ 1. Модели базы данных (SQLAlchemy 2.0 / PostgreSQL)

### 1.1. Таблица `system_state` (Двойной Watermark)
Служит постоянной точкой отсчета для циклов опроса IntraService API. Дублирует горячие метки из Redis.

```python
class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g., "poller_watermark"
    last_check_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
```

---

### 1.2. Таблица `autopilot_policies` (Матрица автономии)
Хранит настройки уровней автономии для каждого бизнес-сценария.

```python
class AutopilotPolicy(Base):
    __tablename__ = "autopilot_policies"

    scenario_key: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g., "install_printer"
    mode: Mapped[str] = mapped_column(String(32), default="ASSISTED")        # FULL_AUTO | ASSISTED | DISABLED
    min_confidence: Mapped[float] = mapped_column(Float, default=0.85)
    max_clarification_retries: Mapped[int] = mapped_column(Integer, default=2)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)    # Счетчик для Circuit Breaker
    is_circuit_broken: Mapped[bool] = mapped_column(Boolean, default=false)  # Флаг срабатывания предохранителя
    updated_by: Mapped[str] = mapped_column(String(128), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

---

### 1.3. Таблица `autopilot_runs` (Журнал запусков автопилота)
Фиксирует историю всех запусков сценариев и диалоговых итераций.

```python
class AutopilotRun(Base):
    __tablename__ = "autopilot_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False)
    mode_applied: Mapped[str] = mapped_column(String(32), nullable=False)    # FULL_AUTO | ASSISTED
    status: Mapped[str] = mapped_column(String(32), nullable=False)          # SUCCESS | FAILED | SUSPENDED_WAITING | CLARIFIED
    clarification_round: Mapped[int] = mapped_column(Integer, default=0)
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    public_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    private_audit_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

---

## 📦 2. Pydantic DTO задач Taskiq

### 2.1. Контракт задачи сценария (`ScenarioTaskParams` и `ScenarioTaskResult`)

```python
class ScenarioTaskParams(BaseModel):
    task_id: int
    scenario_key: str
    target_host: Optional[str] = None
    target_ip: Optional[str] = None
    target_user: Optional[str] = None
    additional_params: Dict[str, Any] = Field(default_factory=dict)
    is_autonomous: bool = False
    actor: str = "autopilot"

class ScenarioTaskResult(BaseModel):
    task_id: int
    scenario_key: str
    success: bool
    status_applied: Optional[int] = None       # e.g., 3 (Выполнена), 6 (Приостановлена)
    public_comment: Optional[str] = None
    private_audit_note: Optional[str] = None
    failure_kind: Optional[str] = None         # "infrastructure" | "missing_data" | "applicant_action_required"
    logs: List[str] = Field(default_factory=list)
```

---

## 🌐 3. REST API Контракты

### 3.1. Short-Polling статуса задачи (`GET /api/v2/tasks/{task_id}`)
Используется фронтендом (TanStack Query v5) для отслеживания прогресса.

* **Ответ (`TaskStatusResponse`):**
```json
{
  "task_id": "c1f7b042-3a52-4e89-8d77-628d0bfa8022",
  "status": "running", // "queued" | "running" | "success" | "failed"
  "progress_message": "Проверка сетевого порта 5985...",
  "completed_at": null,
  "result": null
}
```

---

### 3.2. Управление матрицей автономии (`/api/v2/autopilot/policies`)
* **`GET /api/v2/autopilot/policies`** — список всех сценариев и их текущих тумблеров.
* **`PUT /api/v2/autopilot/policies/{scenario_key}`** — обновление режима работы:
  ```json
  {
    "mode": "FULL_AUTO", // "FULL_AUTO" | "ASSISTED" | "DISABLED"
    "min_confidence": 0.85
  }
  ```

---

### 3.3. Журнал автопилота (`GET /api/v2/autopilot/runs`)
* **Параметры:** `limit=50`, `scenario_key=install_printer`, `status=SUCCESS`.
* **Ответ:** Список записей `AutopilotRun` для отрисовки таблицы на вкладке «Автопилот».
