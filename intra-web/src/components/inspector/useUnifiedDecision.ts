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
import { analyzeTask, applyTask, reanalyzeTask, confirmExecutionJob } from '../../lib/tasks';
import { submitDecisionFeedback, overrideTaskFacts } from '../../lib/decisionsApi';
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
  onRefreshDetails?: (updated?: TaskDetails) => Promise<void> | void;
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
  selectedTab: 'facts' | 'completeness';
  setSelectedTab: (tab: 'facts' | 'completeness') => void;

  // Состояние жизненного цикла и защиты
  ticketRun: TicketRun | null;
  runEvents: TicketRunEvent[];
  pendingCommand: TicketRunCommand | null;
  isStale: boolean;
  submitting: boolean;
  reanalyzing: boolean;
  feedbackSubmitted: boolean;
  setFeedbackSubmitted: (val: boolean) => void;
  pendingNewAiDraft: string | null;
  applyNewDraft: () => void;
  dismissNewDraft: () => void;

  // Резолюция статуса и готовности
  targetStatusId: number;
  targetStatusName: string;
  primaryActionLabel: string;
  requiresComment: boolean;
  commentMissing: boolean;
  actionUnavailable: boolean;
  isPrivateCloseBlocked: boolean;
  isReady: boolean;

  // Действия оператора
  handleApplyDecision: () => Promise<void>;
  handleCancelTicket: () => Promise<void>;
  handleTakeTicket: () => Promise<void>;
  handleReanalyze: () => Promise<void>;
  handleAnalyze: () => Promise<void>;
  handleOverrideFacts: (facts: Record<string, any>) => Promise<boolean>;
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
  // Валидация принадлежности объекта details текущей заявке rawId
  const isDetailsForCurrentTask = Boolean(
    details &&
      (details.id === rawId ||
        (details as any).task?.id === rawId ||
        (details as any).task?.Id === rawId)
  );
  const safeDetails = isDetailsForCurrentTask ? details : null;

  const [replyMode, setReplyModeState] = useState<'reply' | 'internal'>('reply');

  const getDraftKey = useCallback(
    (id: number, mode: 'reply' | 'internal' = 'reply', version?: number | null) => {
      const v = version ?? safeDetails?.decision_envelope?.decision_version ?? 1;
      return `intralink_draft_${id}_v${v}_${mode}`;
    },
    [safeDetails?.decision_envelope?.decision_version]
  );

  // 1. Изолированный буфер черновика ответа (sessionStorage)
  const [replyText, setReplyTextState] = useState<string>(() => {
    if (!rawId) return '';
    const cached = sessionStorage.getItem(getDraftKey(rawId, 'reply'));
    if (cached !== null) return cached;
    return (
      safeDetails?.decision_envelope?.response?.text ||
      ticket.aiPlan?.comment ||
      ticket.aiSuggestion ||
      ''
    );
  });

  const setReplyMode = useCallback(
    (newMode: 'reply' | 'internal') => {
      if (newMode === replyMode) return;
      if (rawId) {
        sessionStorage.setItem(getDraftKey(rawId, replyMode), replyText);
      }
      setReplyModeState(newMode);
      if (rawId) {
        const cached = sessionStorage.getItem(getDraftKey(rawId, newMode));
        if (cached !== null) {
          setReplyTextState(cached);
        } else if (newMode === 'internal') {
          setReplyTextState(safeDetails?.decision_envelope?.internal_summary || '');
        } else {
          const init =
            safeDetails?.decision_envelope?.response?.text ||
            ticket.aiPlan?.comment ||
            ticket.aiSuggestion ||
            '';
          setReplyTextState(init);
        }
      }
    },
    [replyMode, rawId, getDraftKey, replyText, safeDetails, ticket.aiPlan?.comment, ticket.aiSuggestion]
  );

  const setReplyText = useCallback(
    (text: string) => {
      setReplyTextState(text);
      if (rawId) {
        sessionStorage.setItem(getDraftKey(rawId, replyMode), text);
      }
    },
    [rawId, replyMode, getDraftKey]
  );

  // Сброс сопутствующих состояний и загрузка изолированного черновика при смене rawId
  useEffect(() => {
    setSelectedTemplateKey('');
    setSelectedStatusOverride(null);
    setPendingNewAiDraft(null);
    setFeedbackSubmitted(false);
    setExpenses(ticket.aiPlan?.expensesMinutes || ticket.expenses || 10);
    initialDecisionRef.current = null;

    if (!rawId) return;
    setReplyModeState('reply');
    const cached = sessionStorage.getItem(getDraftKey(rawId, 'reply'));
    if (cached !== null) {
      setReplyTextState(cached);
    } else {
      const init =
        safeDetails?.decision_envelope?.response?.text ||
        ticket.aiPlan?.comment ||
        ticket.aiSuggestion ||
        '';
      setReplyTextState(init);
    }
  }, [
    rawId,
    getDraftKey,
    safeDetails,
    ticket.aiPlan?.comment,
    ticket.aiSuggestion,
    ticket.aiPlan?.expensesMinutes,
    ticket.expenses,
  ]);

  // Подстановка единого черновика, когда safeDetails подгружаются асинхронно
  useEffect(() => {
    if (!safeDetails) return;
    if (replyMode === 'reply') {
      const compiledDraft = safeDetails.decision_envelope?.response?.text;
      if (!compiledDraft) return;

      const cached = sessionStorage.getItem(getDraftKey(rawId, 'reply'));
      if (cached !== null) return;

      const fallbackComment = (ticket.aiPlan?.comment || ticket.aiSuggestion || '').trim();
      if (!replyText.trim() || replyText.trim() === fallbackComment) {
        setReplyTextState(compiledDraft);
      }
    }
  }, [
    safeDetails?.decision_envelope?.response?.text,
    rawId,
    getDraftKey,
    replyMode,
    replyText,
    ticket.aiPlan?.comment,
    ticket.aiSuggestion,
  ]);
  const [expenses, setExpenses] = useState<number>(
    ticket.aiPlan?.expensesMinutes || ticket.expenses || 10
  );
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string>('');
  const [selectedStatusOverride, setSelectedStatusOverride] = useState<number | null>(null);
  const [selectedTab, setSelectedTab] = useState<'facts' | 'completeness'>('facts');

  const [ticketRun, setTicketRun] = useState<TicketRun | null>(null);
  const [runEvents, setRunEvents] = useState<TicketRunEvent[]>([]);
  const [pendingCommand, setPendingCommand] = useState<TicketRunCommand | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [pendingNewAiDraft, setPendingNewAiDraft] = useState<string | null>(null);
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

  // 4. Действия оператора: вставка сниппета с защитой от накопления дубликатов
  const insertSnippet = useCallback(
    (snippet: string) => {
      if (!snippet || !snippet.trim()) return;
      const cleanSnippet = snippet.trim();
      if (replyText.includes(cleanSnippet)) {
        onToast({ type: 'info', message: 'Этот фрагмент уже присутствует в тексте ответа' });
        return;
      }
      const separator = replyText.trim() ? '\n\n' : '';
      setReplyText(replyText ? `${replyText.trim()}${separator}${cleanSnippet}` : cleanSnippet);
      onToast({ type: 'success', message: 'Фрагмент добавлен в текст ответа' });
    },
    [replyText, setReplyText, onToast]
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

      if (targetStatusId === 29 && replyMode === 'internal') {
        onToast({
          type: 'error',
          message:
            'Закрытие заявки (статус 29 «Выполнена») запрещено со скрытым комментарием. Переключите режим на «Ответ заявителю».',
        });
        return;
      }

      await applyTask(rawId, {
        status_id: targetStatusId,
        comment: replyText,
        minutes: expenses,
        is_private: replyMode === 'internal',
        decision_id: decision?.id,
        decision_version: decision?.version,
        ticket_run_id: ticketRun?.id,
      });

      // Очищаем локальные буферы черновиков
      sessionStorage.removeItem(getDraftKey(rawId, 'reply'));
      sessionStorage.removeItem(getDraftKey(rawId, 'internal'));

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
    ticketRun?.id,
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

      sessionStorage.removeItem(getDraftKey(rawId, 'reply'));
      sessionStorage.removeItem(getDraftKey(rawId, 'internal'));
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
      sessionStorage.removeItem(getDraftKey(rawId, 'reply'));
      sessionStorage.removeItem(getDraftKey(rawId, 'internal'));
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
        await onRefreshDetails(updated);
      }
      const newDraft = updated.decision_envelope?.response?.text;
      if (newDraft) {
        if (!replyText.trim()) {
          setReplyText(newDraft);
        } else if (newDraft.trim() !== replyText.trim()) {
          setPendingNewAiDraft(newDraft);
        }
      }
      initialDecisionRef.current = updated.decision?.version
        ? { taskId: rawId, version: updated.decision.version }
        : null;
      onToast({
        type: 'success',
        message: `Заявка #${rawId} проанализирована повторно`,
      });
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Не удалось выполнить повторный анализ: ${err.message || err}`,
      });
    } finally {
      setReanalyzing(false);
    }
  }, [rawId, reanalyzing, onRefreshDetails, replyText, setReplyText, onToast]);

  const handleAnalyze = useCallback(async () => {
    if (!rawId || reanalyzing) return;
    setReanalyzing(true);
    try {
      const updated = await analyzeTask(rawId);
      if (onRefreshDetails) {
        await onRefreshDetails(updated);
      }
      const newDraft = updated.decision_envelope?.response?.text;
      if (newDraft) {
        if (!replyText.trim()) {
          setReplyText(newDraft);
        } else if (newDraft.trim() !== replyText.trim()) {
          setPendingNewAiDraft(newDraft);
        }
      }
      initialDecisionRef.current = updated.decision?.version
        ? { taskId: rawId, version: updated.decision.version }
        : null;
      onToast({ type: 'success', message: `Заявка #${rawId} проанализирована` });
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Не удалось выполнить анализ: ${err.message || err}`,
      });
    } finally {
      setReanalyzing(false);
    }
  }, [rawId, reanalyzing, onRefreshDetails, replyText, setReplyText, onToast]);

  // Ручная корректировка фактов сценария (Fact-Override)
  const handleOverrideFacts = useCallback(
    async (facts: Record<string, any>): Promise<boolean> => {
      if (!rawId) return false;
      setSubmitting(true);
      try {
        const expectedVersion =
          details?.decision_envelope?.decision_version || details?.decision?.version;
        const res = await overrideTaskFacts(rawId, {
          expected_decision_version: expectedVersion,
          facts,
          current_draft_text: replyText,
        });

        if (res.decision_envelope?.response?.text && !replyText.trim()) {
          setReplyText(res.decision_envelope.response.text);
        }

        if (onRefreshDetails) {
          await onRefreshDetails();
        }
        await loadRunState();

        onToast({
          type: 'success',
          message: `Факты сценария обновлены. Сформировано решение #${res.decision_envelope.decision_version}.`,
        });
        return true;
      } catch (err: any) {
        onToast({
          type: 'error',
          message: `Ошибка корректировки фактов: ${err.message || err}`,
        });
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [rawId, details, replyText, setReplyText, onRefreshDetails, loadRunState, onToast]
  );

  const applyNewDraft = useCallback(() => {
    if (pendingNewAiDraft) {
      setReplyText(pendingNewAiDraft);
      setPendingNewAiDraft(null);
      if (rawId) {
        sessionStorage.setItem(getDraftKey(rawId, replyMode), pendingNewAiDraft);
      }
    }
  }, [pendingNewAiDraft, rawId, getDraftKey, replyMode, setReplyText]);

  const dismissNewDraft = useCallback(() => {
    setPendingNewAiDraft(null);
  }, []);

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

  // Резолюция параметров действия и валидации
  const decision = safeDetails?.decision;
  const envelope = safeDetails?.decision_envelope;
  const envelopePolicy = envelope?.policy || {};
  const proposal = decision?.proposal;
  const analysis = safeDetails?.analysis;
  const hasAnalysisResult = analysis?.has_result ?? Boolean(decision);

  const readiness =
    safeDetails?.readiness ||
    (decision?.completeness
      ? {
          ready: decision.completeness.complete,
          blocked_reasons: decision.completeness.blocked_reasons || [],
          missing_data: decision.completeness.missing_data || [],
          stale: false,
        }
      : undefined);

  const readinessIsCurrent = analysis
    ? analysis.freshness === 'current'
    : !(readiness?.stale || isStale);
  const isReady = hasAnalysisResult && (readiness?.ready ?? true) && readinessIsCurrent;

  const targetStatusId =
    selectedStatusOverride ||
    envelopePolicy.status_id ||
    proposal?.status_id ||
    (safeDetails?.suggested_action as any)?.status_id ||
    27;

  const statusNameMap: Record<number, string> = {
    27: 'В работе',
    29: 'Выполнена',
    30: 'Отменена',
    35: 'Требует уточнения',
    48: 'Ожидание устройства',
  };

  const targetStatusName =
    statusNameMap[targetStatusId] ||
    proposal?.status_name ||
    envelopePolicy.status_name ||
    (safeDetails?.suggested_action as any)?.status_name ||
    'В работе';

  const actionPolicy = safeDetails?.ai_suggestion?.policy || decision?.policy;
  const policyBlocked = Boolean(
    (actionPolicy as any)?.blocked || (actionPolicy as any)?.allowed === false
  );

  const primaryActionLabel = pendingCommand
    ? 'Подтвердить команду'
    : targetStatusId === 27
    ? 'Перевести в статус «В работе»'
    : targetStatusId === 29
    ? 'Отметить заявку выполненной'
    : targetStatusId === 30
    ? 'Отменить заявку'
    : targetStatusId === 35
    ? 'Запросить уточнение'
    : targetStatusId === 48
    ? 'Перевести в «Ожидание устройства»'
    : `Перевести в статус «${targetStatusName}»`;

  const requiresComment = [29, 30, 35, 48].includes(targetStatusId);
  const commentMissing = requiresComment && !replyText.trim();
  const isPrivateCloseBlocked = targetStatusId === 29 && replyMode === 'internal';
  const actionUnavailable =
    !pendingCommand && (!isReady || policyBlocked || commentMissing || isPrivateCloseBlocked);

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
    targetStatusId,
    targetStatusName,
    primaryActionLabel,
    requiresComment,
    commentMissing,
    isPrivateCloseBlocked,
    isReady,
    actionUnavailable,
    ticketRun,
    runEvents,
    pendingCommand,
    isStale,
    submitting,
    reanalyzing,
    feedbackSubmitted,
    setFeedbackSubmitted,
    pendingNewAiDraft,
    applyNewDraft,
    dismissNewDraft,
    handleApplyDecision,
    handleCancelTicket,
    handleTakeTicket,
    handleReanalyze,
    handleAnalyze,
    handleOverrideFacts,
    handleHitlApprove,
    handleHitlReject,
    handleTogglePauseRun,
    handleSwitchRunMode,
    handleSubmitFeedback,
    insertSnippet,
  };
}
