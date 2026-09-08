import { useMemo, useState, type ReactNode } from 'react';
import type { Ticket } from '../../data/mock';
import type { TaskDetails } from '../../lib/types';
import {
  IconBuilding,
  IconChevronDown,
  IconMonitor,
  IconPaperclip,
  IconPhone,
  IconRefresh,
  IconUser,
} from '../Icons';
import type { DiagStatus } from './DiagnosticsSection';

interface TicketContextSummaryProps {
  ticket: Ticket;
  details: TaskDetails | null;
  commentsList: any[];
  attachmentsCount: number;
  hostList: string[];
  diagStatus: Record<string, DiagStatus>;
  onRunDiag: (host?: string) => void;
}

function getCommentText(comment: any): string {
  return comment?.text || comment?.Comments || comment?.Comment || comment?.Description || '';
}

function getCommentAuthor(comment: any): string {
  return comment?.author || comment?.Editor || comment?.UserName || comment?.Creator || 'Сотрудник';
}

function formatCommentDate(comment: any): string {
  const raw = comment?.created || comment?.Date || comment?.Created;
  if (!raw) return '';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function TicketContextSummary({
  ticket,
  details,
  commentsList,
  attachmentsCount,
  hostList,
  diagStatus,
  onRunDiag,
}: TicketContextSummaryProps) {
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const description = ticket.description || details?.description || '';
  const latestComment = useMemo(
    () => [...commentsList].reverse().find((comment) => getCommentText(comment).trim()),
    [commentsList]
  );
  const location = [
    ticket.room || details?.room ? `каб. ${ticket.room || details?.room}` : '',
    ticket.department || details?.department || '',
  ]
    .filter(Boolean)
    .join(' · ');
  const phone = ticket.requesterPhone || details?.phone || '';
  const requester = ticket.requesterName || details?.creator || '';
  const isPrivate = Boolean(latestComment?.is_private || latestComment?.IsPrivate);

  const meta = [
    requester
      ? { key: 'requester', label: 'Заявитель', value: requester, icon: <IconUser size={14} /> }
      : null,
    phone ? { key: 'phone', label: 'Телефон', value: phone, icon: <IconPhone size={14} /> } : null,
    location
      ? { key: 'location', label: 'Расположение', value: location, icon: <IconBuilding size={14} /> }
      : null,
    ticket.executors
      ? { key: 'executor', label: 'Исполнитель', value: ticket.executors, icon: <IconUser size={14} /> }
      : null,
  ].filter(Boolean) as Array<{ key: string; label: string; value: string; icon: ReactNode }>;

  return (
    <section className="rounded-2xl border border-neutral-200/90 bg-white shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <div className="space-y-4 p-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] text-neutral-500 dark:text-neutral-400">
            Контекст заявки
          </h3>
          <div className="flex items-center gap-2 text-[11px] text-neutral-500 dark:text-neutral-400">
            {attachmentsCount > 0 && (
              <span className="inline-flex items-center gap-1">
                <IconPaperclip size={13} />
                {attachmentsCount}
              </span>
            )}
            <span className="rounded-md bg-neutral-100 px-2 py-1 font-medium dark:bg-neutral-800">
              {ticket.serviceName}
            </span>
          </div>
        </div>

        <div className="space-y-1.5">
          <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-neutral-500 dark:text-neutral-400">
            Описание
          </div>
          {description ? (
            <>
              <p
                className={`whitespace-pre-wrap text-[13px] leading-5 text-neutral-800 dark:text-neutral-200 ${
                  descriptionExpanded ? '' : 'line-clamp-4'
                }`}
              >
                {description}
              </p>
              {description.length > 220 && (
                <button
                  type="button"
                  onClick={() => setDescriptionExpanded((value) => !value)}
                  aria-expanded={descriptionExpanded}
                  className="inline-flex min-h-9 items-center gap-1 rounded-md text-xs font-semibold text-neutral-600 outline-none transition-colors hover:text-neutral-950 focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-neutral-400 dark:hover:text-neutral-100"
                >
                  {descriptionExpanded ? 'Свернуть описание' : 'Показать полностью'}
                  <IconChevronDown
                    size={13}
                    className={`transition-transform ${descriptionExpanded ? 'rotate-180' : ''}`}
                  />
                </button>
              )}
            </>
          ) : (
            <p className="text-[13px] text-neutral-500 dark:text-neutral-400">Описание не заполнено</p>
          )}
        </div>

        {latestComment && (
          <div className="border-l-2 border-neutral-300 pl-3 dark:border-neutral-700">
            <div className="mb-1 flex items-center gap-2 text-[10.5px] text-neutral-500 dark:text-neutral-400">
              <span className="font-semibold text-neutral-700 dark:text-neutral-300">
                Последний комментарий · {getCommentAuthor(latestComment)}
              </span>
              {isPrivate && (
                <span className="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                  Служебный
                </span>
              )}
              <span className="ml-auto shrink-0 font-mono">{formatCommentDate(latestComment)}</span>
            </div>
            <p className="line-clamp-2 text-xs leading-5 text-neutral-700 dark:text-neutral-300">
              {getCommentText(latestComment)}
            </p>
          </div>
        )}

        {meta.length > 0 && (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(145px,1fr))] gap-2 border-t border-neutral-100 pt-3 dark:border-neutral-800">
            {meta.map((item) => (
              <div key={item.key} className="flex min-w-0 items-center gap-2.5 rounded-lg bg-neutral-50 px-2.5 py-2 dark:bg-neutral-800/50">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-white text-neutral-500 shadow-xs dark:bg-neutral-900 dark:text-neutral-400">
                  {item.icon}
                </span>
                <span className="min-w-0">
                  <span className="block text-[9.5px] font-bold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                    {item.label}
                  </span>
                  <span className="block truncate text-xs font-semibold text-neutral-900 dark:text-neutral-100" title={item.value}>
                    {item.value}
                  </span>
                </span>
              </div>
            ))}
          </div>
        )}

        {hostList.length > 0 && (
          <div className="flex items-center justify-between gap-3 border-t border-neutral-100 pt-3 dark:border-neutral-800">
            <div className="flex min-w-0 items-center gap-2.5">
              <span
                className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                  diagStatus.ping === 'ok'
                    ? 'bg-emerald-500'
                    : diagStatus.ping === 'fail'
                    ? 'bg-rose-500'
                    : diagStatus.ping === 'checking'
                    ? 'animate-pulse bg-blue-500'
                    : 'bg-neutral-400'
                }`}
              />
              <IconMonitor size={14} className="shrink-0 text-neutral-500" />
              <div className="min-w-0">
                <div className="text-[9.5px] font-bold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                  {hostList.length === 1 ? 'Рабочая станция' : `Рабочие станции · ${hostList.length}`}
                </div>
                <div className="truncate font-mono text-xs font-semibold text-neutral-900 dark:text-neutral-100" title={hostList.join(', ')}>
                  {hostList.join(', ')}
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => onRunDiag()}
              disabled={diagStatus.ping === 'checking'}
              className="inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-700 outline-none transition-colors hover:bg-neutral-100 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              <IconRefresh size={13} className={diagStatus.ping === 'checking' ? 'animate-spin' : ''} />
              {diagStatus.ping === 'checking' ? 'Проверка' : 'Диагностика'}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
