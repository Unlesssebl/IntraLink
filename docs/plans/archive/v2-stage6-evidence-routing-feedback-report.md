# 📋 Отчет о реализации Этапа 6: Evidence-Based Routing Cascade

> **Версия:** 2.0.0 (Pre-production)  
> **Статус:** Завершено (All 449 unit/integration tests passing, 0 frontend build errors)  
> **Ветка:** `feat/v2-vertical-slice-architecture`

---

## 🎯 1. Цели и объем работ Этапа 6

Этап 6 завершил переход к единому **Evidence-Based Routing Cascade**:
1. **Канонический feedback-контур:** Таблица и модель `RoutingFeedbackRecord` стали единственным источником правды для всех операторских вердиктов (`approved`, `corrected`, `rejected`, `manual_takeover`).
2. **Ликвидация Autopilot Correction & Legacy-маршрутизации:** Удален параллельный dual-write, миграция `0005_routing_feedback_consolidation.py` перенесла существующие legacy-записи с `source=legacy` и удалила старую модель и таблицу `autopilot_corrections`.
3. **Серверная проекция состояния плана (`AgentPlanDTO`):** Полная инкапсуляция бизнес-логики и проверок на бэкенде (`approval_state`, `can_approve`, `can_correct`, `can_reject`, `blocking_reason_codes`, `is_stale`, `has_terminal_feedback`, `command_id`, `command_status`). GET плана строго read-only.
4. **Удаление искусственных confidence-контрактов:** Из DTO, моделей и UI удалены проценты `confidence`, `factor_breakdown`, `min_confidence`. Решения объясняются детерминированными `reason_codes` и `evidence_summaries`.
5. **Серверный каталог исполнимых сценариев:** Эндпоинт `GET /api/v2/autopilot/scenarios/catalog` предоставляет типизированные схемы полей для коррекции без раскрытия паролей и секретов.
6. **Операторский UX (Progressive Disclosure):** Карточка `AgentPlanCard.tsx` переработана по принципам прогрессивного раскрытия: основные действия на виду, технические детали, доказательства, аудит и preflight-чеки в раскрывающихся секциях.
7. **Метрики качества и калибровка:** Эндпоинты `GET /api/v2/autopilot/metrics/quality` и `GET /api/v2/autopilot/feedback/export` с фильтрацией legacy-записей и автоматической санитизацией секретов.

---

## 🛠️ 2. Архитектурные изменения

### База данных и DTO (`core/database/`, `core/autopilot/dto.py`):
- **`RoutingFeedbackRecord`:**
  - Поля: `id`, `decision_id`, `prepared_plan_id`, `command_id`, `task_id`, `snapshot_hash`, `operator_username`, `verdict`, `original_scenario`, `corrected_scenario`, `original_params`, `corrected_params`, `reason_tag`, `operator_notes`, `router_version`, `prompt_version`, `verifier_used`, `source`, `created_at`.
  - Автоматическая санитизация секретов в SQLAlchemy `before_insert`/`before_update` listeners (`sanitize_secrets`).
- **`ScenarioCatalogItemDTO` & `ScenarioFieldSchemaDTO`:** типизированное описание доступных оператору сценариев и их редактируемых полей.
- **`AgentPlanDTO`:** расширен полями серверной проекции жизненного цикла плана.

### API (`api/src/features/autopilot/`):
- `POST /plan/{id}/approve` — 1-клик подтверждение с проверкой OCC, preflight и созданием `CommandRecord` + `RoutingFeedbackRecord(verdict="approved")`.
- `POST /plan/{id}/correct` — коррекция сценария/параметров с валидацией по серверному каталогу, повторным preflight, вычислением нового `plan_hash` и записью `RoutingFeedbackRecord(verdict="corrected")`.
- `POST /plan/{id}/reject` — отклонение плана без создания команды (`RoutingFeedbackRecord(verdict="rejected")`).
- `POST /plan/{id}/manual-takeover` — явная передача тикета инженеру (`RoutingFeedbackRecord(verdict="manual_takeover")`).
- `GET /scenarios/catalog` — серверный каталог исполнимых сценариев.
- `GET /feedback` и `GET /feedback/export` — история и выгрузка датасета для калибровки/обучения с полным provenance и санитизацией.
- `GET /metrics/quality` — агрегированные метрики качества маршрутизации и согласия с оператором.

### Фронтенд (`web/src/features/`):
- `AgentPlanCard.tsx`: Linear-стиль с Progressive Disclosure, бейджами состояния (`selected`, `needs_clarification`, `ambiguous`, `degraded`, `stale`), типизированной формой коррекции и раскрывающимися блоками доказательств и preflight.
- `AutopilotConsole.tsx`: Карточки метрик качества (Точность, Доля правок, Доля отказов, Всего решений) с защитой от малого объема выборки (<5), обновленный Live Feedback Stream.

---

## 🧪 3. Результаты верификации

1. **Backend Tests:**
   - `core/tests`: **297 passed**
   - `api/tests`: **36 passed**
   - `worker/tests`: **116 passed**
   - **Итого: 449 passed, 0 failed** (время прогона: 23.5 сек).
2. **Frontend Build:**
   - `tsc && vite build`: **0 ошибок, код возврата 0**.
3. **Database Migration:**
   - `alembic upgrade -> downgrade -> upgrade`: протестировано и успешно применено на PostgreSQL.

---

## 🔒 4. Безопасность и No-Kludge Policy

- Никаких паролей и секретов (`Field1489`, `user_password`, `it_password`, `token`) не сохраняется в `RoutingFeedbackRecord`, `CommandRecord`, DTO, логах или UI.
- Идемпотентность операторских действий: повторный approve/correct с идентичными параметрами возвращает `is_duplicate=True` без создания дублирующих команд.
- OCC 409 при любых изменениях контекста тикета (`task.status_id`, `snapshot_hash`, `last_event_id`).
