import React from 'react';
import type { Ticket } from '../../data/mock';
import { getStatusDotClass } from '../../data/mock';
import type { TaskDetails } from '../../lib/types';
import { IconBolt, IconSparkles, IconClose } from '../Icons';

interface AiTriageCardProps {
  ticket: Ticket;
  details: TaskDetails | null;
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

export default function AiTriageCard({
  ticket,
  details,
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
  const suggestion = details?.ai_suggestion;
  const calculatedAt = suggestion?.calculated_at
    ? new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(suggestion.calculated_at))
    : null;
  return (
    <div className="flex items-center justify-between gap-2 flex-wrap text-xs pb-1 border-b border-neutral-200/80 dark:border-neutral-800/80">
      <div className="flex items-center gap-2 flex-wrap">
        {/* Rule Engine & AI Solution Badges */}
        {ticket.hasRuleEngine && (
          <span className="px-2 py-0.5 rounded text-[11px] font-semibold border border-blue-300 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/70 text-blue-700 dark:text-blue-300 inline-flex items-center gap-1">
            <IconBolt size={10} className="shrink-0" />
            <span>Rule Engine</span>
            {ticket.aiPlan?.actionBadge && <span className="opacity-75 font-normal">({ticket.aiPlan.actionBadge})</span>}
          </span>
        )}
        {ticket.hasAiSolution && (
          <span className="px-2 py-0.5 rounded text-[11px] font-semibold border border-purple-300 dark:border-purple-800 bg-purple-50 dark:bg-purple-950/70 text-purple-700 dark:text-purple-300 inline-flex items-center gap-1">
            <IconSparkles size={10} className="text-purple-600 dark:text-purple-400" />
            <span>AI Решение</span>
          </span>
        )}

        {suggestion && (
          <span
            className={`px-2 py-0.5 rounded text-[11px] font-semibold border inline-flex items-center gap-1 ${
              suggestion.state === 'stale'
                ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200'
                : 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-200'
            }`}
            title={suggestion.state === 'stale' ? suggestion.stale_reason : 'Рекомендация совпадает с текущим состоянием заявки'}
          >
            <span>{suggestion.state === 'stale' ? 'AI: неактуально' : 'AI: актуально'}</span>
          </span>
        )}

        {/* Zero Trust DLP Circuit */}
        <span
          className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded border inline-flex items-center gap-1 ${
            details?.circuit === 'red'
              ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30'
              : details?.circuit === 'yellow'
              ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30'
              : 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
          }`}
          title={details?.circuit_reason ? `Контур безопасности: ${details.circuit_reason}` : `Контур данных: ${details?.circuit || 'green'}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${details?.circuit === 'red' ? 'bg-rose-500' : details?.circuit === 'yellow' ? 'bg-amber-500' : 'bg-emerald-500'}`} />
          <span>{details?.circuit ? details.circuit.toUpperCase() : 'GREEN'}</span>
        </span>

        {/* Target Status Pill */}
        <div className="flex items-center gap-1.5 px-2 py-0.5 bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md text-[11.5px] font-medium text-neutral-800 dark:text-neutral-200">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${getStatusDotClass(targetStatusId)}`} />
          <span>Целевой статус: <strong>{targetStatusName}</strong></span>
          {selectedStatusOverride !== null && (
            <button
              type="button"
              onClick={onResetStatusOverride}
              className="hover:text-rose-600 font-bold ml-1 cursor-pointer p-0.5 inline-flex items-center"
              title="Сбросить статус к стандартному"
            >
              <IconClose size={10} className="shrink-0" />
            </button>
          )}
        </div>
      </div>

      {suggestion && (
        <div className="w-full flex items-center gap-x-3 gap-y-1 flex-wrap text-[10.5px] text-neutral-500 dark:text-neutral-400 -mt-0.5">
          <span title="Источник рекомендации">Источник: {suggestion.source}</span>
          {calculatedAt && <span>Расчёт: {calculatedAt}</span>}
          <span className={suggestion.policy.blocked ? 'text-rose-600 dark:text-rose-400 font-semibold' : 'text-amber-700 dark:text-amber-300'}>
            Policy: {suggestion.policy.blocked ? 'заблокировано' : suggestion.policy.mode === 'confirm' ? 'требуется подтверждение' : suggestion.policy.mode}
          </span>
          {suggestion.missing_data.length > 0 && (
            <span className="text-rose-600 dark:text-rose-400 font-semibold">Не хватает: {suggestion.missing_data.join(', ')}</span>
          )}
          {suggestion.policy.blocked && <span className="text-rose-600 dark:text-rose-400">{suggestion.policy.reason}</span>}
        </div>
      )}

      <div className="flex items-center gap-2">
        {/* Кнопка ручного перезапуска анализа */}
        <button
          type="button"
          onClick={onReanalyze}
          disabled={reanalyzing}
          className="text-[11.5px] text-neutral-600 dark:text-neutral-300 hover:text-blue-600 dark:hover:text-blue-400 font-medium cursor-pointer inline-flex items-center gap-1 shrink-0 px-2 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-750 border border-neutral-200 dark:border-neutral-700 transition-colors disabled:opacity-50"
          title="Принудительно сбросить кэш и перепрогнать правила и AI-синтез для этой заявки"
        >
          <svg className={`w-3 h-3 ${reanalyzing ? 'animate-spin text-blue-500' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
          </svg>
          <span>{reanalyzing ? 'Анализ...' : 'Переанализировать'}</span>
        </button>

        {/* If AI synthesized text is available and not applied */}
        {details?.ai_suggested_resolution && details.ai_suggested_resolution !== replyText && (
          <button
            type="button"
            onClick={() => onInsertAiSynthesis(details.ai_suggested_resolution!)}
            className="text-[11.5px] text-purple-600 dark:text-purple-400 hover:underline font-semibold cursor-pointer inline-flex items-center gap-1 shrink-0"
            title="Подставить ответ, сформированный AI"
          >
            <IconSparkles size={11} />
            <span>Вставить ответ AI</span>
          </button>
        )}

        {/* Expenses Input */}
        <div className="flex items-center gap-1 text-[12px] text-neutral-500 dark:text-neutral-400 font-medium">
          <span>Списание:</span>
          <input
            type="number"
            value={expenses}
            onChange={e => onChangeExpenses(Number(e.target.value))}
            min={0}
            max={240}
            className="w-12 h-6 px-1 bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded text-neutral-900 dark:text-neutral-100 text-center font-mono font-bold text-[11.5px]"
          />
          <span>мин</span>
        </div>
      </div>
    </div>
  );
}
