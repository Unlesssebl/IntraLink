import { useState } from 'react';
import type { Ticket } from '../../data/mock';
import { getStatusDotClass } from '../../data/mock';
import type { TaskDetails } from '../../lib/types';
import type { TicketRun } from '../../lib/ticketRuns';
import { IconBolt, IconSparkles, IconClose } from '../Icons';

interface AiTriageCardProps {
  ticket: Ticket;
  details: TaskDetails | null;
  ticketRun?: TicketRun | null;
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

export default function AiTriageCard({
  ticket,
  details,
  ticketRun,
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
  const [showEvidence, setShowEvidence] = useState(false);
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
  const blockedReasons = readiness?.blocked_reasons
    || decision?.completeness.blocked_reasons
    || suggestion?.missing_data
    || [];

  return (
    <section className="overflow-hidden rounded-xl border border-neutral-200/80 bg-white shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-start justify-between gap-4 border-b border-neutral-100 px-4 py-3 dark:border-neutral-800">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`h-2 w-2 shrink-0 rounded-full ${isStale ? 'bg-amber-500' : isReady ? 'bg-emerald-500' : 'bg-rose-500'}`} />
            <h3 className="text-sm font-semibold text-neutral-950 dark:text-neutral-50">
              {decision?.proposal.title || ticket.aiPlan?.actionTitle || 'Предложение по заявке'}
            </h3>
            <span className={`rounded-md px-2 py-0.5 text-[10.5px] font-semibold ${isStale ? 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300' : isReady ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300' : 'bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300'}`}>
              {isStale ? 'Требует перерасчёта' : isReady ? 'Готово к проверке' : 'Данных недостаточно'}
            </span>
          </div>
          <p className="mt-1 text-[11.5px] text-neutral-500 dark:text-neutral-400">
            {decision?.proposal.consequences || 'Перед выполнением проверьте ответ и целевой статус.'}
            {decision && <span className="ml-1 font-mono">Решение v{decision.version}</span>}
          </p>
        </div>
        <button type="button" onClick={onReanalyze} disabled={reanalyzing} className="shrink-0 rounded-md border border-neutral-200 px-2.5 py-1.5 text-[11px] font-semibold text-neutral-700 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800">
          {reanalyzing ? 'Перерасчёт…' : 'Пересчитать'}
        </button>
      </div>

      <div className="space-y-3 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          {Object.entries(sources).filter(([, active]) => active).map(([source]) => (
            <span key={source} className="inline-flex items-center gap-1.5 rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] font-medium text-neutral-700 dark:border-neutral-700 dark:bg-neutral-850 dark:text-neutral-300">
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
              <button type="button" onClick={onResetStatusOverride} title="Вернуть предложенный статус" className="ml-0.5 hover:text-rose-600"><IconClose size={10} /></button>
            )}
          </span>
        </div>

        {blockedReasons.length > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-[11.5px] text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            {blockedReasons.join(' · ')}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <button type="button" onClick={() => setShowEvidence(value => !value)} className="text-[11.5px] font-medium text-neutral-600 hover:text-neutral-950 dark:text-neutral-400 dark:hover:text-neutral-100">
            {showEvidence ? 'Скрыть основания' : 'Показать основания'}
          </button>
          <div className="flex items-center gap-3">
            {details?.ai_suggested_resolution && details.ai_suggested_resolution !== replyText && (
              <button type="button" onClick={() => onInsertAiSynthesis(details.ai_suggested_resolution!)} className="text-[11.5px] font-semibold text-blue-600 hover:text-blue-700 dark:text-blue-400">Подставить AI-ответ</button>
            )}
            <label className="flex items-center gap-1.5 text-[11.5px] text-neutral-500">
              <span>Минуты</span>
              <input type="number" value={expenses} min={0} max={240} onChange={event => onChangeExpenses(Number(event.target.value))} className="h-7 w-14 rounded-md border border-neutral-200 bg-neutral-50 px-1.5 text-center font-mono text-xs font-semibold text-neutral-900 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100" />
            </label>
          </div>
        </div>

        {showEvidence && (
          <div className="grid gap-2 border-t border-neutral-100 pt-3 text-[11px] dark:border-neutral-800 sm:grid-cols-3">
            {(decision?.steps || []).map(step => (
              <div key={step.id} className="rounded-lg bg-neutral-50 p-2.5 dark:bg-neutral-850">
                <div className="font-semibold text-neutral-800 dark:text-neutral-200">{sourceLabels[step.component] || step.component}</div>
                <div className="mt-1 text-neutral-500 dark:text-neutral-400">{step.status}{step.error_code ? ` · ${step.error_code}` : ''}</div>
              </div>
            ))}
            {decision?.completeness && (
              <div className="rounded-lg bg-neutral-50 p-2.5 dark:bg-neutral-850">
                <div className="font-semibold text-neutral-800 dark:text-neutral-200">Полнота контекста</div>
                <div className="mt-1 text-neutral-500 dark:text-neutral-400">
                  История: {decision.completeness.history_used ?? 0}/{decision.completeness.history_total ?? 0} · Вложения: {decision.completeness.attachments_read ?? 0}/{decision.completeness.attachments_total ?? 0}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
