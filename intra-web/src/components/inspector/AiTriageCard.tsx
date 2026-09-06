import { useState, useCallback, useEffect } from 'react';
import type { Ticket } from '../../data/mock';
import { getStatusDotClass } from '../../data/mock';
import type {
  TaskDetails,
  DecisionRecord,
  DecisionVerdict,
  DecisionReasonCode,
} from '../../lib/types';
import {
  controlTicketRun,
  fetchTicketRun,
  startTicketRun,
  type TicketRun,
  type TicketRunEvent,
  type TicketRunCommand,
  type TicketRunMode,
} from '../../lib/ticketRuns';
import { fetchTaskDecisions, submitDecisionFeedback } from '../../lib/decisionsApi';
import { confirmExecutionJob } from '../../lib/tasks';
import { IconBolt, IconSparkles, IconClose } from '../Icons';

interface AiTriageCardProps {
  ticket: Ticket;
  details: TaskDetails | null;
  ticketRun?: TicketRun | null;
  onRunChange?: (run: TicketRun | null) => void;
  onToast: (toast: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  targetStatusId: number;
  targetStatusName: string;
  selectedStatusOverride: number | null;
  onResetStatusOverride: () => void;
  reanalyzing: boolean;
  onReanalyze: () => void;
  replyText: string;
  onInsertAiSynthesis: (text: string) => void;
  expenses: number;
  onChangeExpenses: (mins: number) => void;
}

const sourceLabels: Record<string, string> = {
  rule: 'Регламент',
  rag: 'База решений',
  ai: 'AI-синтез',
};

const runLabels: Record<string, string> = {
  pending: 'Ожидает запуска',
  running: 'Выполняется',
  waiting_answer: 'Ждёт ответа',
  waiting_approval: 'Ждёт подтверждения',
  paused: 'Нужно внимание',
  system_error: 'Ошибка связи',
  completed: 'Завершён',
};

const reasonCodeLabels: Record<DecisionReasonCode, string> = {
  wrong_context: 'Неверный контекст заявки',
  wrong_classification: 'Ошибочная классификация сервиса',
  wrong_rule: 'Неверное правило регламента',
  wrong_kb: 'Неподходящее решение из базы знаний',
  wrong_ai_text: 'Некорректный текст AI-синтеза',
  wrong_policy: 'Ошибка политики выполнения',
  execution_error: 'Сбой исполнения команды',
  other: 'Другая причина',
};

export default function AiTriageCard({
  ticket,
  details,
  ticketRun,
  onRunChange,
  onToast,
  targetStatusId,
  targetStatusName,
  selectedStatusOverride,
  onResetStatusOverride,
  reanalyzing,
  onReanalyze,
  replyText,
  onInsertAiSynthesis,
  expenses,
  onChangeExpenses,
}: AiTriageCardProps) {
  const [activeTab, setActiveTab] = useState<'evidence' | 'history' | 'events' | null>(null);
  const [decisionsHistory, setDecisionsHistory] = useState<DecisionRecord[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [runEvents, setRunEvents] = useState<TicketRunEvent[]>([]);
  const [pendingCommand, setPendingCommand] = useState<TicketRunCommand | null>(null);
  const [busyRun, setBusyRun] = useState(false);

  // Feedback state
  const [submittingFeedback, setSubmittingFeedback] = useState(false);
  const [submittedVerdict, setSubmittedVerdict] = useState<DecisionVerdict | null>(null);
  const [selectedReason, setSelectedReason] = useState<DecisionReasonCode | ''>('');
  const [feedbackComment, setFeedbackComment] = useState('');
  const [showReasonPicker, setShowReasonPicker] = useState(false);

  const decision = details?.decision;
  const suggestion = details?.ai_suggestion;
  const readiness = details?.readiness;
  const sources = decision?.sources || details?.sources || {
    rule: ticket.hasRuleEngine,
    rag: Boolean(details?.kb_matches?.length),
    ai: Boolean(details?.ai_suggested_resolution),
  };
  const isStale = readiness?.stale || suggestion?.state === 'stale';
  const isReady = Boolean(
    decision?.proposal.ready ?? (!suggestion?.policy.blocked && !suggestion?.missing_data.length),
  );
  const blockedReasons =
    readiness?.blocked_reasons ||
    decision?.completeness.blocked_reasons ||
    suggestion?.missing_data ||
    [];

  const rawId = ticket.rawId || parseInt(ticket.id.replace(/\D/g, ''), 10);

  // Load ticket run details (events & pending command)
  const loadRunDetails = useCallback(async () => {
    if (!rawId) return;
    try {
      const data = await fetchTicketRun(rawId);
      onRunChange?.(data.run);
      setRunEvents(data.events || []);
      setPendingCommand(data.pending_command);
    } catch {
      // ignore
    }
  }, [rawId, onRunChange]);

  useEffect(() => {
    void loadRunDetails();
    const interval = window.setInterval(() => void loadRunDetails(), 8000);
    return () => window.clearInterval(interval);
  }, [loadRunDetails]);

  // Load decision history
  const loadHistory = useCallback(async () => {
    if (!rawId || loadingHistory) return;
    setLoadingHistory(true);
    try {
      const res = await fetchTaskDecisions(rawId, 15);
      setDecisionsHistory(res.items);
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка загрузки истории решений: ${err.message || err}` });
    } finally {
      setLoadingHistory(false);
    }
  }, [rawId, loadingHistory, onToast]);

  const handleToggleTab = (tab: 'evidence' | 'history' | 'events') => {
    if (activeTab === tab) {
      setActiveTab(null);
    } else {
      setActiveTab(tab);
      if (tab === 'history' && decisionsHistory.length === 0) {
        void loadHistory();
      }
      if (tab === 'events') {
        void loadRunDetails();
      }
    }
  };

  const handleDecideCommand = async (decisionOutcome: 'approve' | 'reject') => {
    if (!pendingCommand) return;
    setBusyRun(true);
    try {
      await confirmExecutionJob(
        pendingCommand.command_id,
        decisionOutcome,
        decisionOutcome === 'reject' ? 'Отклонено инженером в карточке решения' : undefined,
      );
      onToast({
        type: decisionOutcome === 'approve' ? 'success' : 'warning',
        message:
          decisionOutcome === 'approve'
            ? 'Команда подтверждена и запущена'
            : 'Команда отклонена, цикл приостановлен',
      });
      await loadRunDetails();
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка подтверждения: ${err.message || err}` });
    } finally {
      setBusyRun(false);
    }
  };

  const handleMutateRun = async (op: () => Promise<TicketRun>, successMsg: string) => {
    setBusyRun(true);
    try {
      const updated = await op();
      onRunChange?.(updated);
      await loadRunDetails();
      onToast({ type: 'success', message: successMsg });
    } catch (err: any) {
      onToast({ type: 'error', message: `Не удалось изменить цикл: ${err.message || err}` });
    } finally {
      setBusyRun(false);
    }
  };

  const handleSendFeedback = async (verdict: DecisionVerdict, explicitReason?: DecisionReasonCode) => {
    if (!decision?.id || submittingFeedback) return;
    setSubmittingFeedback(true);
    try {
      await submitDecisionFeedback(decision.id, {
        verdict,
        reason_code: explicitReason || (selectedReason ? (selectedReason as DecisionReasonCode) : undefined),
        comment: feedbackComment.trim() || undefined,
        final_action: {
          status_id: targetStatusId,
          comment: replyText,
          expenses,
        },
      });
      setSubmittedVerdict(verdict);
      setShowReasonPicker(false);
      onToast({ type: 'success', message: 'Оценка качества решения сохранена в журнале' });
    } catch (err: any) {
      onToast({ type: 'error', message: `Не удалось отправить отзыв: ${err.message || err}` });
    } finally {
      setSubmittingFeedback(false);
    }
  };

  return (
    <section className="overflow-hidden rounded-xl border border-neutral-200/80 bg-white shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
      {/* 1. Header: Status, Version, Action, Consequences */}
      <div className="flex items-start justify-between gap-4 border-b border-neutral-100 px-4 py-3 dark:border-neutral-800">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                isStale ? 'bg-amber-500 animate-pulse' : isReady ? 'bg-emerald-500' : 'bg-rose-500'
              }`}
            />
            <h3 className="text-sm font-semibold text-neutral-950 dark:text-neutral-50">
              {decision?.proposal.title || ticket.aiPlan?.actionTitle || 'Предложение по заявке'}
            </h3>
            <span
              className={`rounded-md px-2 py-0.5 text-[10.5px] font-semibold ${
                isStale
                  ? 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300'
                  : isReady
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300'
                  : 'bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300'
              }`}
            >
              {isStale ? 'Требует перерасчёта' : isReady ? 'Готово к проверке' : 'Данных недостаточно'}
            </span>
            {decision && (
              <span className="rounded bg-neutral-100 dark:bg-neutral-800 px-1.5 py-0.5 text-[10px] font-mono text-neutral-600 dark:text-neutral-400">
                v{decision.version}
              </span>
            )}
          </div>
          <p className="mt-1 text-[11.5px] text-neutral-500 dark:text-neutral-400">
            {decision?.proposal.consequences || 'Перед выполнением проверьте ответ и целевой статус.'}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onReanalyze}
            disabled={reanalyzing}
            className="shrink-0 rounded-md border border-neutral-200 px-2.5 py-1.5 text-[11px] font-semibold text-neutral-700 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800 cursor-pointer"
          >
            {reanalyzing ? 'Перерасчёт…' : 'Пересчитать'}
          </button>
        </div>
      </div>

      {/* 2. Stale Context Warning Banner */}
      {isStale && (
        <div className="border-b border-amber-200 bg-amber-50/90 px-4 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200 flex items-center justify-between gap-3">
          <span>
            ⚠️ <strong>Контекст заявки изменился в IntraService.</strong> Решение v{decision?.version || 1} устарело. Нажмите «Пересчитать» для актуализации без потери вашего ответа.
          </span>
          <button
            type="button"
            onClick={onReanalyze}
            disabled={reanalyzing}
            className="shrink-0 px-2 py-0.5 font-bold rounded bg-amber-600 hover:bg-amber-500 text-white text-[11px] cursor-pointer"
          >
            Обновить
          </button>
        </div>
      )}

      {/* 3. Pending Command Approval Banner (HitL) */}
      {pendingCommand && (
        <div className="border-b border-amber-300 bg-amber-100/70 dark:border-amber-800 dark:bg-amber-950/60 px-4 py-2.5 flex items-center justify-between gap-3">
          <div className="text-xs text-amber-950 dark:text-amber-100">
            <strong>Ожидает подтверждения:</strong> действие «{pendingCommand.action}». До вашего подтверждения команда не отправится.
          </div>
          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              disabled={busyRun}
              onClick={() => void handleDecideCommand('reject')}
              className="px-2.5 py-1 text-xs font-semibold rounded border border-amber-400 dark:border-amber-700 text-amber-900 dark:text-amber-200 hover:bg-white dark:hover:bg-neutral-800 cursor-pointer"
            >
              Отклонить
            </button>
            <button
              type="button"
              disabled={busyRun}
              onClick={() => void handleDecideCommand('approve')}
              className="px-2.5 py-1 text-xs font-semibold rounded bg-amber-600 hover:bg-amber-500 text-white shadow-xs cursor-pointer"
            >
              Подтвердить
            </button>
          </div>
        </div>
      )}

      {/* 4. Body: Sources, Mode, Target Status, Blockers */}
      <div className="space-y-3 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          {Object.entries(sources)
            .filter(([, active]) => active)
            .map(([source]) => (
              <span
                key={source}
                className="inline-flex items-center gap-1.5 rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] font-medium text-neutral-700 dark:border-neutral-700 dark:bg-neutral-850 dark:text-neutral-300"
              >
                {source === 'ai' ? <IconSparkles size={11} /> : <IconBolt size={11} />}
                {sourceLabels[source] || source}
              </span>
            ))}
          {ticketRun && (
            <span className="rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-[11px] font-medium text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300">
              {ticketRun.mode === 'autopilot' ? 'Автопилот' : 'Ручной режим'} · {runLabels[ticketRun.state] || ticketRun.state}
            </span>
          )}
          <span className="inline-flex items-center gap-1.5 rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] font-medium text-neutral-700 dark:border-neutral-700 dark:bg-neutral-850 dark:text-neutral-300">
            <span className={`h-1.5 w-1.5 rounded-full ${getStatusDotClass(targetStatusId)}`} />
            {targetStatusName}
            {selectedStatusOverride !== null && (
              <button
                type="button"
                onClick={onResetStatusOverride}
                title="Вернуть предложенный статус"
                className="ml-0.5 hover:text-rose-600 cursor-pointer"
              >
                <IconClose size={10} />
              </button>
            )}
          </span>

          {/* TicketRun Lifecycle Controls */}
          {ticketRun && !ticketRun.completed_at && (
            <div className="ml-auto flex items-center gap-1.5">
              <button
                type="button"
                disabled={busyRun}
                onClick={() =>
                  handleMutateRun(
                    () =>
                      controlTicketRun(
                        ticketRun.id,
                        'switch_mode',
                        ticketRun.version,
                        ticketRun.mode === 'manual' ? 'autopilot' : 'manual',
                      ),
                    ticketRun.mode === 'manual' ? 'Автопилот включён' : 'Переведено в ручной режим',
                  )
                }
                className="rounded border border-neutral-250 dark:border-neutral-700 px-2 py-0.5 text-[10.5px] font-medium text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800 cursor-pointer"
              >
                {ticketRun.mode === 'manual' ? 'В автопилот' : 'В ручной'}
              </button>
              <button
                type="button"
                disabled={busyRun}
                onClick={() =>
                  handleMutateRun(
                    () =>
                      controlTicketRun(
                        ticketRun.id,
                        ticketRun.state === 'paused' ? 'resume' : 'pause',
                        ticketRun.version,
                      ),
                    ticketRun.state === 'paused' ? 'Цикл возобновлён' : 'Цикл приостановлен',
                  )
                }
                className="rounded border border-neutral-250 dark:border-neutral-700 px-2 py-0.5 text-[10.5px] font-medium text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800 cursor-pointer"
              >
                {ticketRun.state === 'paused' ? 'Возобновить' : 'Пауза'}
              </button>
            </div>
          )}
          {!ticketRun && (
            <div className="ml-auto flex items-center gap-1.5">
              <button
                type="button"
                disabled={busyRun}
                onClick={() =>
                  handleMutateRun(() => startTicketRun(rawId, 'manual'), 'Ручной цикл создан')
                }
                className="rounded border border-neutral-250 dark:border-neutral-700 px-2 py-0.5 text-[10.5px] font-medium text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800 cursor-pointer"
              >
                Начать цикл
              </button>
            </div>
          )}
        </div>

        {/* Blocked or missing reasons */}
        {blockedReasons.length > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-[11.5px] text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            {blockedReasons.join(' · ')}
          </div>
        )}

        {/* Controls row: Tab triggers, AI reply insert, Minutes */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-neutral-100 pt-2.5 dark:border-neutral-800">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => handleToggleTab('evidence')}
              className={`rounded px-2.5 py-1 text-[11px] font-semibold transition-colors cursor-pointer ${
                activeTab === 'evidence'
                  ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                  : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-750'
              }`}
            >
              Основания
            </button>
            <button
              type="button"
              onClick={() => handleToggleTab('history')}
              className={`rounded px-2.5 py-1 text-[11px] font-semibold transition-colors cursor-pointer ${
                activeTab === 'history'
                  ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                  : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-750'
              }`}
            >
              История версий
            </button>
            {runEvents.length > 0 && (
              <button
                type="button"
                onClick={() => handleToggleTab('events')}
                className={`rounded px-2.5 py-1 text-[11px] font-semibold transition-colors cursor-pointer ${
                  activeTab === 'events'
                    ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                    : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-750'
                }`}
              >
                События цикла ({runEvents.length})
              </button>
            )}
          </div>

          <div className="flex items-center gap-3">
            {details?.ai_suggested_resolution && details.ai_suggested_resolution !== replyText && (
              <button
                type="button"
                onClick={() => onInsertAiSynthesis(details.ai_suggested_resolution!)}
                className="text-[11.5px] font-semibold text-blue-600 hover:text-blue-700 dark:text-blue-400 cursor-pointer"
              >
                Подставить AI-ответ
              </button>
            )}
            <label className="flex items-center gap-1.5 text-[11.5px] text-neutral-500">
              <span>Минуты</span>
              <input
                type="number"
                value={expenses}
                min={0}
                max={240}
                onChange={event => onChangeExpenses(Number(event.target.value))}
                className="h-7 w-14 rounded-md border border-neutral-200 bg-neutral-50 px-1.5 text-center font-mono text-xs font-semibold text-neutral-900 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"
              />
            </label>
          </div>
        </div>

        {/* 5. Tab Content: Evidence */}
        {activeTab === 'evidence' && (
          <div className="space-y-2.5 border-t border-neutral-100 pt-3 text-xs dark:border-neutral-800">
            {/* Context completeness */}
            {decision?.completeness && (
              <div className="rounded-lg bg-neutral-50 p-2.5 dark:bg-neutral-850">
                <div className="font-semibold text-neutral-800 dark:text-neutral-200">Полнота контекста</div>
                <div className="mt-1 text-[11.5px] text-neutral-600 dark:text-neutral-400 flex flex-wrap gap-3">
                  <span>
                    История: <strong>{decision.completeness.history_used ?? 0}</strong> из {decision.completeness.history_total ?? 0} сообщ.
                  </span>
                  <span>
                    Вложения: <strong>{decision.completeness.attachments_read ?? 0}</strong> из {decision.completeness.attachments_total ?? 0} прочит.
                  </span>
                </div>
                {decision.completeness.limitations?.map((lim, idx) => (
                  <div key={idx} className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
                    ℹ️ {lim}
                  </div>
                ))}
              </div>
            )}

            {/* Steps & components trace */}
            <div className="grid gap-2 sm:grid-cols-3">
              {(decision?.steps || []).map(step => (
                <div key={step.id} className="rounded-lg bg-neutral-50 p-2.5 dark:bg-neutral-850 border border-neutral-200/50 dark:border-neutral-800">
                  <div className="font-semibold text-neutral-800 dark:text-neutral-200">
                    {sourceLabels[step.component] || step.component}
                  </div>
                  <div className="mt-1 text-[11px] text-neutral-500 dark:text-neutral-400">
                    Статус: <span className="font-medium text-neutral-700 dark:text-neutral-300">{step.status}</span>
                    {step.error_code && <span className="text-rose-600 block">Ошибка: {step.error_code}</span>}
                    {step.duration_ms !== null && step.duration_ms !== undefined && (
                      <span className="block font-mono text-[10px] text-neutral-400">{step.duration_ms} мс</span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* AI synthesis metadata */}
            {details?.circuit && (
              <div className="rounded-lg bg-neutral-50 p-2.5 text-[11.5px] dark:bg-neutral-850 border border-neutral-200/50 dark:border-neutral-800 flex items-center justify-between">
                <span>
                  Контур безопасности: <strong className="uppercase">{details.circuit}</strong>
                  {details.circuit_reason && ` (${details.circuit_reason})`}
                </span>
                {details.requires_sanitization && (
                  <span className="text-amber-600 dark:text-amber-300 font-medium">Обезличивание включено</span>
                )}
              </div>
            )}
          </div>
        )}

        {/* 6. Tab Content: Decision Versions History */}
        {activeTab === 'history' && (
          <div className="border-t border-neutral-100 pt-3 text-xs dark:border-neutral-800 space-y-2">
            <div className="font-semibold text-neutral-800 dark:text-neutral-200 flex items-center justify-between">
              <span>История версий решения (#{rawId})</span>
              {loadingHistory && <span className="text-[11px] text-neutral-400 animate-pulse">Загрузка…</span>}
            </div>
            {decisionsHistory.length === 0 && !loadingHistory && (
              <div className="text-[11.5px] text-neutral-500 py-2">Предыдущих версий решений для этой заявки нет.</div>
            )}
            {decisionsHistory.map(item => (
              <div
                key={item.id}
                className="rounded-lg border border-neutral-200/70 p-2.5 text-[11.5px] dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/40"
              >
                <div className="flex items-center justify-between">
                  <span className="font-bold font-mono text-neutral-900 dark:text-neutral-100">
                    Версия v{item.version}
                  </span>
                  <span className="text-[10px] text-neutral-400">
                    {item.created_at ? new Date(item.created_at).toLocaleString('ru-RU') : ''}
                  </span>
                </div>
                <div className="mt-1 text-neutral-600 dark:text-neutral-300">
                  Предложение: <strong>{item.proposal.title || 'Решение'}</strong> ({item.proposal.status_name || `Статус #${item.proposal.status_id}`})
                </div>
                <div className="mt-0.5 text-[10.5px] text-neutral-400">
                  Создал: {item.created_by || 'система'} · Исход: {item.outcome}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 7. Tab Content: TicketRun Events */}
        {activeTab === 'events' && (
          <div className="border-t border-neutral-100 pt-3 text-xs dark:border-neutral-800 space-y-1.5">
            <div className="font-semibold text-neutral-800 dark:text-neutral-200">
              События цикла выполнения
            </div>
            {runEvents.length === 0 ? (
              <div className="text-[11.5px] text-neutral-500 py-2">Событий пока нет.</div>
            ) : (
              runEvents.slice(-10).reverse().map(event => (
                <div key={event.id} className="flex justify-between gap-3 text-[11px] text-neutral-500 py-1 border-b border-neutral-100/50 dark:border-neutral-800/50">
                  <span>
                    <strong className="text-neutral-700 dark:text-neutral-300">{event.event_type}</strong> · {event.actor}
                  </span>
                  <span className="font-mono text-[10px] text-neutral-400">
                    {event.created_at ? new Date(event.created_at).toLocaleTimeString('ru-RU') : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        )}

        {/* 8. Operator Quality Feedback Widget */}
        {decision && (
          <div className="border-t border-neutral-100 pt-2.5 dark:border-neutral-800 flex flex-wrap items-center justify-between gap-2 text-[11px]">
            <div className="flex items-center gap-1.5 text-neutral-500">
              <span>Оценка предложения:</span>
              {submittedVerdict ? (
                <span className="font-bold text-emerald-600 dark:text-emerald-400">
                  ✓ {submittedVerdict === 'correct' ? 'Корректно' : submittedVerdict === 'partial' ? 'Частично' : submittedVerdict === 'incorrect' ? 'Ошибка' : 'Мало данных'}
                </span>
              ) : (
                <div className="inline-flex items-center gap-1">
                  <button
                    type="button"
                    disabled={submittingFeedback}
                    onClick={() => void handleSendFeedback('correct')}
                    className="px-2 py-0.5 rounded border border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 font-medium cursor-pointer"
                  >
                    Верно
                  </button>
                  <button
                    type="button"
                    disabled={submittingFeedback}
                    onClick={() => void handleSendFeedback('partial')}
                    className="px-2 py-0.5 rounded border border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 font-medium cursor-pointer"
                  >
                    Частично
                  </button>
                  <button
                    type="button"
                    disabled={submittingFeedback}
                    onClick={() => setShowReasonPicker(prev => !prev)}
                    className="px-2 py-0.5 rounded border border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 font-medium text-rose-600 dark:text-rose-400 cursor-pointer"
                  >
                    Ошибка
                  </button>
                  <button
                    type="button"
                    disabled={submittingFeedback}
                    onClick={() => void handleSendFeedback('insufficient_data')}
                    className="px-2 py-0.5 rounded border border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 font-medium cursor-pointer"
                  >
                    Мало данных
                  </button>
                </div>
              )}
            </div>

            {showReasonPicker && !submittedVerdict && (
              <div className="w-full mt-2 p-2.5 rounded-lg border border-neutral-200 bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-850 space-y-2">
                <div className="font-semibold text-neutral-800 dark:text-neutral-200">Укажите причину ошибки:</div>
                <div className="flex gap-2">
                  <select
                    value={selectedReason}
                    onChange={e => setSelectedReason(e.target.value as DecisionReasonCode)}
                    className="flex-1 rounded border border-neutral-200 bg-white px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-900 text-neutral-800 dark:text-neutral-200"
                  >
                    <option value="">Выберите категорию…</option>
                    {Object.entries(reasonCodeLabels).map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    disabled={submittingFeedback || !selectedReason}
                    onClick={() => void handleSendFeedback('incorrect')}
                    className="px-3 py-1 bg-rose-600 hover:bg-rose-500 text-white font-semibold rounded text-xs disabled:opacity-50 cursor-pointer"
                  >
                    Сохранить
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
