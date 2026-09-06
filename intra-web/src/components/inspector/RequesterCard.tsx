import React from 'react';
import type { Ticket } from '../../data/mock';
import type { TaskDetails } from '../../lib/types';
import { IconUser, IconPhone, IconBuilding, IconRefresh } from '../Icons';
import { type DiagStatus } from './DiagnosticsSection';

interface RequesterCardProps {
  ticket: Ticket;
  details: TaskDetails | null;
  effectiveHost: string;
  hostList: string[];
  rawId: number;
  diagStatus: Record<string, DiagStatus>;
  multiHostDiag: Record<string, { ping: DiagStatus; smb: DiagStatus; winrm: DiagStatus; rtt?: string | null; isOnline?: boolean }>;
  showWinRMAssistant: boolean;
  onRunDiag: (targetHost?: string) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

export default function RequesterCard({
  ticket,
  details,
  effectiveHost,
  hostList,
  rawId,
  diagStatus,
  multiHostDiag,
  showWinRMAssistant,
  onRunDiag,
  onToast,
}: RequesterCardProps) {
  return (
    <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-3.5 bg-white dark:bg-neutral-900 shadow-xs space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
          Заявитель и оборудование
        </span>
        {effectiveHost && (
          <button
            onClick={() => onRunDiag()}
            disabled={diagStatus.ping === 'checking'}
            className="text-[12px] text-neutral-700 dark:text-neutral-300 hover:text-blue-600 dark:hover:text-blue-400 font-semibold cursor-pointer inline-flex items-center gap-1.5 transition-colors"
          >
            <IconRefresh size={12} className={diagStatus.ping === 'checking' ? 'animate-spin text-blue-500' : ''} />
            <span>{diagStatus.ping === 'checking' ? 'Проверка...' : 'Диагностика сети'}</span>
          </button>
        )}
      </div>

      {/* Adaptive chips layout */}
      <div className="flex flex-wrap gap-2 text-[12.5px]">
        {/* Requester name */}
        <div className="flex-1 min-w-[190px] bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 flex items-start gap-2.5">
          <div className="w-7 h-7 rounded-md bg-neutral-200 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 flex items-center justify-center shrink-0 mt-0.5">
            <IconUser size={14} />
          </div>
          <div className="min-w-0 flex-1">
            <span className="text-neutral-400 block text-[10px] uppercase font-bold tracking-wider mb-0.5">Заявитель</span>
            <span className="text-neutral-900 dark:text-neutral-100 font-semibold block truncate" title={ticket.requesterName}>
              {ticket.requesterName || 'Не указан'}
            </span>
            {ticket.requesterLogin && (
              <span className="text-[11px] font-mono text-neutral-400 block truncate">
                @{ticket.requesterLogin}
              </span>
            )}
          </div>
        </div>

        {/* Phone */}
        {(ticket.requesterPhone || details?.phone) && (
          <div className="bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 flex items-start gap-2.5 min-w-[130px]">
            <div className="w-7 h-7 rounded-md bg-neutral-200 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 flex items-center justify-center shrink-0 mt-0.5">
              <IconPhone size={14} />
            </div>
            <div className="min-w-0 flex-1">
              <span className="text-neutral-400 block text-[10px] uppercase font-bold tracking-wider mb-0.5">Телефон</span>
              <span className="text-neutral-900 dark:text-neutral-100 font-mono font-semibold block">
                {ticket.requesterPhone || details?.phone}
              </span>
            </div>
          </div>
        )}

        {/* Location */}
        {(ticket.room || details?.room || ticket.department || details?.department) && (
          <div className="flex-1 min-w-[190px] bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 flex items-start gap-2.5">
            <div className="w-7 h-7 rounded-md bg-neutral-200 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 flex items-center justify-center shrink-0 mt-0.5">
              <IconBuilding size={14} />
            </div>
            <div className="min-w-0 flex-1">
              <span className="text-neutral-400 block text-[10px] uppercase font-bold tracking-wider mb-0.5">Размещение</span>
              <span className="text-neutral-800 dark:text-neutral-200 font-medium block truncate" title={[ticket.room || details?.room ? `каб. ${ticket.room || details?.room}` : '', ticket.department || details?.department].filter(Boolean).join(' · ')}>
                {[ticket.room || details?.room ? `каб. ${ticket.room || details?.room}` : '', ticket.department || details?.department].filter(Boolean).join(' · ')}
              </span>
            </div>
          </div>
        )}

        {/* Executor chip */}
        {ticket.executors && (
          <div className="flex-1 min-w-[190px] bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 flex items-start gap-2.5">
            <div className="w-7 h-7 rounded-md bg-neutral-200 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 flex items-center justify-center shrink-0 mt-0.5">
              <IconUser size={14} />
            </div>
            <div className="min-w-0 flex-1">
              <span className="text-neutral-400 block text-[10px] uppercase font-bold tracking-wider mb-0.5">Исполнитель</span>
              <span className="text-neutral-900 dark:text-neutral-100 font-semibold block truncate" title={ticket.executors}>
                {ticket.executors}
              </span>
            </div>
          </div>
        )}

        {/* Workstation Host Compact Badge */}
        {effectiveHost && (
          <div className="flex-1 min-w-[190px] bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <span
                className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                  diagStatus.ping === 'ok'
                    ? 'bg-emerald-500'
                    : diagStatus.ping === 'fail'
                    ? 'bg-rose-500'
                    : diagStatus.ping === 'checking'
                    ? 'bg-blue-500 animate-pulse'
                    : 'bg-neutral-400'
                }`}
              />
              <div className="min-w-0">
                <span className="text-neutral-400 block text-[10px] uppercase font-bold tracking-wider">
                  Рабочая станция
                </span>
                <span className="text-neutral-900 dark:text-neutral-100 font-mono font-semibold block truncate">
                  {effectiveHost}
                </span>
              </div>
            </div>
            <button
              type="button"
              onClick={() => onRunDiag()}
              disabled={diagStatus.ping === 'checking'}
              className="text-[11px] text-neutral-600 dark:text-neutral-300 hover:text-blue-600 dark:hover:text-blue-400 font-medium px-2 py-1 rounded bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 transition-colors cursor-pointer shrink-0 disabled:opacity-50"
            >
              {diagStatus.ping === 'checking' ? '...' : 'Пинг'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
