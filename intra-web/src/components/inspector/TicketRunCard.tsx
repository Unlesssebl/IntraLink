import { useCallback, useEffect, useState } from 'react';
import {
  controlTicketRun,
  fetchTicketRun,
  startTicketRun,
  type TicketRun,
  type TicketRunEvent,
  type TicketRunCommand,
  type TicketRunMode,
} from '../../lib/ticketRuns';
import { confirmExecutionJob } from '../../lib/tasks';

interface Props {
  taskId: number;
  onToast: (toast: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  onRunChange?: (run: TicketRun | null) => void;
}

const stateLabels: Record<string, string> = {
  pending: 'Ожидает запуска',
  running: 'Выполняется',
  waiting_answer: 'Ожидает ответа',
  waiting_approval: 'Ожидает подтверждения',
  paused: 'Приостановлен',
  system_error: 'Системная ошибка',
  completed: 'Завершён',
};

export default function TicketRunCard({ taskId, onToast, onRunChange }: Props) {
  const [run, setRun] = useState<TicketRun | null>(null);
  const [events, setEvents] = useState<TicketRunEvent[]>([]);
  const [pendingCommand, setPendingCommand] = useState<TicketRunCommand | null>(null);
  const [busy, setBusy] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchTicketRun(taskId);
      setRun(data.run);
      onRunChange?.(data.run);
      setEvents(data.events);
      setPendingCommand(data.pending_command);
    } catch (error) {
      console.warn('Не удалось загрузить цикл заявки', error);
    }
  }, [taskId, onRunChange]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const decideCommand = async (decision: 'approve' | 'reject') => {
    if (!pendingCommand) return;
    setBusy(true);
    try {
      await confirmExecutionJob(
        pendingCommand.command_id,
        decision,
        decision === 'reject' ? 'Отклонено инженером из карточки заявки' : undefined,
      );
      onToast({
        type: decision === 'approve' ? 'success' : 'warning',
        message: decision === 'approve' ? 'Команда подтверждена' : 'Команда отклонена, цикл приостановлен',
      });
      await load();
    } catch (error: any) {
      onToast({ type: 'error', message: `Не удалось обработать подтверждение: ${error.message || error}` });
      await load();
    } finally {
      setBusy(false);
    }
  };

  const mutate = async (operation: () => Promise<TicketRun>, success: string) => {
    setBusy(true);
    try {
      const updated = await operation();
      setRun(updated);
      onRunChange?.(updated);
      await load();
      onToast({ type: 'success', message: success });
    } catch (error: any) {
      onToast({ type: 'error', message: `Не удалось изменить режим: ${error.message || error}` });
      await load();
    } finally {
      setBusy(false);
    }
  };

  const start = (mode: TicketRunMode) =>
    mutate(() => startTicketRun(taskId, mode), mode === 'manual' ? 'Ручной цикл создан' : 'Автопилот запущен');

  return (
    <section className="rounded-lg border border-neutral-200 bg-neutral-50/80 p-3 dark:border-neutral-800 dark:bg-neutral-950/50">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[11px] font-bold uppercase tracking-wider text-neutral-400">Режим заявки</div>
          <div className="mt-0.5 text-xs font-semibold text-neutral-800 dark:text-neutral-200">
            {run ? `${run.mode === 'autopilot' ? 'Автопилот' : 'Ручной'} · ${stateLabels[run.state] || run.state}` : 'Цикл ещё не создан'}
          </div>
        </div>
        {run?.current_step && <span className="rounded bg-neutral-200 px-2 py-1 text-[10px] text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">{run.current_step}</span>}
      </div>

      {run?.error_message && <p className="mt-2 text-xs text-rose-600 dark:text-rose-300">{run.error_message}</p>}
      {run?.waiting_until && <p className="mt-2 text-[11px] text-amber-700 dark:text-amber-300">Срок ожидания: {new Date(run.waiting_until).toLocaleString('ru-RU')}</p>}
      {pendingCommand && (
        <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 dark:border-amber-800 dark:bg-amber-950/40">
          <div className="text-[11px] font-semibold text-amber-900 dark:text-amber-100">
            Требуется подтверждение: {pendingCommand.action}
          </div>
          <div className="mt-2 flex gap-2">
            <button disabled={busy} onClick={() => void decideCommand('approve')} className="rounded bg-amber-600 px-2.5 py-1 text-[11px] font-semibold text-white disabled:opacity-50">Подтвердить</button>
            <button disabled={busy} onClick={() => void decideCommand('reject')} className="rounded border border-amber-400 px-2.5 py-1 text-[11px] font-semibold text-amber-900 disabled:opacity-50 dark:text-amber-100">Отклонить</button>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {!run && (
          <>
            <button disabled={busy} onClick={() => start('manual')} className="rounded border border-neutral-300 px-2.5 py-1.5 text-[11px] font-semibold hover:bg-white disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800">Начать вручную</button>
            <button disabled={busy} onClick={() => start('autopilot')} className="rounded bg-blue-600 px-2.5 py-1.5 text-[11px] font-semibold text-white hover:bg-blue-500 disabled:opacity-50">Запустить автопилот</button>
          </>
        )}
        {run && !run.completed_at && (
          <>
            <button
              disabled={busy}
              onClick={() => mutate(
                () => controlTicketRun(run.id, 'switch_mode', run.version, run.mode === 'manual' ? 'autopilot' : 'manual'),
                run.mode === 'manual' ? 'Автопилот включён для заявки' : 'Заявка переведена в ручной режим',
              )}
              className="rounded border border-neutral-300 px-2.5 py-1.5 text-[11px] font-semibold hover:bg-white disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
            >
              {run.mode === 'manual' ? 'Включить автопилот' : 'Перейти вручную'}
            </button>
            <button
              disabled={busy}
              onClick={() => mutate(
                () => controlTicketRun(run.id, run.state === 'paused' ? 'resume' : 'pause', run.version),
                run.state === 'paused' ? 'Цикл возобновлён' : 'Цикл приостановлен',
              )}
              className="rounded border border-neutral-300 px-2.5 py-1.5 text-[11px] font-semibold hover:bg-white disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
            >
              {run.state === 'paused' ? 'Возобновить' : 'Приостановить'}
            </button>
          </>
        )}
        {events.length > 0 && (
          <button onClick={() => setShowHistory(value => !value)} className="px-1.5 py-1 text-[11px] text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200">
            {showHistory ? 'Скрыть историю' : `История (${events.length})`}
          </button>
        )}
      </div>

      {showHistory && (
        <div className="mt-3 space-y-1 border-t border-neutral-200 pt-2 dark:border-neutral-800">
          {events.slice(-6).reverse().map(event => (
            <div key={event.id} className="flex justify-between gap-3 text-[10px] text-neutral-500">
              <span>{event.event_type} · {event.actor}</span>
              <span>{event.created_at ? new Date(event.created_at).toLocaleString('ru-RU') : ''}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
