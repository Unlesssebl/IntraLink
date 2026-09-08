# Эксплуатационный регламент: Жизненный цикл AI-анализа (Triage Lifecycle)

## 1. Концепция и источник истины

В системе IntraLink источником истины о результате анализа заявки является последний сохранённый `DecisionRecord` с `analysis_kind = "triage"` в PostgreSQL.
Redis выполняет исключительно вспомогательные функции: распределённые блокировки (`lock:triage-analysis:{task_id}`) с fencing token (`fence:triage-analysis:{task_id}`), отслеживание активного выполнения и доставка событий (SSE).

### Публичное состояние анализа (`analysis`)

Каждый объект заявки снабжается сервером объектом `analysis`:
- `state`: `not_analyzed` (не анализировалась), `analyzing` (идёт расчёт), `ready` (есть результат), `failed` (ошибка).
- `freshness`: `current` (актуально), `stale` (устарело: изменились правила или заявка), `unknown` (не удалось сверить с IntraService).
- `disposition`: `available` (доступно для применения), `applied` (решение уже применено через Command API v2).
- `has_result`: логический флаг пригодности результата (`true`, если анализ завершался успешно хотя бы раз).

---

## 2. Управление ревизией логики (`ANALYSIS_REVISION`)

В `.env` и конфигурации бэкенда зафиксирован параметр `ANALYSIS_REVISION` (строка, по умолчанию `"1"`):
```env
ANALYSIS_REVISION=1
TRIAGE_ANALYSIS_MAX_CONCURRENCY=4
```

### Когда повышать `ANALYSIS_REVISION`:
- При внесении изменений в Rule Engine, правила классификации или логику сопоставления с шаблонами.
- При обновлении системных промптов LLM или структуры возвращаемых решений.
- При изменении политик утверждения (policies), влияющих на безопасность применения.

### Что происходит при смене ревизии:
1. Ранее рассчитанные решения **не удаляются** из БД.
2. При запросе карточки или очереди бэкенд сравнивает ревизию в `DecisionRecord.context_json["analysis_revision"]` с текущим значением `settings.ANALYSIS_REVISION`.
3. При несовпадении решение автоматически получает статус `freshness = "stale"` с причиной `stale_reason = "Правила анализа изменились"`.
4. Кнопка быстрого применения для таких решений блокируется, и оператору предлагается выполнить явный повторный анализ («Проанализировать повторно»).

---

## 3. Явные операции анализа через API

`GET`-эндпоинты очереди (`/api/v1/triage/batch`) и карточки (`/api/v1/triage/tasks/{id}`) являются строго **read-only** и никогда не запускают вычисления, RAG или LLM.

Для анализа используются специализированные mutating эндпоинты (требуют права `triage:mutate`):
- `POST /api/v1/triage/tasks/{id}/analyze` — выполняет анализ, если готового актуального результата ещё нет.
- `POST /api/v1/triage/tasks/{id}/reanalyze` — принудительно создаёт новую версию решения.
- `POST /api/v1/triage/analyze-batch` — пакетный анализ списка заявок (`task_ids`) с параллелизмом `TRIAGE_ANALYSIS_MAX_CONCURRENCY`.

---

## 4. Регламент чистой переинициализации базы данных и RAG (Maintenance Window)

При необходимости фундаментальной очистки окружения (удаления временных тестовых решений и неконсистентного кэша):

1. **Остановка приёма команд и воркеров**:
   ```bash
   docker compose stop execution-worker core-api
   ```
2. **Удаление рабочих volumes**:
   ```bash
   docker volume rm intralink_postgres_data intralink_redis_data
   ```
3. **Запуск инфраструктурных сервисов**:
   ```bash
   docker compose up -d postgres redis
   ```
4. **Применение миграций схемы**:
   ```bash
   docker compose run --rm core-api alembic upgrade head
   ```
5. **Запуск Core API и bootstrap администратора**:
   ```bash
   docker compose up -d core-api
   # Проверить готовность: curl http://127.0.0.1:8000/health
   # Авторизоваться администратором из списка ADMIN_LOGINS
   ```
6. **Полная синхронизация и векторизация базы решений Helpdesk (RAG)**:
   ```bash
   # Через Core API или helpdesk-cli:
   curl -X POST http://127.0.0.1:8000/api/v1/triage/rag/sync -H "Authorization: Bearer <ADMIN_TOKEN>" -H "Content-Type: application/json" -d '{"days": 90, "limit": 1000}'
   ```
7. **Запуск фоновых воркеров**:
   ```bash
   docker compose up -d execution-worker
   ```
8. **Smoke-верификация**:
   - Открыть очередь оператора в веб-интерфейсе (`http://127.0.0.1:8000/`).
   - Убедиться, что неанализированные заявки находятся во вкладке «Не проанализированные AI».
   - Выполнить анализ одной тестовой заявки и проверить переход во вкладку «Проанализированные AI».
