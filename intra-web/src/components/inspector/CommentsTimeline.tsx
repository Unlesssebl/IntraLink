import React from 'react';
import { IconChevronDown } from '../Icons';

interface CommentsTimelineProps {
  commentsList: any[];
  loadingDetails: boolean;
  isCommentsExpanded: boolean;
  onToggleCommentsExpanded: () => void;
  expandedMode?: boolean;
  aiSummarySlot?: React.ReactNode;
}

function formatTime(d: Date | string) {
  const dateObj = typeof d === 'string' ? new Date(d) : d;
  if (isNaN(dateObj.getTime())) return '';
  return dateObj.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function formatCommentsCount(count: number) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${count} комментариев`;
  if (mod10 === 1) return `${count} комментарий`;
  if (mod10 >= 2 && mod10 <= 4) return `${count} комментария`;
  return `${count} комментариев`;
}

export default function CommentsTimeline({
  commentsList,
  loadingDetails,
  isCommentsExpanded,
  onToggleCommentsExpanded,
  expandedMode = false,
  aiSummarySlot,
}: CommentsTimelineProps) {
  const isVisible = expandedMode || isCommentsExpanded;

  return (
    <div className={`bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-3.5 shadow-xs space-y-2.5 ${expandedMode ? 'flex flex-col flex-1 min-h-0' : ''}`}>
      <button
        type="button"
        onClick={onToggleCommentsExpanded}
        aria-expanded={isVisible}
        className="group flex min-h-9 w-full items-center justify-between rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-500 group-hover:text-neutral-800 dark:group-hover:text-neutral-200 transition-colors">
            Комментарии заявки
          </span>
          <span className="text-[10.5px] px-2 py-0.2 rounded-full bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 font-semibold font-mono">
            {loadingDetails ? 'Загрузка...' : formatCommentsCount(commentsList.length)}
          </span>
        </div>
        <div className="flex items-center gap-1 text-[11.5px] text-neutral-400 group-hover:text-neutral-600 dark:group-hover:text-neutral-200">
          <span>{isVisible ? 'Свернуть' : 'Развернуть'}</span>
          <IconChevronDown size={14} className={`transition-transform duration-200 ${isVisible ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {isVisible && (
        <div className={`space-y-2.5 pt-1 border-t border-neutral-100 dark:border-neutral-800 ${expandedMode ? 'flex-1 flex flex-col min-h-0' : ''}`}>
          {/* AI Summary Slot (TL;DR) */}
          {aiSummarySlot}

          {/* Comments Stream */}
          <div className={`space-y-2 overflow-y-auto pr-1 ${expandedMode ? 'flex-1 min-h-0' : 'max-h-[350px]'}`}>
            {commentsList.map((c: any, idx: number) => {
              const author = c.author || c.Editor || c.UserName || c.Creator || 'Сотрудник';
              const text = c.text || c.Comments || c.Comment || c.Description || '';
              const isPrivate = Boolean(c.is_private || c.IsPrivate);
              const created = c.created || c.Date || c.Created || '';
              const commentId = c.id || c.Id || idx;
              if (!text) return null;
              return (
                <div
                  key={commentId}
                  className={`p-3 rounded-lg border text-[12.5px] ${
                    isPrivate
                      ? 'border-amber-200 dark:border-amber-800/60 bg-amber-50/50 dark:bg-amber-950/20'
                      : 'border-neutral-200 dark:border-neutral-800 bg-neutral-50/70 dark:bg-neutral-950/40'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-semibold text-neutral-900 dark:text-neutral-100">{author}</span>
                      {isPrivate && (
                        <span className="text-[10px] bg-amber-100 text-amber-900 dark:bg-amber-900/80 dark:text-amber-200 px-1.5 py-0.2 rounded font-bold">
                          Служебный
                        </span>
                      )}
                    </div>
                    <span className="text-[11px] text-neutral-400 font-mono">{formatTime(created)}</span>
                  </div>
                  <p className="text-neutral-800 dark:text-neutral-200 leading-relaxed whitespace-pre-wrap">{text}</p>
                </div>
              );
            })}

            {commentsList.length === 0 && !loadingDetails && (
              <div className="text-[12.5px] text-neutral-400 italic py-4 text-center">
                В этой заявке пока нет комментариев
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
