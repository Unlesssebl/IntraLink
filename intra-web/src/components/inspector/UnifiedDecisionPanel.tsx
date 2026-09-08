import { useState, useRef, useEffect } from 'react';
import type { Ticket } from '../../data/mock';
import type { TaskDetails, TicketSummaryResult, RAGMatchItem } from '../../lib/types';
import {
  IconPlay,
  IconPause,
  IconCheckCircle,
  IconAlertCircle,
  IconFileCode,
  IconServer,
  IconDatabase,
  IconSparkles,
  IconChevronDown,
  IconChevronRight,
  IconBolt,
  IconCheck,
  IconPencil,
} from '../Icons';
import {
  useUnifiedDecision,
  type UseUnifiedDecisionReturn,
} from './useUnifiedDecision';
import FactBagSection from './FactBagSection';
import FactOverrideModal from './FactOverrideModal';

interface TemplateItem {
  key: string;
  name: string;
  template?: string;
  minutes?: number;
  status_id?: number;
}

interface UnifiedDecisionPanelProps {
  ticket: Ticket;
  details: TaskDetails | null;
  rawId: number;
  templates: TemplateItem[];
  aiSummary: TicketSummaryResult | null;
  loadingAiSummary: boolean;
  onGenerateAiSummary: () => void;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  onClose?: () => void;
  onRefreshDetails?: (updated?: TaskDetails) => Promise<void> | void;
  diagStatus?: Record<string, 'idle' | 'checking' | 'ok' | 'fail'>;
}

const runStateConfig: Record<
  string,
  { label: string; dotClass: string; bgClass: string; textClass: string }
> = {
  running: {
    label: 'Выполняется',
    dotClass: 'bg-blue-500 animate-pulse',
    bgClass: 'bg-blue-50 dark:bg-blue-950/40 border-blue-200 dark:border-blue-900',
    textClass: 'text-blue-700 dark:text-blue-300',
  },
  waiting_approval: {
    label: 'Ожидает согласования',
    dotClass: 'bg-amber-500 animate-pulse',
    bgClass: 'bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-900',
    textClass: 'text-amber-700 dark:text-amber-300',
  },
  waiting_answer: {
    label: 'Ожидает ответа',
    dotClass: 'bg-sky-500',
    bgClass: 'bg-sky-50 dark:bg-sky-950/40 border-sky-200 dark:border-sky-900',
    textClass: 'text-sky-700 dark:text-sky-300',
  },
  paused: {
    label: 'Приостановлен',
    dotClass: 'bg-amber-600',
    bgClass: 'bg-amber-50/80 dark:bg-amber-950/30 border-amber-300 dark:border-amber-800',
    textClass: 'text-amber-800 dark:text-amber-200',
  },
  system_error: {
    label: 'Системная ошибка',
    dotClass: 'bg-rose-500',
    bgClass: 'bg-rose-50 dark:bg-rose-950/40 border-rose-200 dark:border-rose-900',
    textClass: 'text-rose-700 dark:text-rose-300',
  },
  completed: {
    label: 'Завершён',
    dotClass: 'bg-emerald-500',
    bgClass: 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-900',
    textClass: 'text-emerald-700 dark:text-emerald-300',
  },
  pending: {
    label: 'Ожидает запуска',
    dotClass: 'bg-neutral-400',
    bgClass: 'bg-neutral-100 dark:bg-neutral-800 border-neutral-200 dark:border-neutral-700',
    textClass: 'text-neutral-700 dark:text-neutral-300',
  },
};

export default function UnifiedDecisionPanel({
  ticket,
  details,
  rawId,
  templates,
  aiSummary,
  loadingAiSummary,
  onGenerateAiSummary,
  onUpdateTicket,
  onToast,
  onClose,
  onRefreshDetails,
  diagStatus,
}: UnifiedDecisionPanelProps) {
  const {
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
    handleTakeTicket,
    handleAnalyze,
    handleReanalyze,
    handleOverrideFacts,
    handleHitlApprove,
    handleHitlReject,
    handleTogglePauseRun,
    handleSwitchRunMode,
    handleSubmitFeedback,
    insertSnippet,
  } = useUnifiedDecision({
    ticket,
    details,
    rawId,
    onUpdateTicket,
    onToast,
    onClose,
    onRefreshDetails,
  });

  const [isAlternativesOpen, setIsAlternativesOpen] = useState(false);
  const [isOverrideModalOpen, setIsOverrideModalOpen] = useState(false);
  const [selectedVerdict, setSelectedVerdict] = useState<
    'correct' | 'partial' | 'incorrect' | 'insufficient_data' | null
  >(null);
  const [reasonCode, setReasonCode] = useState<string>('wrong_context');
  const [showTechnicalAudit, setShowTechnicalAudit] = useState(false);
  const alternativesMenuRef = useRef<HTMLDivElement>(null);

  // Закрытие меню альтернатив при клике вне
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        alternativesMenuRef.current &&
        !alternativesMenuRef.current.contains(event.target as Node)
      ) {
        setIsAlternativesOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // 1. Вычисление параметров решения и Cold-Start
  const decision = details?.decision;
  const envelope = details?.decision_envelope;
  const envelopeOutcome = envelope?.outcome || {};
  const envelopePolicy = envelope?.policy || {};
  const proposal = decision?.proposal;
  const analysis = details?.analysis;
  const hasAnalysisResult = analysis?.has_result ?? Boolean(decision);
  const readiness =
    details?.readiness ||
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
  const blockedReasons: string[] = Array.from(
    new Set([
      ...(readiness?.blocked_reasons || []),
      ...(analysis?.blocked_reason ? [analysis.blocked_reason] : []),
    ])
  );
  const missingData: string[] =
    envelopeOutcome.missing_fields || readiness?.missing_data || [];
  const sources = details?.sources || decision?.sources || {
    rule: envelope
      ? envelope.candidates.some((candidate) => candidate.source === 'rule')
      : Boolean(details?.suggested_action),
    rag: envelope
      ? envelope.candidates.some((candidate) => candidate.source === 'rag')
      : Boolean(details?.kb_matches && details.kb_matches.length > 0),
    ai: Boolean(envelope ? envelope.candidates.length > 1 : details?.ai_suggested_resolution),
  };

  // Оценка риска и обоснований
  const riskLevel =
    envelopeOutcome.risk_level ||
    envelopePolicy.risk_level ||
    proposal?.risk_level ||
    details?.suggested_action?.risk_level ||
    'normal';
  const riskWarning = proposal?.risk_warning || details?.suggested_action?.risk_warning || null;
  const triggerMarkers: string[] = proposal?.trigger_markers || details?.suggested_action?.trigger_markers || [];
  const ruleReason: string | undefined = details?.suggested_action?.reason;
  const isCriticalRisk = riskLevel === 'critical' || Boolean(riskWarning);

  // Хелпер человекочитаемого действия (устранение apply_triage)
  const formatFriendlyAction = (
    action?: string,
    targetStatus?: string,
    suggestedAction?: { name?: string; rule_type?: string }
  ): string => {
    if (suggestedAction?.name) return suggestedAction.name;
    if (suggestedAction?.rule_type === 'duplicate_task') return 'Отмена дубликата';
    if (suggestedAction?.rule_type === 'service_redirect') return 'Перенаправление в другой раздел';
    if (action === 'grant_wlan') return 'Предоставление доступа к Wi-Fi';
    if (action === 'create_user') return 'Создание учетной записи';
    if (action === 'install_printer') return 'Установка принтера';
    if (targetStatus === 'Отменена') return 'Отмена заявки (регламент)';
    if (targetStatus === 'Выполнена') return 'Регламентное закрытие заявки';
    if (targetStatus === 'В работе') return 'Взятие в работу 1-й линией';
    return 'Обработка 1-й линией';
  };

  const targetStatusId =
    selectedStatusOverride ||
    envelopePolicy.status_id ||
    proposal?.status_id ||
    details?.suggested_action?.status_id ||
    27;

  const targetStatusName =
    selectedStatusOverride === 27
      ? 'В работе'
      : selectedStatusOverride === 29
      ? 'Выполнена'
      : selectedStatusOverride === 35
      ? 'Требует уточнения'
      : selectedStatusOverride === 30
      ? 'Отменена'
      : selectedStatusOverride === 48
      ? 'Ожидание поставки'
      : proposal?.status_name ||
        envelopePolicy.status_name ||
        details?.suggested_action?.status_name ||
        'В работе';

  const actionPolicy = details?.ai_suggestion?.policy || decision?.policy;
  const policyBlocked = Boolean(
    (actionPolicy as any)?.blocked || (actionPolicy as any)?.allowed === false
  );

  // Предлагаемое действие
  const actionTitle =
    (envelopeOutcome.action
      ? formatFriendlyAction(envelopeOutcome.action, targetStatusName, details?.suggested_action)
      : undefined) ||
    proposal?.title ||
    details?.suggested_action?.name ||
    formatFriendlyAction(proposal?.action, targetStatusName, details?.suggested_action);

  const primaryActionLabel =
    pendingCommand
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
      ? 'Перевести в «Ожидание поставки»'
      : `Перевести в статус «${targetStatusName}»`;

  const requiresComment = [29, 30, 35].includes(targetStatusId);
  const commentMissing = requiresComment && !replyText.trim();
  const actionUnavailable = !pendingCommand && (!isReady || policyBlocked || commentMissing);
  const commandLabel = pendingCommand?.action || proposal?.action || null;
  const consequences =
    proposal?.consequences ||
    `Статус заявки изменится на «${targetStatusName}»${
      replyText.trim() ? `, комментарий будет ${replyMode === 'internal' ? 'служебным' : 'доступен заявителю'}` : ''
    }.`;
  const successCriterion =
    targetStatusId === 29
      ? 'Результат работ подтверждён до перевода заявки в выполненные.'
      : pendingCommand
      ? 'Команда завершилась с проверенным результатом, отражённым в истории цикла.'
      : 'Изменения подтверждены IntraService и появились в истории заявки.';

  const hasSnippets = Boolean(
    envelope?.response_draft ||
      details?.ai_suggested_resolution ||
      (details?.kb_matches && details.kb_matches.length > 0 && details.kb_matches[0]?.solution)
  );

  const diagnosticItems = [
    { key: 'ping', label: 'PING', success: 'ONLINE', failure: 'OFFLINE' },
    { key: 'smb', label: 'SMB:445', success: 'OPEN', failure: 'CLOSED' },
    { key: 'winrm', label: 'WINRM:5985', success: 'OPEN', failure: 'CLOSED' },
  ].map((item) => {
    const status = diagStatus?.[item.key] || 'idle';
    return {
      ...item,
      value:
        status === 'ok'
          ? item.success
          : status === 'fail'
          ? item.failure
          : status === 'checking'
          ? 'CHECKING'
          : 'NOT CHECKED',
      valueClass:
        status === 'ok'
          ? 'text-emerald-600 dark:text-emerald-400'
          : status === 'fail'
          ? 'text-rose-600 dark:text-rose-400'
          : status === 'checking'
          ? 'text-blue-600 dark:text-blue-400'
          : 'text-neutral-400 dark:text-neutral-500',
    };
  });

  // Статус TicketRun
  const currentRunState = ticketRun?.state || 'pending';
  const runCfg = runStateConfig[currentRunState] || runStateConfig.pending;

  // Обработчик выбора регламентного шаблона
  const handleSelectTemplate = (key: string) => {
    setSelectedTemplateKey(key);
    const tmpl = templates.find((t) => t.key === key);
    if (tmpl) {
      if (tmpl.template) setReplyText(tmpl.template);
      if (tmpl.minutes) setExpenses(tmpl.minutes);
      if (tmpl.status_id) setSelectedStatusOverride(tmpl.status_id);
    }
  };

  return (
    <div className="border border-neutral-200 dark:border-neutral-800 rounded-2xl bg-white dark:bg-neutral-900 shadow-sm overflow-hidden flex flex-col space-y-3 p-4">
      {/* ========================================================================= */}
      {/* 1. HERO HEADER: Режим, Статус TicketRun, Действие и Главная кнопка       */}
      {/* ========================================================================= */}
      <div className="space-y-3 pb-3 border-b border-neutral-100 dark:border-neutral-800">
        {/* Верхняя строка: Режим автопилота/ручной и статус выполнения */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            {/* Pill переключатель режима */}
            <div className="flex bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded-lg border border-neutral-200 dark:border-neutral-700 text-[11px] font-semibold">
              <button
                type="button"
                onClick={() => handleSwitchRunMode('manual')}
                className={`min-h-9 px-2.5 rounded-md outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  ticketRun?.mode !== 'autopilot'
                    ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                    : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
                }`}
              >
                Помощник оператора
              </button>
              <button
                type="button"
                onClick={() => handleSwitchRunMode('autopilot')}
                className={`min-h-9 px-2.5 rounded-md outline-none transition-colors flex items-center gap-1 focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  ticketRun?.mode === 'autopilot'
                    ? 'bg-blue-600 text-white shadow-2xs'
                    : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
                }`}
              >
                <IconBolt size={12} />
                <span>Автопилот</span>
              </button>
            </div>

            {/* Статус-бейдж TicketRun */}
            <div
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[11.5px] font-medium ${runCfg.bgClass} ${runCfg.textClass}`}
              title={`Состояние цикла: ${runCfg.label}`}
            >
              <span className={`w-2 h-2 rounded-full shrink-0 ${runCfg.dotClass}`} />
              <span>Цикл: {runCfg.label}</span>
            </div>
          </div>

          {/* Кнопка управления циклом (Пауза / Плей) */}
          {ticketRun && (
            <button
              type="button"
              onClick={handleTogglePauseRun}
              className="inline-flex min-h-9 items-center gap-1 px-2.5 text-[11.5px] font-medium text-neutral-600 dark:text-neutral-300 bg-neutral-50 dark:bg-neutral-800/80 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg border border-neutral-200 dark:border-neutral-700 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500"
              title={ticketRun.state === 'paused' ? 'Возобновить цикл' : 'Приостановить цикл'}
            >
              {ticketRun.state === 'paused' ? (
                <>
                  <IconPlay size={12} />
                  <span>Возобновить</span>
                </>
              ) : (
                <>
                  <IconPause size={12} />
                  <span>Приостановить</span>
                </>
              )}
            </button>
          )}
        </div>

        {envelope && (
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-neutral-500 dark:text-neutral-400">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="font-mono text-neutral-700 dark:text-neutral-300">
                {envelope.scenario_key}@{envelope.scenario_version}
              </span>
              <span>Факты: #{envelope.facts_revision}</span>
              <span>Доказательств: {envelope.evidence_refs.length}</span>
              <span>Уверенность: {Math.round(envelope.confidence * 100)}%</span>
              {Object.entries(envelope.facts_summary)
                .filter(([, fact]) => fact.state !== 'valid')
                .map(([key, fact]) => (
                  <span
                    key={key}
                    className="rounded-md border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
                  >
                    {key}: {fact.state}
                  </span>
                ))}
            </div>

            <button
              type="button"
              onClick={() => setIsOverrideModalOpen(true)}
              className="inline-flex items-center gap-1 text-[11px] font-semibold text-blue-600 hover:text-blue-500 dark:text-blue-400 cursor-pointer"
              title="Скорректировать факты сценария вручную"
            >
              <IconPencil size={11} />
              <span>Скорректировать факты</span>
            </button>
          </div>
        )}

        {/* Блок HitL-подтверждения (если действие требует одобрения инженера) */}
        {(ticketRun?.state === 'waiting_approval' || pendingCommand) && (
          <div className="p-3 rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5 text-xs font-bold text-amber-900 dark:text-amber-200">
                <IconAlertCircle size={14} className="text-amber-600" />
                <span>Требуется одобрение действия автопилота</span>
              </div>
              <span className="text-[11px] font-mono text-amber-700 dark:text-amber-400">
                {pendingCommand?.action || 'команда исполнения'}
              </span>
            </div>
            <p className="text-[12px] text-amber-800 dark:text-amber-300 leading-snug">
              Автопилот подготовил команду для выполнения на рабочей станции. Подтвердите выполнение или отклоните действие.
            </p>
            <div className="flex items-center justify-between gap-2 pt-1">
              <span className="text-[11px] font-medium text-amber-800 dark:text-amber-300">
                Параметры команды показаны перед основной кнопкой.
              </span>
              <button
                type="button"
                onClick={handleHitlReject}
                className="min-h-9 rounded-lg bg-neutral-200 px-3 text-xs font-semibold text-neutral-800 outline-none transition-colors hover:bg-neutral-300 focus-visible:ring-2 focus-visible:ring-amber-500 dark:bg-neutral-800 dark:text-neutral-200 dark:hover:bg-neutral-700"
              >
                Отклонить команду
              </button>
            </div>
          </div>
        )}

        {/* Предлагаемое решение и готовность */}
        <div className="flex items-start justify-between gap-3 pt-1">
          <div className="space-y-1">
            <div className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
              Предложение помощника
            </div>
            <div className="text-sm font-bold text-neutral-900 dark:text-neutral-100 flex items-center gap-2 flex-wrap">
              <span>{actionTitle}</span>
              <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300 border border-blue-200 dark:border-blue-900">
                Статус после действия: {targetStatusName}
              </span>
            </div>
            <div className="text-[11.5px] text-neutral-600 dark:text-neutral-400 flex items-center gap-3 flex-wrap">
              <span>Трудозатраты: <strong>{expenses} мин</strong></span>
              <span>•</span>
              <span className="flex items-center gap-1">
                Основания:
                {sources.rule && <span className="font-semibold text-neutral-700 dark:text-neutral-300">Правило</span>}
                {sources.rag && <span className="font-semibold text-purple-600 dark:text-purple-400">База знаний</span>}
                {sources.ai && <span className="font-semibold text-blue-600 dark:text-blue-400">AI-анализ</span>}
                {!sources.rule && !sources.rag && !sources.ai && <span>Не указаны</span>}
              </span>
            </div>
          </div>

          {/* Индикатор готовности (Ready) / Риска */}
          <div className="shrink-0 text-right">
            {isCriticalRisk ? (
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-amber-50 text-amber-900 dark:bg-amber-950/60 dark:text-amber-300 border border-amber-300 dark:border-amber-800 text-[11px] font-semibold">
                <IconAlertCircle size={12} className="text-amber-600 dark:text-amber-400" />
                <span>Риск простоя</span>
              </span>
            ) : isReady && !policyBlocked ? (
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 border border-neutral-300 dark:border-neutral-700 text-[11px] font-semibold">
                <IconCheckCircle size={12} className="text-neutral-500 dark:text-neutral-400" />
                <span>Готово к действию</span>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300 border border-rose-200 dark:border-rose-800 text-[11px] font-semibold">
                <IconAlertCircle size={12} />
                <span>Действие недоступно</span>
              </span>
            )}
          </div>
        </div>

        {/* Причины блокировки (если есть) */}
        {!isReady && blockedReasons.length > 0 && (
          <div className="p-2.5 rounded-lg bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900 text-xs text-rose-800 dark:text-rose-300 space-y-1">
            <div className="font-semibold">Почему действие недоступно:</div>
            <ul className="list-disc list-inside space-y-0.5">
              {blockedReasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        {missingData.length > 0 && (
          <div className="rounded-lg bg-amber-50 px-3 py-2.5 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            <div className="font-semibold">Не хватает данных:</div>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {missingData.map((item, index) => <li key={index}>{item}</li>)}
            </ul>
          </div>
        )}

        {policyBlocked && (
          <div className="rounded-lg bg-rose-50 px-3 py-2.5 text-xs font-medium text-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
            Действие запрещено действующей политикой. Подробности доступны в технических данных.
          </div>
        )}

        {/* Предупреждение о риске простоя производства */}
        {riskWarning && (
          <div className="p-3 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 text-xs text-amber-900 dark:text-amber-200 space-y-1">
            <div className="flex items-center gap-1.5 font-bold text-amber-800 dark:text-amber-300">
              <IconAlertCircle size={14} className="text-amber-600 shrink-0" />
              <span>Внимание: Риск производственного простоя</span>
            </div>
            <p className="text-[11.5px] leading-relaxed text-amber-900/90 dark:text-amber-200/90">
              {riskWarning}
            </p>
          </div>
        )}

        {/* Безопасное действие при риске простоя, если целевой статус был ошибочно выбран "Отменена" */}
        {isCriticalRisk && targetStatusId === 30 && (
          <div className="p-2.5 rounded-xl bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900 text-xs flex items-center justify-between gap-3">
            <span className="text-[11.5px] font-medium text-rose-800 dark:text-rose-300">
              Отмена инцидента с простоем заблокирована политикой безопасности.
            </span>
            <button
              type="button"
              onClick={handleTakeTicket}
              className="px-3 py-1.5 bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 rounded-lg font-bold text-xs shrink-0 transition-colors cursor-pointer"
            >
              Перевести в статус «В работе»
            </button>
          </div>
        )}

      </div>

      {/* ========================================================================= */}
      {/* 2. STALENESS BANNER (появляется только при изменении заявки на сервере)    */}
      {/* ========================================================================= */}
      {!hasAnalysisResult && (
        <div className="p-3 rounded-xl bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 flex items-center justify-between gap-3 text-xs text-neutral-700 dark:text-neutral-300">
          <div className="flex items-center gap-2">
            <IconAlertCircle size={15} className="text-neutral-500 shrink-0" />
            <span>Эта заявка ещё не анализировалась AI.</span>
          </div>
          <button
            type="button"
            onClick={handleAnalyze}
            disabled={reanalyzing}
            className="px-2.5 py-1 bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-white text-white dark:text-neutral-900 rounded-lg font-semibold text-[11px] shrink-0 transition-colors cursor-pointer disabled:opacity-50"
          >
            {reanalyzing ? 'Анализ...' : 'Проанализировать'}
          </button>
        </div>
      )}

      {hasAnalysisResult && !readinessIsCurrent && (
        <div className="p-3 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 flex items-center justify-between gap-3 text-xs text-amber-900 dark:text-amber-200">
          <div className="flex items-center gap-2">
            <IconAlertCircle size={15} className="text-amber-600 shrink-0" />
            <span>{analysis?.blocked_reason || 'Результат анализа устарел. Ваш черновик сохранён.'}</span>
          </div>
          <button
            type="button"
            onClick={handleReanalyze}
            disabled={reanalyzing}
            className="px-2.5 py-1 bg-amber-600 hover:bg-amber-500 text-white rounded-lg font-semibold text-[11px] shrink-0 transition-colors cursor-pointer shadow-xs disabled:opacity-50"
          >
            {reanalyzing ? 'Анализ...' : 'Проанализировать повторно'}
          </button>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 3. РЕДАКТОР ОТВЕТА И ПАРАМЕТРОВ (ИЗОЛИРОВАННЫЙ БУФЕР)                      */}
      {/* ========================================================================= */}
      <div className="space-y-2.5">
        {/* Панель управления формой: режим получателя и выбор шаблона */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded-lg border border-neutral-200 dark:border-neutral-700 text-xs font-semibold" aria-label="Видимость комментария">
            <button
              type="button"
              onClick={() => setReplyMode('reply')}
              className={`min-h-9 px-3 rounded-md outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500 ${
                replyMode === 'reply'
                  ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                  : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
              }`}
            >
              Комментарий заявителю
            </button>
            <button
              type="button"
              onClick={() => setReplyMode('internal')}
              className={`min-h-9 px-3 rounded-md outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500 ${
                replyMode === 'internal'
                  ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                  : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
              }`}
            >
              Служебный комментарий
            </button>
          </div>

          {/* Селектор регламентных шаблонов */}
          {templates && templates.length > 0 && (
            <select
              value={selectedTemplateKey}
              onChange={(e) => handleSelectTemplate(e.target.value)}
              aria-label="Шаблон комментария"
              className="min-h-9 text-xs font-medium px-2.5 rounded-lg bg-neutral-50 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 text-neutral-800 dark:text-neutral-200 outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            >
              <option value="">Шаблоны комментариев...</option>
              {templates.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
        </div>

        <p className="text-[11px] leading-4 text-neutral-500 dark:text-neutral-400">
          {replyMode === 'reply'
            ? 'Комментарий будет виден заявителю в истории заявки.'
            : 'Комментарий увидят только сотрудники поддержки.'}
        </p>

        {/* Поле ввода текста ответа */}
        <div className="relative">
          <textarea
            value={replyText}
            onChange={(e) => setReplyText(e.target.value)}
            placeholder={
              replyMode === 'reply'
                ? 'Введите текст сообщения заявителю или используйте быструю вставку ниже...'
                : 'Служебный комментарий, недоступный заявителю...'
            }
            rows={4}
            aria-label={replyMode === 'reply' ? 'Комментарий заявителю' : 'Служебный комментарий'}
            className="w-full text-xs p-3 rounded-xl border border-neutral-200 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/40 text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 transition-all resize-y font-sans leading-relaxed"
          />
          <span className="absolute bottom-2 right-2 rounded bg-white/90 px-1.5 py-0.5 font-mono text-[10px] text-neutral-500 dark:bg-neutral-900/90 dark:text-neutral-400">
            {replyText.length}
          </span>
        </div>

        {/* Строка быстрых сниппетов и списания минут */}
        <div className="flex items-center justify-between gap-2 flex-wrap text-xs">
          {hasSnippets ? (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] text-neutral-400 font-medium">Вставка:</span>
              {details?.ai_suggested_resolution && (
                <button
                  type="button"
                  onClick={() => insertSnippet(details.ai_suggested_resolution!)}
                  className="px-2 py-0.5 bg-blue-50 dark:bg-blue-950/60 hover:bg-blue-100 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-900 rounded-md text-[11px] font-semibold flex items-center gap-1 transition-colors cursor-pointer"
                  title="Вставить подготовленный AI-черновик"
                >
                  <IconSparkles size={11} />
                  <span>+ AI-черновик</span>
                </button>
              )}
              {details?.kb_matches && details.kb_matches.length > 0 && details.kb_matches[0]?.solution && (
                <button
                  type="button"
                  onClick={() => insertSnippet(details.kb_matches![0].solution)}
                  className="px-2 py-0.5 bg-purple-50 dark:bg-purple-950/60 hover:bg-purple-100 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-900 rounded-md text-[11px] font-semibold flex items-center gap-1 transition-colors cursor-pointer"
                  title="Вставить решение из первого совпадения базы знаний"
                >
                  <IconDatabase size={11} />
                  <span>+ Из RAG</span>
                </button>
              )}
            </div>
          ) : (
            <div />
          )}

          <div className="flex items-center gap-2">
            <label className="text-[11px] text-neutral-500 flex items-center gap-1 font-medium">
              <span>Трудозатраты:</span>
              <input
                type="number"
                min={0}
                max={480}
                value={expenses}
                onChange={(e) => setExpenses(parseInt(e.target.value, 10) || 0)}
                className="w-14 text-center font-mono py-0.5 px-1 rounded-md border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-semibold"
              />
              <span>мин</span>
            </label>
          </div>
        </div>

        <div className="space-y-3 rounded-xl bg-neutral-50 p-3 dark:bg-neutral-950/40" id="decision-impact">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[11.5px] sm:grid-cols-4">
            <div>
              <div className="text-[9.5px] font-bold uppercase tracking-wider text-neutral-500">Новый статус</div>
              <div className="mt-0.5 font-semibold text-neutral-900 dark:text-neutral-100">{targetStatusName}</div>
            </div>
            <div>
              <div className="text-[9.5px] font-bold uppercase tracking-wider text-neutral-500">Комментарий</div>
              <div className="mt-0.5 font-semibold text-neutral-900 dark:text-neutral-100">
                {replyMode === 'internal' ? 'Служебный' : 'Заявителю'}
              </div>
            </div>
            <div>
              <div className="text-[9.5px] font-bold uppercase tracking-wider text-neutral-500">Трудозатраты</div>
              <div className="mt-0.5 font-mono font-semibold text-neutral-900 dark:text-neutral-100">{expenses} мин</div>
            </div>
            {commandLabel && (
              <div>
                <div className="text-[9.5px] font-bold uppercase tracking-wider text-neutral-500">Команда</div>
                <div className="mt-0.5 truncate font-mono font-semibold text-neutral-900 dark:text-neutral-100" title={commandLabel}>
                  {commandLabel}
                </div>
              </div>
            )}
          </div>
          <div className="border-t border-neutral-200 pt-2 text-[11.5px] leading-relaxed text-neutral-600 dark:border-neutral-800 dark:text-neutral-400">
            <p><span className="font-semibold text-neutral-800 dark:text-neutral-200">Что изменится:</span> {consequences}</p>
            <p className="mt-1"><span className="font-semibold text-neutral-800 dark:text-neutral-200">Критерий результата:</span> {successCriterion}</p>
          </div>
        </div>

        {commentMissing && (
          <p className="text-xs font-medium text-rose-700 dark:text-rose-300" role="alert">
            Для выбранного статуса требуется комментарий.
          </p>
        )}

        <div className="relative flex items-center gap-2" ref={alternativesMenuRef}>
          <button
            type="button"
            onClick={pendingCommand ? handleHitlApprove : handleApplyDecision}
            disabled={submitting || actionUnavailable}
            aria-describedby="decision-impact"
            className="flex min-h-10 flex-1 items-center justify-center gap-2 rounded-xl bg-neutral-900 px-4 text-xs font-bold text-white shadow-sm outline-none transition-colors hover:bg-neutral-800 focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-200 dark:focus-visible:ring-offset-neutral-900"
          >
            {submitting ? (
              <>
                <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                <span>Сохранение...</span>
              </>
            ) : (
              <>
                <IconCheckCircle size={14} />
                <span>{primaryActionLabel}</span>
              </>
            )}
          </button>
          <button
            type="button"
            onClick={() => setIsAlternativesOpen((prev) => !prev)}
            aria-expanded={isAlternativesOpen}
            aria-label="Выбрать другой статус"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-neutral-200 bg-white text-neutral-700 outline-none transition-colors hover:bg-neutral-100 focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 dark:hover:bg-neutral-700"
          >
            <IconChevronDown size={15} className={`transition-transform ${isAlternativesOpen ? 'rotate-180' : ''}`} />
          </button>

          {isAlternativesOpen && (
            <div className="absolute right-0 top-full z-50 mt-1.5 w-56 space-y-0.5 rounded-xl border border-neutral-200 bg-white py-1.5 text-xs font-medium shadow-xl dark:border-neutral-800 dark:bg-neutral-900">
              <div className="px-3.5 py-1 text-[10px] font-bold uppercase tracking-wider text-neutral-500">Другой статус</div>
              {targetStatusId !== 27 && (
                <button type="button" onClick={() => { setSelectedStatusOverride(27); setIsAlternativesOpen(false); }} className="min-h-9 w-full px-3.5 text-left hover:bg-neutral-50 focus-visible:bg-neutral-100 dark:hover:bg-neutral-800">В работе</button>
              )}
              {targetStatusId !== 29 && (
                <button type="button" onClick={() => { setSelectedStatusOverride(29); setIsAlternativesOpen(false); }} className="min-h-9 w-full px-3.5 text-left hover:bg-neutral-50 focus-visible:bg-neutral-100 dark:hover:bg-neutral-800">Выполнена</button>
              )}
              {targetStatusId !== 35 && (
                <button type="button" onClick={() => { setSelectedStatusOverride(35); setIsAlternativesOpen(false); }} className="min-h-9 w-full px-3.5 text-left hover:bg-neutral-50 focus-visible:bg-neutral-100 dark:hover:bg-neutral-800">Требует уточнения</button>
              )}
              {targetStatusId !== 48 && (
                <button type="button" onClick={() => { setSelectedStatusOverride(48); setIsAlternativesOpen(false); }} className="min-h-9 w-full px-3.5 text-left hover:bg-neutral-50 focus-visible:bg-neutral-100 dark:hover:bg-neutral-800">Ожидание поставки (ремонт)</button>
              )}
              {targetStatusId !== 30 && (
                <button type="button" onClick={() => { setSelectedStatusOverride(30); setIsAlternativesOpen(false); }} className="min-h-9 w-full px-3.5 text-left text-rose-600 hover:bg-rose-50 focus-visible:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/30">Отменена</button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ========================================================================= */}
      {/* 4. ДОКАЗАТЕЛЬНАЯ БАЗА, ОСНОВАНИЯ, ДИАГНОСТИКА И ИСТОРИЯ ЦИКЛА             */}
      {/* ========================================================================= */}
      <div className="pt-2 border-t border-neutral-100 dark:border-neutral-800 space-y-2.5">
        {/* Таб-бар с векторными иконками */}
        <div className="flex flex-nowrap items-center gap-1 overflow-x-auto border-b border-neutral-200 pb-1 text-xs dark:border-neutral-800">
          <button
            type="button"
            onClick={() => setSelectedTab('facts')}
            className={`flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500 ${
              selectedTab === 'facts'
                ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            <IconDatabase size={13} />
            <span>Факты ({Object.keys(envelope?.facts_summary || {}).length})</span>
          </button>
          <button
            type="button"
            onClick={() => setSelectedTab('rules')}
            className={`flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500 ${
              selectedTab === 'rules'
                ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            <IconFileCode size={13} />
            <span>Основания</span>
          </button>
          <button
            type="button"
            onClick={() => setSelectedTab('diagnostics')}
            className={`flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500 ${
              selectedTab === 'diagnostics'
                ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            <IconServer size={13} />
            <span>Диагностика</span>
          </button>
          <button
            type="button"
            onClick={() => setSelectedTab('completeness')}
            className={`flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500 ${
              selectedTab === 'completeness'
                ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            <IconCheckCircle size={13} />
            <span>История цикла ({runEvents.length})</span>
          </button>
        </div>

        {/* Содержимое активного таба */}
        <div className="text-xs text-neutral-700 dark:text-neutral-300 leading-relaxed min-h-[90px]">
          {/* ТАБ 0: Факты сценария (FactBag) */}
          {selectedTab === 'facts' && (
            <FactBagSection
              envelope={envelope}
              onOpenOverride={() => setIsOverrideModalOpen(true)}
            />
          )}

          {/* ТАБ 1: Правила регламента (Explainable Rules) */}
          {selectedTab === 'rules' && (
            <div className="space-y-2.5 p-3 rounded-xl bg-neutral-50/60 dark:bg-neutral-950/30 border border-neutral-200 dark:border-neutral-800">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="font-bold text-neutral-900 dark:text-neutral-100 text-xs">
                  {details?.suggested_action?.name || 'Стандартное правило 1-й линии'}
                </span>
                <span className="text-[10.5px] font-mono px-2 py-0.5 rounded bg-neutral-200 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300">
                  {details?.suggested_action?.rule_type || 'standard_first_line'}
                </span>
              </div>

              {/* Маршрутизация раздела (если есть перенаправление) */}
              {details?.suggested_action?.rule_type === 'service_redirect' && (
                <div className="flex items-center gap-2 text-[11.5px] font-medium text-neutral-600 dark:text-neutral-400 bg-white dark:bg-neutral-900 p-2 rounded-lg border border-neutral-200 dark:border-neutral-800 flex-wrap">
                  <span>Текущий:</span>
                  <span className="px-1.5 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 font-semibold">
                    {ticket.serviceName || details?.service_name || 'Не указан'}
                  </span>
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="text-neutral-400 shrink-0">
                    <path d="M2.5 6h7M6.5 3l3 3-3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                  <span>Рекомендуемый:</span>
                  <span className="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-900 font-semibold">
                    {details?.suggested_action?.target_service || details?.suggested_action?.name || 'Другой раздел'}
                  </span>
                </div>
              )}

              {/* Обоснование / Причина сработки */}
              <div className="space-y-1">
                <div className="text-[10.5px] font-bold text-neutral-400 dark:text-neutral-500 uppercase tracking-wider">
                  Основание предложения:
                </div>
                <p className="text-[11.5px] text-neutral-700 dark:text-neutral-300 leading-relaxed">
                  {ruleReason ||
                    (details?.suggested_action?.rule_type === 'service_redirect'
                      ? 'Тематика инцидента не соответствует выбранному разделу каталога услуг.'
                      : details?.suggested_action?.rule_type === 'duplicate_task'
                      ? 'Обнаружен дубликат ранее созданной активной заявки.'
                      : details?.suggested_action?.rule_type === 'downtime_priority'
                      ? 'Обнаружен критический инцидент с признаками простоя производства: заявка передана в ускоренную обработку.'
                      : 'Применяется базовое правило диспетчеризации технической поддержки 1-й линии.')}
                </p>
              </div>

              {/* Ключевые маркеры сработки */}
              {triggerMarkers.length > 0 && (
                <div className="space-y-1 pt-1 border-t border-neutral-200/60 dark:border-neutral-800">
                  <div className="text-[10.5px] font-bold text-neutral-400 dark:text-neutral-500 uppercase tracking-wider">
                    Обнаруженные маркеры в тексте:
                  </div>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {triggerMarkers.map((m, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 rounded-md bg-neutral-200/80 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 text-[10.5px] font-mono border border-neutral-300/60 dark:border-neutral-700"
                      >
                        {m}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Предупреждение о риске (если есть) */}
              {riskWarning && (
                <div className="p-2.5 rounded-lg bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 text-[11px] text-amber-900 dark:text-amber-200 leading-snug">
                  <strong>Внимание:</strong> {riskWarning}
                </div>
              )}
            </div>
          )}

          {/* ТАБ 2: База знаний (RAG) */}
          {selectedTab === 'rag' && (
            <div className="space-y-2">
              {details?.kb_matches && details.kb_matches.length > 0 ? (
                details.kb_matches.map((m: RAGMatchItem, i: number) => (
                  <div
                    key={m.task_id || i}
                    className="p-2.5 rounded-xl border border-neutral-200 dark:border-neutral-800 bg-neutral-50/60 dark:bg-neutral-950/30 space-y-1.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-bold text-neutral-900 dark:text-neutral-100 font-mono">
                        #{m.task_id} {m.name || m.problem}
                      </span>
                      <span className="text-[10.5px] font-mono px-1.5 py-0.5 rounded bg-purple-100 dark:bg-purple-950 text-purple-700 dark:text-purple-300 font-bold shrink-0">
                        {Math.round(m.similarity_pct || (1 - (m.distance || 0.3)) * 100)}% сходство
                      </span>
                    </div>
                    <div className="text-[11.5px] text-neutral-600 dark:text-neutral-400 line-clamp-2">
                      {m.solution}
                    </div>
                    <div className="pt-1 flex justify-end">
                      <button
                        type="button"
                        onClick={() => insertSnippet(m.solution)}
                        className="text-[11px] text-purple-600 dark:text-purple-400 hover:underline font-semibold cursor-pointer"
                      >
                        Вставить решение в ответ
                      </button>
                    </div>
                  </div>
                ))
              ) : (
                <div className="py-4 text-center text-neutral-400">
                  Векторная база знаний pgvector не обнаружила релевантных прецедентов
                </div>
              )}
            </div>
          )}

          {/* ТАБ 3: AI-анализ, черновик и сводка комментариев */}
          {selectedTab === 'ai' && (
            <div className="space-y-2.5">
              {/* Факты, извлеченные LLM-сенсором с подтверждением (Grounding Evidence) */}
              {(details?.task?._llm_fact_extraction || details?.task?._extracted_pc_name || details?.task?._extracted_printer_address || details?.task?._extracted_file_path) && (
                <div className="p-2.5 rounded-xl border border-emerald-200 dark:border-emerald-900 bg-emerald-50/40 dark:bg-emerald-950/20 space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-emerald-900 dark:text-emerald-200 flex items-center gap-1.5 text-xs">
                      <IconSparkles size={13} className="text-emerald-600 dark:text-emerald-400" />
                      <span>Подтвержденные факты (LLM Sensor)</span>
                    </span>
                    <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200 font-semibold">
                      Grounded
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-[11px] pt-1">
                    {details?.task?._extracted_pc_name && (
                      <div className="rounded bg-white/70 dark:bg-neutral-900/60 p-1.5 border border-emerald-100 dark:border-emerald-950">
                        <span className="text-[10px] text-neutral-500 block">ПК:</span>
                        <span className="font-mono font-bold text-neutral-900 dark:text-neutral-100">{details.task._extracted_pc_name}</span>
                      </div>
                    )}
                    {details?.task?._extracted_printer_address && (
                      <div className="rounded bg-white/70 dark:bg-neutral-900/60 p-1.5 border border-emerald-100 dark:border-emerald-950">
                        <span className="text-[10px] text-neutral-500 block">Принтер:</span>
                        <span className="font-mono font-bold text-neutral-900 dark:text-neutral-100">{details.task._extracted_printer_address}</span>
                      </div>
                    )}
                    {details?.task?._extracted_file_path && (
                      <div className="col-span-2 rounded bg-white/70 dark:bg-neutral-900/60 p-1.5 border border-emerald-100 dark:border-emerald-950">
                        <span className="text-[10px] text-neutral-500 block">Путь к файлу:</span>
                        <span className="font-mono text-[10.5px] break-all font-semibold text-neutral-900 dark:text-neutral-100">{details.task._extracted_file_path}</span>
                      </div>
                    )}
                  </div>
                </div>
              )}
              {details?.ai_suggested_resolution ? (
                <div className="p-2.5 rounded-xl border border-blue-200 dark:border-blue-900 bg-blue-50/40 dark:bg-blue-950/20 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-blue-900 dark:text-blue-200 flex items-center gap-1.5">
                      <IconSparkles size={13} />
                      <span>AI-черновик с проверкой источников</span>
                    </span>
                    <span className="text-[10.5px] font-mono px-2 py-0.5 rounded bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200 font-semibold">
                      Circuit: {details?.circuit?.toUpperCase() || 'GREEN'}
                    </span>
                  </div>
                  <p className="text-[11.5px] text-neutral-800 dark:text-neutral-200 whitespace-pre-wrap leading-relaxed">
                    {details.ai_suggested_resolution}
                  </p>
                </div>
              ) : (
                <div className="py-2 text-neutral-400">
                  AI-анализ не использовался: предложение подготовлено по формализованному правилу
                </div>
              )}

              {/* AI-сводка переписки (TL;DR) */}
              <div className="pt-1 border-t border-neutral-200 dark:border-neutral-800 flex items-center justify-between">
                <span className="text-[11px] text-neutral-500 font-medium">Сводка комментариев:</span>
                {!aiSummary && !loadingAiSummary && (
                  <button
                    type="button"
                    onClick={onGenerateAiSummary}
                    className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline font-semibold cursor-pointer"
                  >
                    Сгенерировать сводку
                  </button>
                )}
              </div>
              {loadingAiSummary && (
                <div className="text-[11px] text-neutral-400 animate-pulse">
                  AI Hub формирует сводку диалога...
                </div>
              )}
              {aiSummary && (
                <div className="p-2.5 rounded-lg bg-neutral-100 dark:bg-neutral-800 text-[11.5px] text-neutral-700 dark:text-neutral-300 space-y-1">
                  <div className="font-medium text-neutral-900 dark:text-neutral-100">{aiSummary.core_problem}</div>
                  {aiSummary.recommended_next_step && (
                    <div className="text-neutral-500 dark:text-neutral-400 text-[11px]">
                      Следующий шаг: {aiSummary.recommended_next_step}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ТАБ 4: Диагностика хоста */}
          {selectedTab === 'diagnostics' && (
            <div className="space-y-2 p-2 rounded-xl bg-neutral-50/60 dark:bg-neutral-950/30 border border-neutral-200 dark:border-neutral-800">
              <div className="font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-1.5">
                <IconServer size={14} />
                <span>Результаты для <span className="font-mono">{ticket.host || details?.pc_name || 'хост не указан'}</span></span>
              </div>

              <div className="grid grid-cols-3 gap-2 pt-1 text-center font-mono text-[11px]">
                {diagnosticItems.map((item) => (
                  <div key={item.key} className="min-w-0 rounded-lg border border-neutral-200 bg-white p-2 dark:border-neutral-800 dark:bg-neutral-900">
                    <div className="text-[10px] text-neutral-400">{item.label}</div>
                    <div className={`truncate font-bold ${item.valueClass}`} title={item.value}>
                      {item.value}
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[10.5px] text-neutral-500 dark:text-neutral-400">
                Запуск проверки доступен в блоке рабочей станции в контексте заявки.
              </p>
            </div>
          )}

          {/* ТАБ 5: История внутреннего цикла и аудит */}
          {selectedTab === 'completeness' && (
            <div className="space-y-2">
              {runEvents.length > 0 ? (
                <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                  {runEvents.map((ev) => (
                    <div
                      key={ev.id}
                      className="p-2 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/20 text-[11px] flex items-center justify-between"
                    >
                      <div className="space-y-0.5">
                        <div className="font-semibold text-neutral-900 dark:text-neutral-100">
                          {ev.event_type}
                        </div>
                        <div className="text-neutral-400 text-[10px]">
                          Инициатор: {ev.actor}
                        </div>
                      </div>
                      <div className="text-[10px] font-mono text-neutral-400">
                        {ev.created_at ? new Date(ev.created_at).toLocaleTimeString() : ''}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-3 text-center text-neutral-400 text-xs">
                  События внутреннего цикла отсутствуют
                </div>
              )}

              {/* Раскрывающийся JSON payload */}
              <div className="pt-1">
                <button
                  type="button"
                  onClick={() => setShowTechnicalAudit((prev) => !prev)}
                  className="text-[11px] text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200 font-mono flex items-center gap-1 cursor-pointer"
                >
                  <IconChevronRight
                    size={12}
                    className={`transition-transform duration-150 ${showTechnicalAudit ? 'rotate-90' : ''}`}
                  />
                  <span>Технические данные (JSON)</span>
                </button>
                {showTechnicalAudit && (
                  <pre className="mt-1 p-2 rounded-lg bg-neutral-900 text-neutral-200 text-[10px] font-mono overflow-x-auto max-h-36">
                    {JSON.stringify(
                      {
                        decision_id: decision?.id,
                        version: decision?.version,
                        fingerprint: decision?.fingerprint,
                        sources,
                        readiness,
                      },
                      null,
                      2
                    )}
                  </pre>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ========================================================================= */}
      {/* 5. ОЦЕНКА РЕШЕНИЯ ОПЕРАТОРОМ (FEEDBACK)                                   */}
      {/* ========================================================================= */}
      <div className="pt-2 border-t border-neutral-100 dark:border-neutral-800 flex items-center justify-between gap-2 flex-wrap text-xs">
        <div className="text-[11px] text-neutral-400 font-medium">Оценка предложения:</div>
        {feedbackSubmitted ? (
          <div className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400 font-semibold">
            <IconCheck size={12} />
            <span>Оценка сохранена в журнале аудита</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 flex-wrap">
            <button
              type="button"
              onClick={() => {
                setSelectedVerdict('correct');
                handleSubmitFeedback('correct');
              }}
              className="px-2 py-0.5 rounded-md border border-neutral-200 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-800 hover:bg-emerald-50 hover:text-emerald-700 dark:hover:bg-emerald-950/60 dark:hover:text-emerald-300 text-[11px] font-semibold transition-colors cursor-pointer"
            >
              Верно
            </button>
            <button
              type="button"
              onClick={() => {
                setSelectedVerdict('partial');
                handleSubmitFeedback('partial');
              }}
              className="px-2 py-0.5 rounded-md border border-neutral-200 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-800 hover:bg-amber-50 hover:text-amber-700 dark:hover:bg-amber-950/60 dark:hover:text-amber-300 text-[11px] font-semibold transition-colors cursor-pointer"
            >
              Частично
            </button>
            <button
              type="button"
              onClick={() => {
                setSelectedVerdict('incorrect');
                handleSubmitFeedback('incorrect', reasonCode);
              }}
              className="px-2 py-0.5 rounded-md border border-neutral-200 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-800 hover:bg-rose-50 hover:text-rose-700 dark:hover:bg-rose-950/60 dark:hover:text-rose-300 text-[11px] font-semibold transition-colors cursor-pointer"
            >
              Ошибка
            </button>
            <button
              type="button"
              onClick={() => {
                setSelectedVerdict('insufficient_data');
                handleSubmitFeedback('insufficient_data');
              }}
              className="px-2 py-0.5 rounded-md border border-neutral-200 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-800 text-[11px] font-semibold text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200 transition-colors cursor-pointer"
            >
              Мало данных
            </button>
          </div>
        )}
      </div>

      {/* Модальное окно Fact-Override */}
      <FactOverrideModal
        isOpen={isOverrideModalOpen}
        onClose={() => setIsOverrideModalOpen(false)}
        envelope={envelope}
        onSubmit={handleOverrideFacts}
        isSubmitting={submitting}
      />
    </div>
  );
}
