import React from 'react';
import type { TicketSummaryResult } from '../../lib/types';
import { IconSparkles } from '../Icons';

interface AiSummarySectionProps {
  commentsCount: number;
  aiSummary: TicketSummaryResult | null;
  loadingAiSummary: boolean;
  isAiSummaryExpanded: boolean;
  onToggleAiSummaryExpanded: () => void;
  onGenerateAiSummary: () => void;
}

export default function AiSummarySection({
  commentsCount,
  aiSummary,
  loadingAiSummary,
  isAiSummaryExpanded,
  onToggleAiSummaryExpanded,
  onGenerateAiSummary,
}: AiSummarySectionProps) {
  if (commentsCount < 2) return null;

  return (
    <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl overflow-hidden bg-neutral-50/60 dark:bg-neutral-900/40 shadow-xs mb-3">
      {!aiSummary && !loadingAiSummary ? (
        <div className="p-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-neutral-700 dark:text-neutral-300">
            <IconSparkles size={14} className="text-neutral-500 shrink-0" />
            <span className="truncate">Цепочка из {commentsCount} сообщений</span>
          </div>
          <button
            type="button"
            onClick={onGenerateAiSummary}
            className="px-3 py-1.5 bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 rounded-lg text-xs font-semibold flex items-center gap-1.5 cursor-pointer shadow-xs transition-colors shrink-0"
          >
            <IconSparkles size={12} />
            <span>AI Сводка (TL;DR)</span>
          </button>
        </div>
      ) : loadingAiSummary ? (
        <div className="p-3.5 flex items-center justify-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
          <svg className="animate-spin h-4 w-4 text-blue-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
          </svg>
          <span>AI Hub анализирует цепочку переписки...</span>
        </div>
      ) : aiSummary ? (
        <div className="p-3.5 space-y-2.5 text-xs">
          <div className="flex items-center justify-between border-b border-neutral-200 dark:border-neutral-800 pb-2">
            <div className="flex items-center gap-1.5 font-bold text-neutral-900 dark:text-neutral-100">
              <IconSparkles size={14} className="text-neutral-500" />
              <span>AI Сводка диалога (TL;DR)</span>
            </div>
            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={onGenerateAiSummary}
                className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline cursor-pointer font-medium"
              >
                Обновить
              </button>
              <button
                type="button"
                onClick={onToggleAiSummaryExpanded}
                className="text-[11px] text-neutral-500 hover:underline cursor-pointer"
              >
                {isAiSummaryExpanded ? 'Свернуть' : 'Развернуть'}
              </button>
            </div>
          </div>

          {isAiSummaryExpanded && (
            <div className="space-y-2 pt-0.5">
              <div>
                <span className="font-semibold text-neutral-800 dark:text-neutral-200">Суть проблемы: </span>
                <span className="text-neutral-700 dark:text-neutral-300">{aiSummary.core_problem}</span>
              </div>
              {aiSummary.actions_taken && aiSummary.actions_taken.length > 0 && (
                <div>
                  <span className="font-semibold text-neutral-800 dark:text-neutral-200">Предпринятые действия: </span>
                  <ul className="list-disc list-inside text-neutral-600 dark:text-neutral-400 space-y-0.5 pl-1">
                    {aiSummary.actions_taken.map((act, i) => (
                      <li key={i}>{act}</li>
                    ))}
                  </ul>
                </div>
              )}
              {aiSummary.current_status && (
                <div>
                  <span className="font-semibold text-neutral-800 dark:text-neutral-200">Текущее состояние: </span>
                  <span className="text-neutral-700 dark:text-neutral-300">{aiSummary.current_status}</span>
                </div>
              )}
              {aiSummary.recommended_next_step && (
                <div className="bg-neutral-100 dark:bg-neutral-800/80 p-2.5 rounded-lg border border-neutral-200 dark:border-neutral-700/80">
                  <span className="font-semibold text-neutral-900 dark:text-neutral-100">Рекомендованный шаг: </span>
                  <span className="text-neutral-700 dark:text-neutral-300">{aiSummary.recommended_next_step}</span>
                </div>
              )}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
