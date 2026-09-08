import { useState, useEffect, useCallback, useRef } from 'react';
import type { Ticket } from '../../data/mock';
import type { TaskDetails } from '../../lib/types';
import {
  fetchTicketRun,
  controlTicketRun,
  ensureManualTicketRun,
  type TicketRun,
  type TicketRunEvent,
  type TicketRunCommand,
} from '../../lib/ticketRuns';
import { applyTask, reanalyzeTask, confirmExecutionJob } from '../../lib/tasks';
import { submitDecisionFeedback } from '../../lib/decisionsApi';
import {
  captureInitialDecisionVersion,
  isDecisionVersionStale,
  type InitialDecisionVersion,
} from './decisionStaleness';

export interface UseUnifiedDecisionProps {
  ticket: Ticket;
  details: TaskDetails | null;
  rawId: number;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  onClose?: () => void;
  onRefreshDetails?: () => Promise<void>;
}

export interface UseUnifiedDecisionReturn {
  // Изолированное состояние формы ответа
  replyText: string;
  setReplyText: (val: string) => void;
  replyMode: 'reply' | 'internal';
  setReplyMode: (mode: 'reply' | 'internal') => void;
  expenses: number;
  setExpenses: (val: number) => void;
  selectedTemplateKey: string;
  setSelectedTemplateKey: (key: string) => void;
  selectedStatusOverride: number | null;
  setSelectedStatusOverride: (statusId: number | null) => void;

  // Вкладки оснований
  selectedTab: 'rules' | 'rag' | 'ai' | 'diagnostics' | 'completeness';
  setSelectedTab: (tab: 'rules' | 'rag' | 'ai' | 'diagnostics' | 'completeness') => void;

  // Состояние жизненного цикла и защиты
  ticketRun: TicketRun | null;
  runEvents: TicketRunEvent[];
  pendingCommand: TicketRunCommand | null;
  isStale: boolean;
  submitting: boolean;
  reanalyzing: boolean;
  feedbackSubmitted: boolean;

  // Действия оператора
  handleApplyDecision: () => Promise<void>;
  handleCancelTicket: () => Promise<void>;
  handleTakeTicket: () => Promise<void>;
  handleReanalyze: () => Promise<void>;
  handleHitlApprove: () => Promise<void>;
  handleHitlReject: () => Promise<void>;
  handleTogglePauseRun: () => Promise<void>;
  handleSwitchRunMode: (mode: 'manual' | 'autopilot') => Promise<void>;
  handleSubmitFeedback: (
    verdict: 'correct' | 'partial' | 'incorrect' | 'insufficient_data',
    reasonCode?: string
  ) => Promise<void>;
  insertSnippet: (snippet: string) => void;
}

export function useUnifiedDecision({
  ticket,
  details,
  rawId,
  onUpdateTicket,
  onToast,
  onClose,
  onRefreshDetails,
}: UseUnifiedDecisionProps): UseUnifiedDecisionReturn {
  const getDraftKey = useCallback((id: number) => `intralink_draft_${id}`, []);

  // 1. Изолированный буфер черновика ответа (sessionStorage)
  const [replyText, setReplyTextState] = useState<string>(() => {
    if (!rawId) return '';
    const cached = sessionStorage.getItem(getDraftKey(rawId));
    if (cached !== null) return cached;
    return (
      details?.decision_envelope?.response_draft ||
      details?.ai_suggested_resolution ||
      ticket.aiPlan?.comment ||
      ticket.aiSuggestion ||
      ''
    );
  });

  const setReplyText = useCallback(
    (text: string) => {
      setReplyTextState(text);
      if (rawId) {
        sessionStorage.setItem(getDraftKey(rawId), text);
      }
    },
    [rawId, getDraftKey]
  );

  // При смене rawId загружаем черновик для новой заявки
  useEffect(() => {
    if (!rawId) return;
    const cached = sessionStorage.getItem(getDraftKey(rawId));
    if (cached !== null) {
      setReplyTextState(cached);
    } else {
      const init =
        details?.decision_envelope?.response_draft ||
        details?.ai_suggested_resolution ||
        ticket.aiPlan?.comment ||
        ticket.aiSuggestion ||
        '';
      setReplyTextState(init);
    }
  }, [rawId, getDraftKey]);

  // Подстановка единого черновика только если редактор был пуст
  useEffect(() => {
    const compiledDraft =
      details?.decision_envelope?.response_draft || details?.ai_suggested_resolution;
    if (compiledDraft && !replyText.trim()) {
      const text = compiledDraft;
      setReplyTextState(text);
      if (rawId) sessionStorage.setItem(getDraftKey(rawId), text);
    }
  }, [
    details?.decision_envelope?.response_draft,
    details?.ai_suggested_resolution,
    rawId,
    getDraftKey,
  ]);

  const [replyMode, setReplyMode] = useState<'reply' | 'internal'>('reply');
  const [expenses, setExpenses] = useState<number>(
    ticket.aiPlan?.expensesMinutes || ticket.expenses || 10
  );
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string>('');
  const [selectedStatusOverride, setSelectedStatusOverride] = useState<number | null>(null);
  const [selectedTab, setSelectedTab] = useState<
    'rules' | 'rag' | 'ai' | 'diagnostics' | 'completeness'
  >('rules');

  const [ticketRun, setTicketRun] = useState<TicketRun | null>(null);
  const [runEvents, setRunEvents] = useState<TicketRunEvent[]>([]);
  const [pendingCommand, setPendingCommand] = useState<TicketRunCommand | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);

  // 2. Staleness Guard
  const initialDecisionRef = useRef<InitialDecisionVersion | null>(null);
  const isStale = isDecisionVersionStale(
    Boolean((details?.readiness as any)?.stale),
    initialDecisionRef.current,
    rawId,
    details?.decision?.version,
  );

  useEffect(() => {
    initialDecisionRef.current = captureInitialDecisionVersion(
      initialDecisionRef.current,
      rawId,
      details?.decision?.task_id,
      details?.decision?.version,
    );
  }, [rawId, details?.decision?.task_id, details?.decision?.version]);

  // 3. Адаптивный дифференциальный поллинг TicketRun
  const loadRunState = useCallback(async () => {
    if (!rawId) return;
    try {
      const view = await fetchTicketRun(rawId);
      setTicketRun(view.run);
      setRunEvents(view.events || []);
      setPendingCommand(view.pending_command || null);
    } catch (err) {
      console.warn(`[TicketRun] Не удалось загрузить цикл для #${rawId}:`, err);
    }
  }, [rawId]);

  useEffect(() => {
    loadRunState();
  }, [loadRunState]);

  useEffect(() => {
    if (!rawId) return;
    // Опрашиваем с интервалом 3с ТОЛЬКО в переходных статусах
    const isTransitional =
      ticketRun?.state === 'running' || ticketRun?.state === 'pending';
    if (!isTransitional) return;

    const timer = setInterval(() => {
      loadRunState();
    }, 3000);

    return () => clearInterval(timer);
  }, [rawId, ticketRun?.state, loadRunState]);

  // 4. Действия оператора
  const insertSnippet = useCallback(
    (snippet: string) => {
      setReplyText(replyText ? `${replyText}\n\n${snippet}` : snippet);
    },
    [replyText, setReplyText]
  );

  const handleApplyDecision = useCallback(async () => {
    if (!rawId || submitting) return;
    setSubmitting(true);
    try {
      const decision = details?.decision;
      const targetStatusId =
        selectedStatusOverride ||
        decision?.proposal?.status_id ||
        (details?.suggested_action as any)?.status_id ||
        27;

      await applyTask(rawId, {
        status_id: targetStatusId,
        comment: replyText,
        minutes: expenses,
        is_private: replyMode === 'internal',
        decision_id: decision?.id,
        decision_version: decision?.version,
        ticket_run_id: ticketRun?.id,
      });

      // Очищаем локальный буфер черновика
      sessionStorage.removeItem(getDraftKey(rawId));

      onUpdateTicket(ticket.id, {
        status: targetStatusId === 29 || targetStatusId === 30 ? 'resolved' : 'in_progress',
        statusId: targetStatusId,
        expenses,
      });

      onToast({
        type: 'success',
        message: `Изменения по заявке #${rawId} сохранены`,
      });

      if (onClose) onClose();
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Не удалось сохранить изменения: ${err.message || err}`,
      });
    } finally {
      setSubmitting(false);
    }
  }, [
    rawId,
    submitting,
    details,
    selectedStatusOverride,
    replyText,
    expenses,
    replyMode,
    getDraftKey,
    onUpdateTicket,
    ticket.id,
    onToast,
    onClose,
  ]);

  const handleCancelTicket = useCallback(async () => {
    if (!rawId || submitting) return;
    setSubmitting(true);
    try {
      const cancelComment =
        replyText.trim() ||
        'Заявка отменена по решению инженера первой линии техподдержки.';

      await applyTask(rawId, {
        status_id: 30,
        comment: cancelComment,
        minutes: expenses || 5,
        is_private: false,
      });

      sessionStorage.removeItem(getDraftKey(rawId));
      onUpdateTicket(ticket.id, {
        status: 'resolved',
        statusId: 30,
      });

      onToast({
        type: 'info',
        message: `Заявка #${rawId} отменена`,
      });

      if (onClose) onClose();
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Ошибка отмены заявки: ${err.message || err}`,
      });
    } finally {
      setSubmitting(false);
    }
  }, [rawId, submitting, replyText, expenses, getDraftKey, onUpdateTicket, ticket.id, onToast, onClose]);

  const handleTakeTicket = useCallback(async () => {
    if (!rawId || submitting) return;
    setSubmitting(true);
    try {
      await applyTask(rawId, {
        status_id: 27,
        comment: replyText,
        minutes: expenses || 10,
        is_private: false,
      });
      sessionStorage.removeItem(getDraftKey(rawId));
      onUpdateTicket(ticket.id, {
        status: 'in_progress',
        statusId: 27,
      });
      onToast({
        type: 'success',
        message: `Заявка #${rawId} взята в работу`,
      });
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Ошибка взятия в работу: ${err.message || err}`,
      });
    } finally {
      setSubmitting(false);
    }
  }, [rawId, submitting, replyText, expenses, getDraftKey, onUpdateTicket, ticket.id, onToast]);

  const handleReanalyze = useCallback(async () => {
    if (!rawId || reanalyzing) return;
    setReanalyzing(true);
    try {
      const updated = await reanalyzeTask(rawId);
      if (onRefreshDetails) {
        await onRefreshDetails();
      }
      if (updated.ai_suggested_resolution && !replyText.trim()) {
        setReplyText(updated.ai_suggested_resolution);
      }
      initialDecisionRef.current = updated.decision?.version
        ? { taskId: rawId, version: updated.decision.version }
        : null;
      onToast({
        type: 'success',
        message: `Предложение по заявке #${rawId} обновлено по актуальным данным`,
      });
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Не удалось обновить предложение: ${err.message || err}`,
      });
    } finally {
      setReanalyzing(false);
    }
  }, [rawId, reanalyzing, onRefreshDetails, replyText, setReplyText, onToast]);

  const handleTogglePauseRun = useCallback(async () => {
    if (!ticketRun) return;
    try {
      const nextAction = ticketRun.state === 'paused' ? 'resume' : 'pause';
      const updated = await controlTicketRun(
        ticketRun.id,
        nextAction,
        ticketRun.version
      );
      setTicketRun(updated);
      onToast({
        type: 'info',
        message:
          nextAction === 'pause'
            ? `Цикл заявки #${rawId} приостановлен`
            : `Цикл заявки #${rawId} возобновлен`,
      });
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Не удалось изменить состояние цикла: ${err.message || err}`,
      });
    }
  }, [ticketRun, rawId, onToast]);

  const handleSwitchRunMode = useCallback(
    async (mode: 'manual' | 'autopilot') => {
      if (!ticketRun) {
        if (mode === 'manual') {
          const run = await ensureManualTicketRun(rawId);
          setTicketRun(run);
        }
        return;
      }
      try {
        const updated = await controlTicketRun(
          ticketRun.id,
          'switch_mode',
          ticketRun.version,
          mode
        );
        setTicketRun(updated);
        onToast({
          type: 'info',
          message: `Режим цикла переключён на «${mode === 'autopilot' ? 'Автопилот' : 'Помощник оператора'}»`,
        });
      } catch (err: any) {
        onToast({
          type: 'error',
          message: `Ошибка переключения режима: ${err.message || err}`,
        });
      }
    },
    [ticketRun, rawId, onToast]
  );

  const handleHitlApprove = useCallback(async () => {
    if (!pendingCommand) return;
    try {
      await confirmExecutionJob(pendingCommand.command_id, 'approve');
      onToast({
        type: 'success',
        message: `Действие автопилота «${pendingCommand.action}» одобрено`,
      });
      await loadRunState();
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Ошибка одобрения действия: ${err.message || err}`,
      });
    }
  }, [pendingCommand, onToast, loadRunState]);

  const handleHitlReject = useCallback(async () => {
    if (!pendingCommand || !ticketRun) return;
    try {
      await confirmExecutionJob(pendingCommand.command_id, 'reject');
      await controlTicketRun(ticketRun.id, 'pause', ticketRun.version);
      onToast({
        type: 'warning',
        message: `Действие автопилота отклонено. Цикл переведён в режим паузы.`,
      });
      await loadRunState();
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Ошибка отклонения действия: ${err.message || err}`,
      });
    }
  }, [pendingCommand, ticketRun, onToast, loadRunState]);

  const handleSubmitFeedback = useCallback(
    async (
      verdict: 'correct' | 'partial' | 'incorrect' | 'insufficient_data',
      reasonCode?: string
    ) => {
      const decisionId = details?.decision?.id;
      if (!decisionId) {
        onToast({
          type: 'warning',
          message: 'Решение ещё не зафиксировано на сервере для оценки',
        });
        return;
      }
      try {
        await submitDecisionFeedback(decisionId, {
          verdict,
          reason_code: reasonCode,
        });
        setFeedbackSubmitted(true);
        onToast({
          type: 'success',
          message: 'Оценка качества решения сохранена в журнале аудита',
        });
      } catch (err: any) {
        onToast({
          type: 'error',
          message: `Не удалось сохранить оценку: ${err.message || err}`,
        });
      }
    },
    [details?.decision?.id, onToast]
  );

  return {
    replyText,
    setReplyText,
    replyMode,
    setReplyMode,
    expenses,
    setExpenses,
    selectedTemplateKey,
    setSelectedTemplateKey,
    selectedStatusOverride,
    setSelectedStatusOverride,
    selectedTab,
    setSelectedTab,
    ticketRun,
    runEvents,
    pendingCommand,
    isStale,
    submitting,
    reanalyzing,
    feedbackSubmitted,
    handleApplyDecision,
    handleCancelTicket,
    handleTakeTicket,
    handleReanalyze,
    handleHitlApprove,
    handleHitlReject,
    handleTogglePauseRun,
    handleSwitchRunMode,
    handleSubmitFeedback,
    insertSnippet,
  };
}
