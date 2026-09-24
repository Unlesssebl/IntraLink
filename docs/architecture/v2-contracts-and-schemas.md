# 📑 Спецификация контрактов и схем данных IntraLink v2 (v2.1 Refined)

> **Статус:** Нормативный контракт v2 (с учетом Big-Shot использования существующей `CommandRecord`)  
> **Связанные документы:** [v2-automation-pipeline.md](file:///docs/architecture/v2-automation-pipeline.md), [v2-automation-implementation-plan.md](file:///docs/plans/v2-automation-implementation-plan.md)

---

## 🗄️ 1. Модели базы данных (SQLAlchemy 2.0 / PostgreSQL)

### 1.1. Существующая модель: `CommandRecord` (Единая шина команд Outbox/Inbox)
Таблица [core/database/models.py:CommandRecord](file:///core/database/models.py) используется как единый журнал для всех действий (человека в UI и Автопилота). Дополнительные дублирующие таблицы не создаются.

```python
class CommandRecord(Base, TimestampMixin):
    __tablename__ = "commands"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True) # e.g. "install_printer", "ad_password_reset"
    executor: Mapped[str] = mapped_column(String(32), nullable=False, index=True) # 'api', 'worker', 'auto'
    target_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict) # {"ticket_id": 1234}
    params_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict) # {"pc_name": "...", "ip": "..."}
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True) # pending | running | succeeded | failed
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    initiator: Mapped[str] = mapped_column(String(100), nullable=False, index=True) # "user:alen" | "autopilot"
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    result_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True) # logs, failure_kind, public_report
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

---

### 1.2. Новая модель: `SystemState` (Двойной Watermark)
Служит персистентной точкой отсчета для циклов опроса IntraService API.

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

### 1.3. Новая модель: `AutopilotPolicy` (Матрица автономии и Circuit Breaker)
Хранит настройки уровней автономии для каждого бизнес-сценария.

```python
class AutopilotPolicy(Base):
    __tablename__ = "autopilot_policies"

    scenario_key: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g., "install_printer"
    mode: Mapped[str] = mapped_column(String(32), default="ASSISTED")        # FULL_AUTO | ASSISTED | DISABLED
    min_confidence: Mapped[float] = mapped_column(Float, default=0.85)
    max_clarification_retries: Mapped[int] = mapped_column(Integer, default=2)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)    # Счетчик ошибок для Circuit Breaker
    is_circuit_broken: Mapped[bool] = mapped_column(Boolean, default=False)   # Флаг сработавшего предохранителя
    updated_by: Mapped[str] = mapped_column(String(128), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

---

## 📦 2. Pydantic DTO задач Taskiq

```python
class DispatchCommandParams(BaseModel):
    command_id: uuid.UUID
    task_id: int
    action: str
    params: Dict[str, Any]
    is_autonomous: bool = False
    initiator: str = "autopilot"

class CommandExecutionResult(BaseModel):
    command_id: uuid.UUID
    success: bool
    status_applied: Optional[int] = None       # e.g., 3 (Выполнена), 6 (Приостановлена), 30 (Отменена)
    public_comment: Optional[str] = None
    private_audit_note: Optional[str] = None
    failure_kind: Optional[str] = None         # "infrastructure" | "missing_data" | "applicant_action_required" | "race_condition"
    logs: List[str] = Field(default_factory=list)
```

---

## 🛡️ 3. Контракты защиты и фильтрации (Anti-Loop & Optimistic Lock)

### 3.1. Защита от автоответчиков (Anti-Loop Filter)
```python
AUTO_REPLY_MARKERS = [
    "автоматический ответ",
    "автоответ",
    "out of office",
    "в отпуске",
    "автоматическое уведомление",
    "не отвечайте на это письмо",
]

def is_auto_responder(comment_text: str, author_id: Optional[int], service_bot_id: int) -> bool:
    if author_id == service_bot_id:
        return True
    lower = comment_text.lower()
    return any(marker in lower for marker in AUTO_REPLY_MARKERS)
```

### 3.2. Защита от состояния гонки (Optimistic Lock)
Перед отправкой обновления в IntraService:
```python
async def verify_ticket_not_hijacked(client: IntraServiceClient, task_id: int, expected_status_id: int) -> bool:
    latest = await client.get_task(task_id=task_id)
    # Если инженер уже взял в работу (статус 2) или сменил статус — автопилот отступает
    if latest.status_id != expected_status_id:
        return False
    return True
```

---

## 🌐 4. REST API Контракты

* **`GET /api/v2/tasks/{command_id}`:** Short-Polling фронтенда, возвращающий статус из `CommandRecord` (`pending` | `running` | `succeeded` | `failed`).
* **`GET /api/v2/autopilot/policies`:** Получение матрицы сценариев и статусов Circuit Breaker.
* **`PUT /api/v2/autopilot/policies/{scenario_key}`:** Обновление режима (`mode`, `min_confidence`).
* **`GET /api/v2/autopilot/runs`:** Список выполненных команд из `CommandRecord` с фильтром `initiator=autopilot`.
