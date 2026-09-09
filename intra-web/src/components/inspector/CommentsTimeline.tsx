import React from 'react';
import { IconChevronDown, IconSparkles, IconCheckCircle } from '../Icons';
import type { TicketSummaryResult } from '../../lib/types';
import {
  filterMeaningfulComments,
  formatCommentsCount,
  getCommentText,
} from './commentsUtils';

interface CommentsTimelineProps {
  commentsList: any[];
  loadingDetails: boolean;
  isCommentsExpanded: boolean;
  onToggleCommentsExpanded: () => void;
  expandedMode?: boolean;
  aiSummarySlot?: React.ReactNode;
  aiSummary?: TicketSummaryResult | null;
  loadingAiSummary?: boolean;
  onGenerateAiSummary?: () => void;
}

function formatTime(d: Date | string) {
  const dateObj = typeof d === 'string' ? new Date(d) : d;
  if (isNaN(dateObj.getTime())) return '';
  return dateObj.toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function CommentsTimeline({
  commentsList,
  loadingDetails,
  isCommentsExpanded,
  onToggleCommentsExpanded,
  expandedMode = false,
  aiSummarySlot,
  aiSummary,
  loadingAiSummary,
  onGenerateAiSummary,
}: CommentsTimelineProps) {
  const isVisible = expandedMode || isCommentsExpanded;

  const meaningfulComments = React.useMemo(
    () => filterMeaningfulComments(commentsList),
    [commentsList]
  );

  return (
    <div
      className={`rounded-2xl border border-neutral-200/90 bg-white p-4 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-3 ${expandedMode ? 'flex flex-col flex-1 min-h-0' : ''
        }`}
    >
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onToggleCommentsExpanded}
          aria-expanded={isVisible}
          className="group flex min-h-8 items-center gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
          <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-neutral-500 group-hover:text-neutral-900 dark:group-hover:text-neutral-100 transition-colors">
            История переписки
          </span>
          <span className="rounded-full bg-neutral-100 px-2 py-0.5 font-mono text-[10.5px] font-semibold text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
            {loadingDetails ? 'Загрузка...' : formatCommentsCount(meaningfulComments.length)}
          </span>
          <IconChevronDown
            size={14}
            className={`text-neutral-400 transition-transform duration-200 ${isVisible ? 'rotate-180' : ''
              }`}
          />
        </button>

        {/* Кнопка генерации AI-сводки прямо в шапке ленты */}
        {onGenerateAiSummary && !aiSummary && meaningfulComments.length > 0 && (
          <button
            type="button"
            onClick={onGenerateAiSummary}
            disabled={loadingAiSummary}
            className="inline-flex items-center gap-1.5 rounded-lg border border-purple-200 bg-purple-50/70 px-2.5 py-1 text-xs font-semibold text-purple-700 outline-none transition-colors hover:bg-purple-100 dark:border-purple-900/60 dark:bg-purple-950/40 dark:text-purple-300 dark:hover:bg-purple-900/50 disabled:opacity-50"
            title="Сформировать краткую AI-выжимку переписки"
          >
            <IconSparkles size={12} className={loadingAiSummary ? 'animate-spin' : ''} />
            <span>{loadingAiSummary ? 'Анализ переписки...' : 'AI-сводка переписки'}</span>
          </button>
        )}
      </div>

      {isVisible && (
        <div
          className={`space-y-3 pt-2 border-t border-neutral-100 dark:border-neutral-800 ${expandedMode ? 'flex-1 flex flex-col min-h-0' : ''
            }`}
        >
          {/* AI Summary Slot / Card */}
          {aiSummarySlot ? (
            aiSummarySlot
          ) : aiSummary ? (
            <div className="rounded-xl border border-purple-200/80 bg-purple-50/50 p-3 dark:border-purple-900/50 dark:bg-purple-950/20 text-xs space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 font-bold text-purple-900 dark:text-purple-200 text-[11.5px]">
                  <IconSparkles size={13} className="text-purple-600 dark:text-purple-400" />
                  <span>AI-сводка переписки (TL;DR)</span>
                </div>
                {aiSummary.current_status && (
                  <span className="rounded bg-purple-100 dark:bg-purple-900/60 px-1.5 py-0.5 text-[10px] font-semibold text-purple-800 dark:text-purple-300">
                    {aiSummary.current_status}
                  </span>
                )}
              </div>

              {aiSummary.core_problem && (
                <div className="text-neutral-800 dark:text-neutral-200 leading-relaxed font-medium">
                  {aiSummary.core_problem}
                </div>
              )}

              {aiSummary.actions_taken && aiSummary.actions_taken.length > 0 && (
                <div className="space-y-1 pt-1 border-t border-purple-200/60 dark:border-purple-900/40">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-purple-700 dark:text-purple-400">
                    Предпринятые действия:
                  </span>
                  <ul className="space-y-0.5 pl-3 list-disc text-neutral-700 dark:text-neutral-300 text-[11.5px]">
                    {aiSummary.actions_taken.map((action, idx) => (
                      <li key={idx}>{action}</li>
                    ))}
                  </ul>
                </div>
              )}

              {aiSummary.recommended_next_step && (
                <div className="flex items-start gap-1.5 pt-1 text-[11px] text-purple-800 dark:text-purple-300 font-medium">
                  <IconCheckCircle size={13} className="shrink-0 mt-0.5" />
                  <span>Рекомендация: {aiSummary.recommended_next_step}</span>
                </div>
              )}
            </div>
          ) : null}

          {/* Comments Stream */}
          <div
            className={`space-y-2 overflow-y-auto pr-1 ${expandedMode ? 'flex-1 min-h-0' : 'max-h-[380px]'
              }`}
          >
            {meaningfulComments.map((c: any, idx: number) => {
              const author = c.author || c.Editor || c.UserName || c.Creator || 'Сотрудник';
              const text = getCommentText(c);
              const isPrivate = Boolean(c.is_private || c.IsPrivate);
              const created = c.created || c.Date || c.Created || '';
              const commentId = c.id || c.Id || idx;
              if (!text) return null;

              return (
                <div
                  key={commentId}
                  className={`rounded-xl border p-3 text-[12.5px] transition-colors ${isPrivate
                      ? 'border-amber-200/80 bg-amber-50/40 dark:border-amber-800/50 dark:bg-amber-950/20'
                      : 'border-neutral-200/80 bg-neutral-50/60 dark:border-neutral-800 dark:bg-neutral-900/60'
                    }`}
                >
                  <div className="flex items-center justify-between mb-1.5 gap-2">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="font-semibold text-neutral-900 dark:text-neutral-100 truncate">
                        {author}
                      </span>
                      {isPrivate && (
                        <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-900 dark:bg-amber-900/80 dark:text-amber-200">
                          Служебный
                        </span>
                      )}
                    </div>
                    <span className="shrink-0 font-mono text-[10.5px] text-neutral-400">
                      {formatTime(created)}
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap leading-relaxed text-neutral-800 dark:text-neutral-200">
                    {text}
                  </p>
                </div>
              );
            })}

            {meaningfulComments.length === 0 && !loadingDetails && (
              <div className="py-6 text-center text-xs italic text-neutral-400">
                В этой заявке пока нет комментариев
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
