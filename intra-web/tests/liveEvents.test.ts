import test from 'node:test';
import assert from 'node:assert';

// Моделируем логику сопоставления статусов из useLiveEvents / App
const STATUS_MAP: Record<number, { status: string; statusName: string }> = {
  29: { status: 'closed', statusName: 'Выполнена' },
  26: { status: 'in_progress', statusName: 'В работе' },
  30: { status: 'cancelled', statusName: 'Отменена' },
  35: { status: 'client_hold', statusName: 'На согласовании' },
  36: { status: 'client_hold', statusName: 'Ждем поставку' },
  37: { status: 'client_hold', statusName: 'Ждем пользователя' },
  48: { status: 'client_hold', statusName: 'Плановые работы' },
};

function parseLiveEvent(rawData: string) {
  const parsed = JSON.parse(rawData);
  const eventType = parsed.event || parsed.type || 'message';
  return { ...parsed, event: eventType };
}

function resolveTaskStatusUpdate(event: any) {
  if (event.event === 'triage_applied' && Array.isArray(event.task_ids) && event.status_id) {
    return {
      taskIds: event.task_ids,
      statusInfo: STATUS_MAP[event.status_id] ?? { status: 'updated', statusName: 'Обновлена' },
    };
  }
  if ((event.event === 'status_change' || event.event === 'new_task') && event.task_id && event.status_id) {
    return {
      taskIds: [event.task_id],
      statusInfo: STATUS_MAP[event.status_id] ?? { status: 'updated', statusName: 'Обновлена' },
    };
  }
  if (event.event === 'success' && event.data?.task_id) {
    return {
      taskIds: [event.data.task_id],
      statusInfo: STATUS_MAP[29],
    };
  }
  return null;
}

test('liveEvents: парсинг входящего SSE события с полем event', () => {
  const raw = JSON.stringify({ event: 'triage_applied', task_ids: [101, 102], status_id: 29 });
  const ev = parseLiveEvent(raw);
  assert.strictEqual(ev.event, 'triage_applied');
  assert.deepStrictEqual(ev.task_ids, [101, 102]);
  assert.strictEqual(ev.status_id, 29);
});

test('liveEvents: парсинг входящего события poller с полем type', () => {
  const raw = JSON.stringify({ type: 'status_change', task_id: 5555, status_id: 26 });
  const ev = parseLiveEvent(raw);
  assert.strictEqual(ev.event, 'status_change');
  assert.strictEqual(ev.task_id, 5555);
  assert.strictEqual(ev.status_id, 26);
});

test('liveEvents: сопоставление статуса triage_applied', () => {
  const ev = { event: 'triage_applied', task_ids: [1001, 1002], status_id: 30 };
  const update = resolveTaskStatusUpdate(ev);
  assert.ok(update);
  assert.deepStrictEqual(update.taskIds, [1001, 1002]);
  assert.strictEqual(update.statusInfo.status, 'cancelled');
  assert.strictEqual(update.statusInfo.statusName, 'Отменена');
});

test('liveEvents: сопоставление завершения задачи воркера (success)', () => {
  const ev = { event: 'success', data: { task_id: 8888, message: 'Wi-Fi выдан' } };
  const update = resolveTaskStatusUpdate(ev);
  assert.ok(update);
  assert.deepStrictEqual(update.taskIds, [8888]);
  assert.strictEqual(update.statusInfo.status, 'closed');
  assert.strictEqual(update.statusInfo.statusName, 'Выполнена');
});

test('liveEvents: парсинг и валидация событий batch triage (task_analyzed, batch_completed, batch_cancelled)', () => {
  const rawAnalyzed = JSON.stringify({
    event: 'task_analyzed',
    batch_id: 'batch_test_123',
    task_id: 139001,
    status: 'processed',
    scenario_key: 'printer_install',
    progress: { processed: 10, failed: 1, total: 50, pct: 22 },
  });
  const evAnalyzed = parseLiveEvent(rawAnalyzed);
  assert.strictEqual(evAnalyzed.event, 'task_analyzed');
  assert.strictEqual(evAnalyzed.batch_id, 'batch_test_123');
  assert.strictEqual(evAnalyzed.task_id, 139001);
  assert.strictEqual(evAnalyzed.status, 'processed');
  assert.strictEqual(evAnalyzed.scenario_key, 'printer_install');
  assert.strictEqual(evAnalyzed.progress.pct, 22);

  const rawCompleted = JSON.stringify({
    event: 'batch_completed',
    batch_id: 'batch_test_123',
    total: 50,
    processed: 48,
    failed: 2,
    skipped: 0,
    elapsed_seconds: 4.2,
  });
  const evCompleted = parseLiveEvent(rawCompleted);
  assert.strictEqual(evCompleted.event, 'batch_completed');
  assert.strictEqual(evCompleted.processed, 48);
  assert.strictEqual(evCompleted.elapsed_seconds, 4.2);

  const rawCancelled = JSON.stringify({
    event: 'batch_cancelled',
    batch_id: 'batch_test_123',
    total: 50,
    processed: 25,
    failed: 0,
    elapsed_seconds: 2.1,
  });
  const evCancelled = parseLiveEvent(rawCancelled);
  assert.strictEqual(evCancelled.event, 'batch_cancelled');
  assert.strictEqual(evCancelled.processed, 25);
});

