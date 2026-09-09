import { useState } from 'react';
import type { Ticket } from '../../data/mock';
import type { TaskDetails } from '../../lib/types';
import {
  IconPlay,
  IconPause,
  IconAlertCircle,
  IconSparkles,
  IconChevronRight,
  IconCheck,
  IconRefresh,
} from '../Icons';
import type { UseUnifiedDecisionReturn } from './useUnifiedDecision';
import FactBagSection from './FactBagSection';
import FactOverrideModal from './FactOverrideModal';
import { getScenarioTitle } from '../../lib/scenarios';

interface UnifiedDecisionPanelProps {
  ticket: Ticket;
  details: TaskDetails | null;
  rawId: number;
  loadingDetails?: boolean;
  decisionState: UseUnifiedDecisionReturn;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
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
  loadingDetails = false,
  decisionState,
  onToast,
}: UnifiedDecisionPanelProps) {
  const {
    selectedTab,
    setSelectedTab,
    ticketRun,
    runEvents,
    pendingCommand,
    isStale,
    reanalyzing,
    feedbackSubmitted,
    handleReanalyze,
    handleAnalyze,
    handleOverrideFacts,
    handleHitlApprove,
    handleHitlReject,
    handleTogglePauseRun,
    handleSwitchRunMode,
    handleSubmitFeedback,
    insertSnippet,
  } = decisionState;

  const [isOverrideModalOpen, setIsOverrideModalOpen] = useState(false);
  const [showTechnicalEvidence, setShowTechnicalEvidence] = useState(false);
  const [selectedVerdict, setSelectedVerdict] = useState<
    'correct' | 'partial' | 'incorrect' | 'insufficient_data' | null
  >(null);
  const [showFeedbackDetails, setShowFeedbackDetails] = useState(false);
  const [reasonCode, setReasonCode] = useState<string>('wrong_context');

  // Вычисление параметров решения
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

  const isReady = hasAnalysisResult && (readiness?.ready ?? true) && !(readiness?.stale || isStale);
  const blockedReasons: string[] = Array.from(
    new Set([
      ...(readiness?.blocked_reasons || []),
      ...(analysis?.blocked_reason ? [analysis.blocked_reason] : []),
    ])
  );

  const riskLevel =
    envelopeOutcome.risk_level ||
    envelopePolicy.risk_level ||
    proposal?.risk_level ||
    details?.suggested_action?.risk_level ||
    'normal';
  const riskWarning = proposal?.risk_warning || details?.suggested_action?.risk_warning || null;
  const isCriticalRisk = riskLevel === 'critical' || Boolean(riskWarning);

  const scenarioKey =
    envelope?.scenario_key ||
    details?.suggested_action?.scenario_key ||
    details?.suggested_action?.rule_type ||
    ticket.scenarioKey;

  const scenarioTitle =
    (envelope as any)?.scenario_title ||
    getScenarioTitle(scenarioKey, envelope?.scenario_version);

  const formatFriendlyAction = (action?: string, ruleType?: string): string => {
    if (scenarioKey && scenarioKey !== 'consultation') {
      return scenarioTitle;
    }
    if (details?.suggested_action?.name) return details.suggested_action.name;
    if (ruleType === 'duplicate_task' || details?.suggested_action?.rule_type === 'duplicate_task')
      return 'Отмена дубликата';
    if (ruleType === 'service_redirect' || details?.suggested_action?.rule_type === 'service_redirect')
      return 'Перенаправление в другой раздел';
    if (action === 'grant_wlan') return 'Предоставление доступа к Wi-Fi';
    if (action === 'create_user') return 'Создание учетной записи';
    if (action === 'install_printer') return 'Установка принтера';
    return proposal?.title || scenarioTitle || 'Обработка первой линией';
  };

  const actionTitle =
    scenarioTitle !== 'Решение первой линии' && scenarioTitle !== 'Стандартная обработка 1-й линией'
      ? scenarioTitle
      : (envelopeOutcome.action
          ? formatFriendlyAction(envelopeOutcome.action, details?.suggested_action?.rule_type)
          : undefined) ||
        scenarioTitle ||
        proposal?.title ||
        details?.suggested_action?.name ||
        'Решение первой линии';

  const currentRunState = ticketRun?.state || 'pending';
  const runCfg = runStateConfig[currentRunState] || runStateConfig.pending;

  // Данные для превью в заголовке аккордеона
  const factsCount = Object.keys(envelope?.facts_summary || {}).length;

  return (
    <div className="rounded-2xl border border-neutral-200/90 bg-white p-4 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-3.5">
      {/* 1. Header: Статус TicketRun, режим автопилота и кнопка переанализа */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-neutral-100 dark:border-neutral-800">
        <div className="flex items-center gap-2">
          {/* Переключатель режима Помощник / Автопилот */}
          <div className="inline-flex rounded-lg border border-neutral-200 bg-neutral-100 p-0.5 dark:border-neutral-700 dark:bg-neutral-800 text-xs">
            <button
              type="button"
              onClick={() => handleSwitchRunMode('manual')}
              className={`rounded-md px-2 py-0.5 font-semibold transition-colors ${
                ticketRun?.mode !== 'autopilot'
                  ? 'bg-white text-neutral-900 shadow-2xs dark:bg-neutral-900 dark:text-neutral-100'
                  : 'text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200'
              }`}
            >
              Помощник
            </button>
            <button
              type="button"
              onClick={() => handleSwitchRunMode('autopilot')}
              className={`rounded-md px-2 py-0.5 font-semibold transition-colors ${
                ticketRun?.mode === 'autopilot'
                  ? 'bg-blue-600 text-white shadow-2xs'
                  : 'text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200'
              }`}
            >
              Автопилот
            </button>
          </div>

          {/* Статус-бейдж TicketRun */}
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${runCfg.bgClass} ${runCfg.textClass}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${runCfg.dotClass}`} />
            <span>{runCfg.label}</span>
          </span>

          {/* Пауза / Возобновление цикла */}
          {ticketRun && (
            <button
              type="button"
              onClick={handleTogglePauseRun}
              className="p-1 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 rounded transition-colors"
              title={ticketRun.state === 'paused' ? 'Возобновить цикл' : 'Приостановить цикл'}
            >
              {ticketRun.state === 'paused' ? <IconPlay size={13} /> : <IconPause size={13} />}
            </button>
          )}
        </div>

        <button
          type="button"
          onClick={hasAnalysisResult ? handleReanalyze : handleAnalyze}
          disabled={reanalyzing || loadingDetails}
          className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 bg-neutral-50 px-2.5 py-1 text-xs font-semibold text-neutral-700 outline-none transition-colors hover:bg-neutral-100 focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 dark:hover:bg-neutral-750 disabled:opacity-50"
        >
          <IconRefresh size={12} className={reanalyzing ? 'animate-spin text-blue-500' : ''} />
          <span>{reanalyzing ? 'Анализ...' : 'Обновить анализ'}</span>
        </button>
      </div>

      {/* 2. Staleness Alert: если тикет обновился на сервере */}
      {isStale && (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <div className="flex items-center gap-2">
            <IconAlertCircle size={14} className="shrink-0 text-amber-600" />
            <span>Данные заявки изменились на сервере. Рекомендуется обновить анализ.</span>
          </div>
          <button
            type="button"
            onClick={handleReanalyze}
            className="rounded-md bg-amber-600 px-2.5 py-1 font-semibold text-white hover:bg-amber-500"
          >
            Обновить
          </button>
        </div>
      )}

      {/* 3. HitL подтверждение команды автопилота */}
      {pendingCommand && (
        <div className="rounded-xl border border-blue-300 bg-blue-50/80 p-3 dark:border-blue-800 dark:bg-blue-950/40 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 font-bold text-blue-900 dark:text-blue-100 text-xs">
              <IconSparkles size={14} className="text-blue-600" />
              <span>Требуется одобрение действия автопилота</span>
            </div>
            <span className="rounded bg-blue-200 dark:bg-blue-900 px-1.5 py-0.5 font-mono text-[10px] text-blue-900 dark:text-blue-100 font-bold">
              HITL
            </span>
          </div>
          <p className="text-xs text-blue-950 dark:text-blue-200 leading-relaxed font-medium">
            Команда: <strong className="font-mono">{pendingCommand.action}</strong>
          </p>
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              onClick={handleHitlApprove}
              className="rounded-lg bg-blue-600 px-3 py-1 text-xs font-bold text-white hover:bg-blue-500 shadow-xs"
            >
              Одобрить и выполнить
            </button>
            <button
              type="button"
              onClick={handleHitlReject}
              className="rounded-lg border border-neutral-300 bg-white px-3 py-1 text-xs font-semibold text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
            >
              Отклонить
            </button>
          </div>
        </div>
      )}

      {/* 4. Предлагаемое решение, индикаторы и Оценка решения (НЕ спрятана!) */}
      <div className="rounded-xl border border-neutral-200/80 bg-neutral-50/60 p-3.5 dark:border-neutral-800 dark:bg-neutral-950/40 space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <span className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500 block">
              Рекомендованный сценарий
            </span>
            <h4 className="text-sm font-bold text-neutral-900 dark:text-neutral-100 mt-0.5">
              {actionTitle}
            </h4>
          </div>

          <span
            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold border ${
              isReady
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800'
                : 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800'
            }`}
          >
            {isReady ? <IconCheck size={11} /> : <IconAlertCircle size={11} />}
            <span>{isReady ? 'Готово к выполнению' : 'Требует уточнения'}</span>
          </span>
        </div>

        {/* Предупреждение о риске */}
        {isCriticalRisk && (
          <div className="rounded-lg border border-rose-200 bg-rose-50/80 p-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300 flex items-center gap-2">
            <IconAlertCircle size={14} className="shrink-0 text-rose-600" />
            <span>{riskWarning || 'Внимание: высокий риск простоя оборудования или сервиса.'}</span>
          </div>
        )}

        {/* Причины блокировки (если есть) */}
        {blockedReasons.length > 0 && (
          <div className="space-y-1 text-xs text-amber-800 dark:text-amber-300">
            {blockedReasons.map((reason, idx) => (
              <div key={idx} className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500 shrink-0" />
                <span>{reason}</span>
              </div>
            ))}
          </div>
        )}

        {/* Оценка решения (Feedback) - на первом плане, прямо в карточке решения */}
        <div className="flex items-center justify-between gap-2 pt-2 border-t border-neutral-200/60 dark:border-neutral-800 text-xs">
          <span className="text-neutral-500 dark:text-neutral-400 font-medium">
            Оценка решения:
          </span>

          {feedbackSubmitted ? (
            <span className="font-semibold text-emerald-600 dark:text-emerald-400 inline-flex items-center gap-1 text-[11px]">
              <IconCheck size={12} />
              <span>Спасибо за оценку</span>
            </span>
          ) : (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => {
                  setSelectedVerdict('correct');
                  handleSubmitFeedback('correct');
                }}
                className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-white px-2.5 py-1 font-medium text-neutral-700 hover:bg-emerald-50 hover:text-emerald-700 hover:border-emerald-200 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 transition-colors shadow-2xs"
                title="Рекомендация точна"
              >
                <span>👍 Точно</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setSelectedVerdict('incorrect');
                  setShowFeedbackDetails((prev) => !prev);
                }}
                className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-white px-2.5 py-1 font-medium text-neutral-700 hover:bg-rose-50 hover:text-rose-700 hover:border-rose-200 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 transition-colors shadow-2xs"
                title="Рекомендация неверна"
              >
                <span>👎 Неверно</span>
              </button>
            </div>
          )}
        </div>

        {/* Выпадающий выбор причины при отрицательной оценке */}
        {showFeedbackDetails && !feedbackSubmitted && (
          <div className="rounded-lg border border-neutral-200 bg-white p-2 text-xs space-y-1.5 dark:border-neutral-800 dark:bg-neutral-900 animate-in fade-in duration-100">
            <div className="font-semibold text-neutral-700 dark:text-neutral-300 text-[11px]">
              В чём неточность?
            </div>
            <div className="flex items-center gap-2">
              <select
                value={reasonCode}
                onChange={(e) => setReasonCode(e.target.value)}
                className="rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 flex-1 outline-none"
              >
                <option value="wrong_context">Неверно понята проблема</option>
                <option value="wrong_action">Неверное действие</option>
                <option value="wrong_status">Неверный статус</option>
                <option value="wrong_fact">Ошибочные факты (ПК/Принтер)</option>
              </select>
              <button
                type="button"
                onClick={() => {
                  handleSubmitFeedback('incorrect', reasonCode);
                  setShowFeedbackDetails(false);
                }}
                className="rounded-md bg-neutral-900 px-2.5 py-1 font-semibold text-white hover:bg-neutral-800 dark:bg-neutral-100 dark:text-neutral-900"
              >
                Отправить
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 5. Сворачиваемая техническая база и доказательства (FactBag, Регламент, RAG, Аудит) */}
      <div className="rounded-xl border border-neutral-200/80 bg-neutral-50/50 dark:border-neutral-800 dark:bg-neutral-950/30 overflow-hidden">
        <button
          type="button"
          onClick={() => setShowTechnicalEvidence((prev) => !prev)}
          className="flex items-center justify-between w-full p-3 text-left transition-colors hover:bg-neutral-100/70 dark:hover:bg-neutral-900/60 group"
          aria-expanded={showTechnicalEvidence}
        >
          <div className="flex items-center gap-2 flex-wrap min-w-0">
            <IconChevronRight
              size={13}
              className={`text-neutral-400 transition-transform duration-200 ${
                showTechnicalEvidence ? 'rotate-90 text-neutral-700 dark:text-neutral-200' : ''
              }`}
            />
            <span className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
              Техническая база и FactBag
            </span>

            {/* Компактные бейджи-индикаторы в свернутом виде */}
            {factsCount > 0 && (
              <span className="rounded-md bg-neutral-200/80 dark:bg-neutral-800 px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-neutral-600 dark:text-neutral-300">
                {factsCount} {factsCount === 1 ? 'факт' : factsCount < 5 ? 'факта' : 'фактов'}
              </span>
            )}

            {envelope?.facts_revision && (
              <span className="rounded-md bg-blue-50 dark:bg-blue-950 px-1.5 py-0.5 font-mono text-[10px] text-blue-700 dark:text-blue-300 border border-blue-200/60 dark:border-blue-900/60">
                v{envelope.facts_revision}
              </span>
            )}
          </div>

          <span className="text-[11px] font-medium text-neutral-400 group-hover:text-neutral-700 dark:group-hover:text-neutral-200 shrink-0">
            {showTechnicalEvidence ? 'Свернуть' : 'Развернуть'}
          </span>
        </button>

        {showTechnicalEvidence && (
          <div className="p-3 pt-1 border-t border-neutral-200/70 dark:border-neutral-800 space-y-2.5 animate-in fade-in duration-150">
            {/* Таб-бар */}
            <div className="flex items-center gap-1 overflow-x-auto border-b border-neutral-200/60 pb-1.5 dark:border-neutral-800">
              <button
                type="button"
                onClick={() => setSelectedTab('facts')}
                className={`rounded-lg px-2.5 py-1 text-xs font-semibold transition-colors ${
                  selectedTab === 'facts'
                    ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                    : 'text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200'
                }`}
              >
                Факты (FactBag)
              </button>

              <button
                type="button"
                onClick={() => setSelectedTab('completeness')}
                className={`rounded-lg px-2.5 py-1 text-xs font-semibold transition-colors ${
                  selectedTab === 'completeness'
                    ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                    : 'text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200'
                }`}
              >
                Журнал цикла
              </button>
            </div>

            {/* Контент табов */}
            <div>
              {/* Факты */}
              {selectedTab === 'facts' && (
                <FactBagSection
                  envelope={envelope}
                  onOpenOverride={() => setIsOverrideModalOpen(true)}
                />
              )}



              {/* Журнал цикла */}
              {selectedTab === 'completeness' && (
                <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                  {runEvents.length > 0 ? (
                    runEvents.map((ev) => (
                      <div
                        key={ev.id}
                        className="flex items-center justify-between rounded-lg border border-neutral-200/70 bg-white p-2 text-[11px] dark:border-neutral-800 dark:bg-neutral-900"
                      >
                        <div>
                          <div className="font-semibold text-neutral-900 dark:text-neutral-100">
                            {ev.event_type}
                          </div>
                          <div className="text-[10px] text-neutral-400">Инициатор: {ev.actor}</div>
                        </div>
                        <span className="font-mono text-[10px] text-neutral-400">
                          {ev.created_at ? new Date(ev.created_at).toLocaleTimeString() : ''}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="py-4 text-center text-xs text-neutral-400 italic">
                      Событий цикла пока не зафиксировано
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Модальное окно корректировки фактов (FactOverrideModal) */}
      <FactOverrideModal
        isOpen={isOverrideModalOpen}
        onClose={() => setIsOverrideModalOpen(false)}
        envelope={envelope}
        onSubmit={handleOverrideFacts}
        isSubmitting={decisionState.submitting}
      />
    </div>
  );
}
