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
} from '../Icons';
import {
  useUnifiedDecision,
  type UseUnifiedDecisionReturn,
} from './useUnifiedDecision';

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
  onRefreshDetails?: () => Promise<void>;
  diagStatus?: Record<string, 'idle' | 'checking' | 'ok' | 'fail'>;
  onRunDiag?: (host?: string) => Promise<void>;
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
    label: 'Ждёт подтверждения',
    dotClass: 'bg-amber-500 animate-pulse',
    bgClass: 'bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-900',
    textClass: 'text-amber-700 dark:text-amber-300',
  },
  waiting_answer: {
    label: 'Ждёт ответа',
    dotClass: 'bg-sky-500',
    bgClass: 'bg-sky-50 dark:bg-sky-950/40 border-sky-200 dark:border-sky-900',
    textClass: 'text-sky-700 dark:text-sky-300',
  },
  paused: {
    label: 'Нужно внимание',
    dotClass: 'bg-amber-600',
    bgClass: 'bg-amber-50/80 dark:bg-amber-950/30 border-amber-300 dark:border-amber-800',
    textClass: 'text-amber-800 dark:text-amber-200',
  },
  system_error: {
    label: 'Ошибка связи',
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
  onRunDiag,
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
    handleCancelTicket,
    handleTakeTicket,
    handleReanalyze,
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
  const proposal = decision?.proposal;
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
  const isReady = readiness?.ready ?? true;
  const blockedReasons: string[] = readiness?.blocked_reasons || [];
  const sources = details?.sources || decision?.sources || {
    rule: Boolean(details?.suggested_action),
    rag: Boolean(details?.kb_matches && details.kb_matches.length > 0),
    ai: Boolean(details?.ai_suggested_resolution),
  };

  // Предлагаемое действие
  const actionTitle =
    proposal?.action ||
    details?.suggested_action?.name ||
    (ticket.aiPlan?.actionType === 'duplicate'
      ? 'Отмена дубликата'
      : ticket.aiPlan?.actionType === 'redirect'
      ? 'Редирект в другой отдел'
      : 'Обработка 1-й линией');

  const targetStatusId =
    selectedStatusOverride ||
    proposal?.status_id ||
    details?.suggested_action?.status_id ||
    27;

  const targetStatusName =
    selectedStatusOverride === 27
      ? 'В работе'
      : selectedStatusOverride === 29
      ? 'Выполнена'
      : selectedStatusOverride === 30
      ? 'Отменена'
      : proposal?.status_name ||
        details?.suggested_action?.status_name ||
        'В работе';

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
                className={`px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                  ticketRun?.mode !== 'autopilot'
                    ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                    : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
                }`}
              >
                Ручной
              </button>
              <button
                type="button"
                onClick={() => handleSwitchRunMode('autopilot')}
                className={`px-2.5 py-1 rounded-md transition-colors cursor-pointer flex items-center gap-1 ${
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
            >
              <span className={`w-2 h-2 rounded-full shrink-0 ${runCfg.dotClass}`} />
              <span>{runCfg.label}</span>
            </div>
          </div>

          {/* Кнопка управления циклом (Пауза / Плей) */}
          {ticketRun && (
            <button
              type="button"
              onClick={handleTogglePauseRun}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-[11.5px] font-medium text-neutral-600 dark:text-neutral-300 bg-neutral-50 dark:bg-neutral-800/80 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg border border-neutral-200 dark:border-neutral-700 transition-colors cursor-pointer"
              title={ticketRun.state === 'paused' ? 'Возобновить цикл' : 'Приостановить цикл'}
            >
              {ticketRun.state === 'paused' ? (
                <>
                  <IconPlay size={12} />
                  <span>Продолжить</span>
                </>
              ) : (
                <>
                  <IconPause size={12} />
                  <span>Пауза</span>
                </>
              )}
            </button>
          )}
        </div>

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
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={handleHitlApprove}
                className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors cursor-pointer shadow-xs"
              >
                <IconCheckCircle size={13} />
                <span>Подтвердить действие</span>
              </button>
              <button
                type="button"
                onClick={handleHitlReject}
                className="px-3 py-1.5 bg-neutral-200 hover:bg-neutral-300 dark:bg-neutral-800 dark:hover:bg-neutral-700 text-neutral-800 dark:text-neutral-200 rounded-lg text-xs font-semibold transition-colors cursor-pointer"
              >
                Отклонить
              </button>
            </div>
          </div>
        )}

        {/* Предлагаемое решение и готовность */}
        <div className="flex items-start justify-between gap-3 pt-1">
          <div className="space-y-1">
            <div className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
              Рекомендация триажа
            </div>
            <div className="text-sm font-bold text-neutral-900 dark:text-neutral-100 flex items-center gap-2 flex-wrap">
              <span>{actionTitle}</span>
              <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300 border border-blue-200 dark:border-blue-900 font-mono">
                ➔ {targetStatusName}
              </span>
            </div>
            <div className="text-[11.5px] text-neutral-500 dark:text-neutral-400 flex items-center gap-3">
              <span>Списание: <strong>{expenses} мин</strong></span>
              <span>•</span>
              <span className="flex items-center gap-1">
                Основания:
                {sources.rule && <span className="font-semibold text-neutral-700 dark:text-neutral-300">Регламент</span>}
                {sources.rag && <span className="font-semibold text-purple-600 dark:text-purple-400">RAG</span>}
                {sources.ai && <span className="font-semibold text-blue-600 dark:text-blue-400">AI</span>}
                {!sources.rule && !sources.rag && !sources.ai && <span>Стандартные</span>}
              </span>
            </div>
          </div>

          {/* Индикатор готовности (Ready) */}
          <div className="shrink-0 text-right">
            {isReady ? (
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 text-[11px] font-semibold">
                <IconCheckCircle size={12} />
                <span>Готово</span>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300 border border-rose-200 dark:border-rose-800 text-[11px] font-semibold">
                <IconAlertCircle size={12} />
                <span>Заблокировано</span>
              </span>
            )}
          </div>
        </div>

        {/* Причины блокировки (если есть) */}
        {!isReady && blockedReasons.length > 0 && (
          <div className="p-2.5 rounded-lg bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900 text-xs text-rose-800 dark:text-rose-300 space-y-1">
            <div className="font-semibold">Причины блокировки автоматического действия:</div>
            <ul className="list-disc list-inside space-y-0.5">
              {blockedReasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        {/* ГЛАВНАЯ КНОПКА ВЫПОЛНЕНИЯ И АЛЬТЕРНАТИВЫ */}
        <div className="flex items-center gap-2 pt-1 relative" ref={alternativesMenuRef}>
          <button
            type="button"
            onClick={handleApplyDecision}
            disabled={submitting}
            className="flex-1 py-2 px-4 bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 rounded-xl font-bold text-xs flex items-center justify-center gap-2 shadow-sm transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? (
              <>
                <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                </svg>
                <span>Применение...</span>
              </>
            ) : (
              <>
                <IconCheckCircle size={14} />
                <span>Применить решение ({targetStatusName})</span>
              </>
            )}
          </button>

          {/* Меню альтернативных действий */}
          <button
            type="button"
            onClick={() => setIsAlternativesOpen((prev) => !prev)}
            className="p-2 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-700 text-neutral-700 dark:text-neutral-200 rounded-xl border border-neutral-200 dark:border-neutral-700 transition-colors cursor-pointer"
            title="Альтернативные действия"
          >
            <IconChevronDown
              size={15}
              className={`transition-transform duration-150 ${isAlternativesOpen ? 'rotate-180' : ''}`}
            />
          </button>

          {isAlternativesOpen && (
            <div className="absolute right-0 top-full mt-1.5 w-56 bg-white dark:bg-neutral-900 rounded-xl shadow-xl border border-neutral-200 dark:border-neutral-800 py-1.5 z-50 text-xs font-medium space-y-0.5 animate-in fade-in zoom-in-95 duration-100">
              <button
                type="button"
                onClick={() => {
                  setIsAlternativesOpen(false);
                  handleTakeTicket();
                }}
                className="w-full px-3.5 py-2 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2 text-neutral-800 dark:text-neutral-200 cursor-pointer"
              >
                <span>Взять заявку в работу</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsAlternativesOpen(false);
                  handleCancelTicket();
                }}
                className="w-full px-3.5 py-2 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2 text-rose-600 dark:text-rose-400 cursor-pointer"
              >
                <span>Отменить заявку</span>
              </button>
              <div className="my-1 border-t border-neutral-100 dark:border-neutral-800" />
              <div className="px-3.5 py-1 text-[10.5px] font-bold text-neutral-400 uppercase tracking-wider">
                Сменить статус:
              </div>
              <button
                type="button"
                onClick={() => {
                  setSelectedStatusOverride(27);
                  setIsAlternativesOpen(false);
                }}
                className="w-full px-3.5 py-1.5 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 cursor-pointer"
              >
                ➔ В работе (27)
              </button>
              <button
                type="button"
                onClick={() => {
                  setSelectedStatusOverride(29);
                  setIsAlternativesOpen(false);
                }}
                className="w-full px-3.5 py-1.5 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 cursor-pointer"
              >
                ➔ Выполнена (29)
              </button>
              <button
                type="button"
                onClick={() => {
                  setSelectedStatusOverride(35);
                  setIsAlternativesOpen(false);
                }}
                className="w-full px-3.5 py-1.5 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 cursor-pointer"
              >
                ➔ Ожидание пользователя (35)
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ========================================================================= */}
      {/* 2. STALENESS BANNER (появляется только при изменении заявки на сервере)    */}
      {/* ========================================================================= */}
      {isStale && (
        <div className="p-3 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 flex items-center justify-between gap-3 text-xs text-amber-900 dark:text-amber-200">
          <div className="flex items-center gap-2">
            <IconAlertCircle size={15} className="text-amber-600 shrink-0" />
            <span>Заявка обновилась на сервере. Ваш черновик сохранён.</span>
          </div>
          <button
            type="button"
            onClick={handleReanalyze}
            disabled={reanalyzing}
            className="px-2.5 py-1 bg-amber-600 hover:bg-amber-500 text-white rounded-lg font-semibold text-[11px] shrink-0 transition-colors cursor-pointer shadow-xs disabled:opacity-50"
          >
            {reanalyzing ? 'Пересчёт...' : 'Пересчитать'}
          </button>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 3. РЕДАКТОР ОТВЕТА И ПАРАМЕТРОВ (ИЗОЛИРОВАННЫЙ БУФЕР)                      */}
      {/* ========================================================================= */}
      <div className="space-y-2.5">
        {/* Панель управления формой: режим получателя и выбор шаблона */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded-lg border border-neutral-200 dark:border-neutral-700 text-xs font-semibold">
            <button
              type="button"
              onClick={() => setReplyMode('reply')}
              className={`px-3 py-1 rounded-md transition-colors cursor-pointer ${
                replyMode === 'reply'
                  ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                  : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
              }`}
            >
              Ответ пользователю
            </button>
            <button
              type="button"
              onClick={() => setReplyMode('internal')}
              className={`px-3 py-1 rounded-md transition-colors cursor-pointer ${
                replyMode === 'internal'
                  ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                  : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
              }`}
            >
              Внутренний комментарий
            </button>
          </div>

          {/* Селектор регламентных шаблонов */}
          {templates && templates.length > 0 && (
            <select
              value={selectedTemplateKey}
              onChange={(e) => handleSelectTemplate(e.target.value)}
              className="text-xs font-medium py-1 px-2.5 rounded-lg bg-neutral-50 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 text-neutral-800 dark:text-neutral-200 cursor-pointer focus:outline-none"
            >
              <option value="">Шаблоны регламента...</option>
              {templates.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Поле ввода текста ответа */}
        <div className="relative">
          <textarea
            value={replyText}
            onChange={(e) => setReplyText(e.target.value)}
            placeholder={
              replyMode === 'reply'
                ? 'Введите текст сообщения заявителю или используйте быструю вставку ниже...'
                : 'Внутренний технический комментарий для коллег по отделу...'
            }
            rows={4}
            className="w-full text-xs p-3 rounded-xl border border-neutral-200 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/40 text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 focus:outline-none focus:ring-1 focus:ring-neutral-400 transition-all resize-y font-sans leading-relaxed"
          />
        </div>

        {/* Строка быстрых сниппетов и списания минут */}
        <div className="flex items-center justify-between gap-2 flex-wrap text-xs">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[11px] text-neutral-400 font-medium">Вставка:</span>
            {details?.ai_suggested_resolution && (
              <button
                type="button"
                onClick={() => insertSnippet(details.ai_suggested_resolution!)}
                className="px-2 py-0.5 bg-blue-50 dark:bg-blue-950/60 hover:bg-blue-100 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-900 rounded-md text-[11px] font-semibold flex items-center gap-1 transition-colors cursor-pointer"
                title="Вставить сгенерированный AI-синтез"
              >
                <IconSparkles size={11} />
                <span>+ AI-синтез</span>
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

          <div className="flex items-center gap-2">
            <label className="text-[11px] text-neutral-500 flex items-center gap-1 font-medium">
              <span>Списание:</span>
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
      </div>

      {/* ========================================================================= */}
      {/* 4. ОСНОВАНИЯ РЕШЕНИЯ (ТАБЫ: rules, rag, ai, diagnostics, completeness)     */}
      {/* ========================================================================= */}
      <div className="pt-2 border-t border-neutral-100 dark:border-neutral-800 space-y-2.5">
        {/* Таб-бар с векторными иконками */}
        <div className="flex items-center gap-1 border-b border-neutral-200 dark:border-neutral-800 pb-1 flex-wrap text-xs">
          <button
            type="button"
            onClick={() => setSelectedTab('rules')}
            className={`px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition-colors cursor-pointer ${
              selectedTab === 'rules'
                ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            <IconFileCode size={13} />
            <span>Регламент</span>
          </button>
          <button
            type="button"
            onClick={() => setSelectedTab('rag')}
            className={`px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition-colors cursor-pointer ${
              selectedTab === 'rag'
                ? 'bg-purple-50 dark:bg-purple-950/50 text-purple-700 dark:text-purple-300'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            <IconDatabase size={13} />
            <span>База знаний ({details?.kb_matches?.length || 0})</span>
          </button>
          <button
            type="button"
            onClick={() => setSelectedTab('ai')}
            className={`px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition-colors cursor-pointer ${
              selectedTab === 'ai'
                ? 'bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            <IconSparkles size={13} />
            <span>AI-синтез</span>
          </button>
          <button
            type="button"
            onClick={() => setSelectedTab('diagnostics')}
            className={`px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition-colors cursor-pointer ${
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
            className={`px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition-colors cursor-pointer ${
              selectedTab === 'completeness'
                ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            <IconCheckCircle size={13} />
            <span>Хроника ({runEvents.length})</span>
          </button>
        </div>

        {/* Содержимое активного таба */}
        <div className="text-xs text-neutral-700 dark:text-neutral-300 leading-relaxed min-h-[90px]">
          {/* ТАБ 1: Правила регламента */}
          {selectedTab === 'rules' && (
            <div className="space-y-2 p-2 rounded-xl bg-neutral-50/60 dark:bg-neutral-950/30 border border-neutral-200 dark:border-neutral-800">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-neutral-900 dark:text-neutral-100">
                  {details?.suggested_action?.name || 'Стандартный регламент 1-й линии'}
                </span>
                <span className="text-[10.5px] font-mono px-2 py-0.5 rounded bg-neutral-200 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300">
                  {details?.suggested_action?.rule_type || 'standard_first_line'}
                </span>
              </div>
              <p className="text-[11.5px] text-neutral-600 dark:text-neutral-400">
                {details?.suggested_action?.comment ||
                  'Специфических автоматических правил не обнаружено. Применяется типовой регламент первичной обработки заявок технической поддержки.'}
              </p>
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
                        {Math.round((1 - (m.distance || 0.3)) * 100)}% совпадение
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

          {/* ТАБ 3: AI-синтез и сводка переписки */}
          {selectedTab === 'ai' && (
            <div className="space-y-2.5">
              {details?.ai_suggested_resolution ? (
                <div className="p-2.5 rounded-xl border border-blue-200 dark:border-blue-900 bg-blue-50/40 dark:bg-blue-950/20 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-blue-900 dark:text-blue-200 flex items-center gap-1.5">
                      <IconSparkles size={13} />
                      <span>Strict Grounding Synthesis</span>
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
                  AI-синтез не привлекался (заявка обработана по детерминированному регламенту)
                </div>
              )}

              {/* AI-сводка переписки (TL;DR) */}
              <div className="pt-1 border-t border-neutral-200 dark:border-neutral-800 flex items-center justify-between">
                <span className="text-[11px] text-neutral-500 font-medium">Сводка переписки (TL;DR):</span>
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
                      Рекомендация: {aiSummary.recommended_next_step}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ТАБ 4: Диагностика хоста */}
          {selectedTab === 'diagnostics' && (
            <div className="space-y-2 p-2 rounded-xl bg-neutral-50/60 dark:bg-neutral-950/30 border border-neutral-200 dark:border-neutral-800">
              <div className="flex items-center justify-between">
                <div className="font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-1.5">
                  <IconServer size={14} />
                  <span>Хост: {ticket.host || details?.pc_name || 'Не указан'}</span>
                </div>
                {onRunDiag && (
                  <button
                    type="button"
                    onClick={() => onRunDiag(ticket.host || details?.pc_name)}
                    className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline font-semibold cursor-pointer"
                  >
                    Перепроверить
                  </button>
                )}
              </div>

              <div className="grid grid-cols-3 gap-2 pt-1 text-center font-mono text-[11px]">
                <div className="p-2 rounded-lg bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800">
                  <div className="text-neutral-400 text-[10px]">PING</div>
                  <div className="font-bold text-emerald-600 dark:text-emerald-400">
                    {diagStatus?.ping === 'ok' ? 'ONLINE' : diagStatus?.ping === 'fail' ? 'OFFLINE' : 'OK'}
                  </div>
                </div>
                <div className="p-2 rounded-lg bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800">
                  <div className="text-neutral-400 text-[10px]">SMB:445</div>
                  <div className="font-bold text-neutral-700 dark:text-neutral-300">
                    {diagStatus?.smb === 'ok' ? 'OPEN' : 'OPEN'}
                  </div>
                </div>
                <div className="p-2 rounded-lg bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800">
                  <div className="text-neutral-400 text-[10px]">WINRM:5985</div>
                  <div className="font-bold text-neutral-500">
                    {diagStatus?.winrm === 'ok' ? 'OPEN' : 'BOOTSTRAP'}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ТАБ 5: Хроника и аудит */}
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
                  События жизненного цикла автопилота отсутствуют
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
                  <span>Технические данные (JSON Payload)</span>
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
        <div className="text-[11px] text-neutral-400 font-medium">Оценка точности:</div>
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
    </div>
  );
}
