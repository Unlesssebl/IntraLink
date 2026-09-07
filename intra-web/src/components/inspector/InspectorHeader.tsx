import React from 'react';
import type { Ticket } from '../../data/mock';
import { statusConfig } from '../../data/mock';
import { IconExternalLink, IconCopy } from '../Icons';

interface InspectorHeaderProps {
  rawId: number;
  ticket: Ticket;
  expanded: boolean;
  onToggleExpanded: () => void;
  onClose: () => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

export default function InspectorHeader({
  rawId,
  ticket,
  expanded,
  onToggleExpanded,
  onClose,
  onToast,
}: InspectorHeaderProps) {
  const copyId = () => {
    navigator.clipboard.writeText(String(rawId)).then(() => {
      onToast({ type: 'info', message: `ID #${rawId} скопирован в буфер` });
    });
  };

  return (
    <div className="px-4 py-3.5 bg-white dark:bg-neutral-900 border-b border-neutral-200 dark:border-neutral-800 shrink-0 sticky top-0 z-20">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-md text-neutral-500 outline-none transition-colors hover:bg-neutral-100 hover:text-neutral-800 focus-visible:ring-2 focus-visible:ring-blue-500 dark:hover:bg-neutral-800 dark:hover:text-neutral-200"
            title="Закрыть панель (Esc)"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M10 4l-4 4 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>

          <button
            onClick={copyId}
            className="inline-flex min-h-9 items-center gap-1 rounded-md font-mono text-[14px] font-bold text-neutral-800 outline-none transition-colors hover:text-blue-600 focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-neutral-200 dark:hover:text-blue-400"
            title="Нажмите, чтобы скопировать ID"
          >
            <span>#{rawId}</span>
            <IconCopy size={12} className="opacity-60" />
          </button>

          <span
            className={`h-6 px-2.5 rounded-full font-semibold text-[11.5px] inline-flex items-center gap-1.5 ${statusConfig[ticket.status].className}`}
            title={`Статус заявки: ${ticket.statusName || statusConfig[ticket.status].label}`}
          >
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusConfig[ticket.status].dotClass}`} />
            <span>{ticket.statusName || statusConfig[ticket.status].label}</span>
          </span>

          {(ticket.isDuplicate || ticket.ruleType === 'duplicate_task') && (
            <a
              href={`/admin/api/tasks/${ticket.duplicateInfo?.master_task_id || rawId}/open`}
              target="_blank"
              rel="noreferrer"
              className="h-6 px-2.5 rounded-md font-semibold text-[11.5px] bg-amber-50 dark:bg-amber-950/60 text-amber-800 dark:text-amber-200 border border-amber-200 dark:border-amber-800/70 hover:bg-amber-100 transition-colors inline-flex items-center gap-1 cursor-pointer"
              title={`Открыть основную заявку #${ticket.duplicateInfo?.master_task_id || ''}`}
            >
              <span>Дубликат №{ticket.duplicateInfo?.master_task_id || '—'}</span>
              <IconExternalLink size={10} />
            </a>
          )}
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <a
            href={`/admin/api/tasks/${rawId}/open`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-9 items-center gap-1 rounded-md border border-neutral-200 bg-neutral-100 px-2.5 text-[12px] font-medium text-neutral-700 outline-none transition-colors hover:bg-neutral-200 focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-700"
            title="Открыть заявку в IntraService"
          >
            <span>IntraService</span>
            <IconExternalLink size={11} />
          </a>
          <button
            onClick={onToggleExpanded}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-neutral-200 text-neutral-500 outline-none transition-colors hover:bg-neutral-100 hover:text-neutral-800 focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-700 dark:hover:bg-neutral-800 dark:hover:text-neutral-200"
            title={expanded ? 'Свернуть' : 'Развернуть'}
          >
            {expanded ? (
              <svg width="12" height="12" viewBox="0 0 13 13" fill="none">
                <path d="M8.5 1.5v3h3M4.5 11.5v-3h-3M8.5 11.5v-3h3M4.5 1.5v3h-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 13 13" fill="none">
                <path d="M1.5 4.5h3v-3M11.5 4.5h-3v-3M1.5 8.5h3v3M11.5 8.5h-3v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </button>
        </div>
      </div>

      <h2 className="text-[15px] font-bold text-neutral-900 dark:text-neutral-100 leading-snug">
        {ticket.title}
      </h2>
      <div className="text-[12.5px] text-neutral-500 dark:text-neutral-400 mt-1 font-medium truncate">
        {ticket.servicePath || ticket.serviceName}
      </div>
    </div>
  );
}
