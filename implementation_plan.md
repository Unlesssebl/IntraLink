# Единый план: Промышленный асинхронный пакетный анализ заявок (Async Batch Triage Platform)

> **Статус:** Утверждение архитектуры и детального плана  
> **Основание:** [ADR 0003](docs/adr/0003-asynchronous-batch-triage-platform.md), аудит `/deep-reflect`

---

## 1. Контекст, проблема и целевая архитектура

### 1.1. Проблема синхронного подхода
Текущий вызов `POST /api/v1/triage/analyze-batch` выполняется синхронно:
- Анализ очереди из 100–200 заявок при параллелизме `TRIAGE_ANALYSIS_MAX_CONCURRENCY=4` занимает **60–120 секунд** (IntraService API + FastEmbed + pgvector + LLM).
- Длинный HTTP POST гарантированно вызывает **`504 Gateway Timeout`** на корпоративных reverse proxy и балансировщиках с лимитом ожидания 60 сек.
- Клиентское чанкование в React делает браузер ненадежным оркестратором очереди: при закрытии вкладки или сбое сети процесс прерывается.
- Оператор видит заблокированный спиннер без деталей («UX Blackout»).

### 1.2. Целевое решение (ADR 0003)
Переход на асинхронную модель **Job Queue + HTTP 202 Accepted + SSE Event Stream**:
1. **Быстрый шлюз (< 30 мс)**: `POST /api/v1/triage/analyze-batch` принимает ID заявок, регистрирует задание в Redis и возвращает `202 Accepted` с `batch_id`.
2. **Фоновый серверный оркестратор**: корутина с семафором `concurrency=4` выполняет анализ заявок на сервере независимо от браузера.
3. **Реактивный фидбек**: по завершении каждой заявки в Redis Pub/Sub (`events:all`) публикуется событие `task_analyzed`, транслируемое в браузер через существующий SSE-стрим `/api/v1/events/stream`.
4. **Плавный UI**: фронтенд буферизирует события (сброс раз в 350 мс), на лету красит карточки заявок и двигает живой прогресс-бар с возможностью отмены.

---

## 2. Спецификация данных в Redis (Redis Key & Schema Design)

| Ключ в Redis | Тип данных | TTL | Назначение |
|---|---|---|---|
| `triage:batch:{batch_id}` | **Hash** | 1800 с (30 мин) | Метаданные батча: `batch_id`, `total`, `processed`, `failed`, `skipped`, `status`, `created_at`, `heartbeat`, `operator` |
| `triage:batch:{batch_id}:completed` | **Set** | 1800 с (30 мин) | Набор ID завершённых заявок для быстрой сверки (`SMEMBERS`) при реконнекте |
| `triage:batch:active_id` | **String** | 1800 с (30 мин) | **Single Flight** замок: содержит `batch_id` текущего активного батча |
| `triage:batch:{batch_id}:abort` | **String** | 300 с (5 мин) | Флаг кооперативной отмены (создаётся при вызове `/cancel`) |

---

## 3. Спецификация REST API Контрактов

### 3.1. Запуск пакетного анализа
* **Эндпоинт:** `POST /api/v1/triage/analyze-batch`
* **Права:** `triage:mutate`
* **Тело запроса:**
  ```json
  {
    "task_ids": [140594, 140593, 140589]
  }
  ```
  *(Валидация: список целых чисел > 0, дедупликация, лимит от 1 до 500 элементов)*.
* **Ответ при новом запуске (HTTP 202 Accepted):**
  ```json
  {
    "status": "accepted",
    "batch_id": "batch_8f2a1b9c",
    "total": 3,
    "already_running": false,
    "message": "Пакетный анализ запущен в фоновом режиме"
  }
  ```
* **Ответ при наличии активного батча (Single Flight, HTTP 200 OK):**
  ```json
  {
    "status": "already_running",
    "batch_id": "batch_8f2a1b9c",
    "total": 3,
    "already_running": true,
    "message": "Пакетный анализ уже выполняется"
  }
  ```

---

### 3.2. Опрос статуса и сверка (Reconciliation)
* **Эндпоинт:** `GET /api/v1/triage/analyze-batch/{batch_id}`
* **Права:** `triage:read`
* **Ответ (HTTP 200 OK):**
  ```json
  {
    "batch_id": "batch_8f2a1b9c",
    "status": "running",
    "total": 200,
    "processed": 45,
    "failed": 2,
    "skipped": 0,
    "progress_pct": 23,
    "is_active": true,
    "completed_task_ids": [140594, 140593]
  }
  ```

---

### 3.3. Кооперативная отмена анализа
* **Эндпоинт:** `POST /api/v1/triage/analyze-batch/{batch_id}/cancel`
* **Права:** `triage:mutate`
* **Ответ (HTTP 200 OK):**
  ```json
  {
    "batch_id": "batch_8f2a1b9c",
    "status": "cancelling",
    "message": "Сигнал отмены передан воркеру"
  }
  ```

---

## 4. Спецификация событий шины (Redis Pub/Sub -> SSE)

События публикуются в канал `events:all` и доставляются клиентам через `/api/v1/events/stream`:

### 4.1. `event: task_analyzed`
Публикуется сразу после финализации анализа конкретной заявки:
```json
{
  "event": "task_analyzed",
  "batch_id": "batch_8f2a1b9c",
  "task_id": 140594,
  "status": "processed",
  "analysis": {
    "has_result": true,
    "state": "ready",
    "freshness": "current",
    "disposition": "available",
    "decision_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "decision_version": 1,
    "scenario_key": "consultation",
    "can_quick_apply": true
  },
  "progress": {
    "processed": 45,
    "failed": 2,
    "total": 200,
    "pct": 23
  }
}
```

### 4.2. `event: batch_completed`
Публикуется при завершении всех заявок из пачки:
```json
{
  "event": "batch_completed",
  "batch_id": "batch_8f2a1b9c",
  "total": 200,
  "processed": 195,
  "failed": 5,
  "skipped": 0,
  "elapsed_seconds": 42.5
}
```

### 4.3. `event: batch_cancelled`
Публикуется при остановке оператором:
```json
{
  "event": "batch_cancelled",
  "batch_id": "batch_8f2a1b9c",
  "total": 200,
  "processed": 45,
  "failed": 2
}
```

---

## 5. Предохранители и надежность (Edge Cases & Resilience)

1. **Защита корутин от сборщика мусора (GC Protection):**  
   Фоновая задача регистрируется в наборе `_active_background_tasks: set[asyncio.Task]`, ссылка удаляется только через `task.add_done_callback(_active_background_tasks.discard)`.
2. **Heartbeat и Stale Recovery при рестарте `core-api`:**  
   Воркер каждые 5 секунд обновляет поле `heartbeat = time.time()` в Redis. В хуке startup FastAPI проверяется `triage:batch:active_id`: если сервер перезапустился и `time.time() - heartbeat > 30`, замок сбрасывается, а батч переводится в `interrupted`.
3. **Защита от шторма ре-рендеров (UI Throttling):**  
   На фронтенде события `task_analyzed` поступают в буфер (`useRef`) и сбрасываются в React state пачками раз в **350 мс**, предотвращая лаги DOM на слабых клиентах.
4. **Автоматическая сверка (Reconciliation):**  
   При событии `batch_completed` или при переподключении SSE фронтенд опрашивает `GET /analyze-batch/{batch_id}` и точечно подтягивает пропущенные изменения.

---

## 6. Детализация изменений в файлах

### Backend:
1. **[MODIFY] [core-api/app/routers/triage.py](core-api/app/routers/triage.py)**
   - Добавление Pydantic DTO: `AnalyzeBatchResponse`, `BatchStatusResponse`, `CancelBatchResponse`.
   - Обновление `POST /api/v1/triage/analyze-batch` (202 Accepted + Single Flight).
   - Реализация фонового воркера `_execute_triage_batch_worker` (Heartbeat, Semaphore, Redis Pub/Sub, Abort-check).
   - Реализация `GET /api/v1/triage/analyze-batch/{batch_id}`.
   - Реализация `POST /api/v1/triage/analyze-batch/{batch_id}/cancel`.
2. **[MODIFY] [core-api/app/main.py](core-api/app/main.py)**
   - В хук lifespan startup: вызов очистки зависших батчей `cleanup_stale_triage_batches(redis)`.
3. **[MODIFY] [core-api/tests/test_triage.py](core-api/tests/test_triage.py)**
   - Тест на возврат 202 Accepted и генерацию `batch_id`.
   - Тест Single Flight (повторный запуск возвращает активный батч).
   - Тест отмены батча через `/cancel`.
   - Тест получения статуса через `GET /analyze-batch/{batch_id}`.

### Frontend:
4. **[MODIFY] [intra-web/src/hooks/useLiveEvents.ts](intra-web/src/hooks/useLiveEvents.ts)**
   - Добавление типов для событий `task_analyzed`, `batch_completed`, `batch_cancelled`.
   - Регистрация SSE event listeners и проброс колбэков.
5. **[MODIFY] [intra-web/src/lib/tasks.ts](intra-web/src/lib/tasks.ts)**
   - Обновление `triggerQueueAnalysis`: отправка `POST /analyze-batch` без клиентского чанкования, возврат `{ batch_id, total, status }`.
   - Добавление функций `getBatchStatus(batchId)` и `cancelBatch(batchId)`.
6. **[MODIFY] [intra-web/src/App.tsx](intra-web/src/App.tsx)**
   - Обработка `onTaskAnalyzed` из `useLiveEvents`: реактивное обновление `t.analysis` и `t.isProcessed` у соответствующей заявки в общем массиве `tickets`.
7. **[MODIFY] [intra-web/src/pages/QueuePage.tsx](intra-web/src/pages/QueuePage.tsx)**
   - Throttled Event Buffer (таймер 350 мс).
   - Компонент **Live Batch Progress Banner**:
     - Текст: *«⚡ Анализ очереди: {processed} из {total} ({pct}%)»*.
     - Анимированный прогресс-бар (`width: ${pct}%`).
     - Кнопка «Остановить» (`cancelBatch`).
     - Авто-скрытие через 3 секунды после завершения.

---

## 7. План верификации

### Автоматические тесты:
```powershell
# Бэкенд
.\.venv\Scripts\python.exe -m pytest core-api\tests\test_triage.py core-api\tests\test_admin_queue.py -v
ruff check core-api\app\routers\triage.py core-api\tests\test_triage.py

# Фронтенд
npm --prefix intra-web test
npm --prefix intra-web run build
```

### Ручная интеграционная верификация в браузере:
1. Запуск анализа 200 заявок через кнопку «Проанализировать очередь».
2. Проверка вкладки Network: `POST /analyze-batch` завершается за **< 50 мс** со статусом **202 Accepted**.
3. Проверка интерфейса:
   - Появление баннера прогресса со шкалой от 0% до 100%.
   - Карточки в очереди на лету получают бейджи и переходят во вкладку «Проанализированные AI».
   - Скролл таблицы остаётся плавным без подвисаний.
4. Проверка устойчивости:
   - Перезагрузка страницы (`F5`): батч на сервере продолжает выполняться, после F5 баннер прогресса восстанавливается или подтягивает свежие данные.
5. Проверка отмены:
   - Клик на кнопку «Остановить»: батч останавливается, в логах фиксируется `batch_cancelled`.
