import { useState } from 'react';
import type { InspectorModuleProps } from './InspectorModuleProps';
import {
  IconPrinter,
  IconRefresh,
  IconCopy,
  IconAlertTriangle,
  IconCheckCircle,
  IconWrench,
} from '../../Icons';

export default function PrinterModule({
  ticket,
  details,
  facts,
  hostList,
  onToast,
  onExecuteAction,
}: InspectorModuleProps) {
  const [restartingSpooler, setRestartingSpooler] = useState(false);

  // Extract printer facts from scenario DecisionEnvelope or details
  const printerName = facts.printer_name || facts.printer || details?.custom_fields?.['printer'] || '';
  const printerIp = facts.printer_ip || facts.target_ip || '';
  const targetHost = facts.target_pc || facts.pc_name || hostList[0] || details?.pc_name || ticket.host || '';
  const spoolerStatus = facts.spooler_status || facts.spooler || 'Running';
  const queueCount = facts.queue_count ?? facts.pending_jobs ?? 0;

  const handleRestartSpooler = async () => {
    if (!targetHost) {
      onToast({ type: 'warning', message: 'Хост ПК не определен для перезапуска Spooler' });
      return;
    }
    setRestartingSpooler(true);
    try {
      if (onExecuteAction) {
        await onExecuteAction('restart_spooler', { host: targetHost });
      } else {
        await navigator.clipboard.writeText(`Invoke-Command -ComputerName "${targetHost}" -ScriptBlock { Restart-Service Spooler -Force }`);
        onToast({ type: 'info', message: 'Команда перезапуска Spooler скопирована в буфер' });
      }
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка перезапуска Spooler: ${err?.message || err}` });
    } finally {
      setRestartingSpooler(false);
    }
  };

  return (
    <div className="space-y-3">
      {/* Printer Summary Card */}
      <div className="rounded-xl border border-neutral-200/80 bg-neutral-50/50 p-3.5 dark:border-neutral-800 dark:bg-neutral-950/40 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-purple-50 text-purple-600 dark:bg-purple-950/60 dark:text-purple-400">
              <IconPrinter size={13} />
            </span>
            <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-700 dark:text-neutral-300">
              Служба печати & Оргтехника
            </span>
          </div>

          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300">
            {ticket.serviceName || 'Печать / МФУ'}
          </span>
        </div>

        {/* Fact Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          {/* 1. Printer Model / Name */}
          <div className="rounded-lg border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
            <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 mb-1">
              Устройство / Модель
            </div>
            <div className="font-semibold text-neutral-900 dark:text-neutral-100 flex items-center justify-between">
              <span>{printerName || 'Сетевой принтер'}</span>
              {printerIp && (
                <span className="font-mono text-[10px] text-neutral-500 bg-neutral-100 dark:bg-neutral-800 px-1.5 py-0.5 rounded">
                  {printerIp}:9100
                </span>
              )}
            </div>
          </div>

          {/* 2. Target Workstation */}
          <div className="rounded-lg border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
            <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 mb-1">
              Рабочая станция (ПК)
            </div>
            <div className="font-semibold text-neutral-900 dark:text-neutral-100 flex items-center justify-between font-mono">
              <span>{targetHost || 'Не привязан'}</span>
              {targetHost && (
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard.writeText(targetHost);
                    onToast({ type: 'info', message: `Имя ПК ${targetHost} скопировано` });
                  }}
                  className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 p-0.5 cursor-pointer"
                  title="Скопировать имя ПК"
                >
                  <IconCopy size={12} />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Spooler & Queue Status Bar */}
        <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-lg bg-white dark:bg-neutral-900 border border-neutral-200/70 dark:border-neutral-800">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs">
              <span className={`h-2 w-2 rounded-full ${spoolerStatus === 'Running' ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              <span className="font-medium text-neutral-700 dark:text-neutral-300">
                Spooler: {spoolerStatus === 'Running' ? 'Работает (Running)' : spoolerStatus}
              </span>
            </div>

            {queueCount > 0 && (
              <span className="inline-flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400 font-medium">
                <IconAlertTriangle size={12} />
                Очередь: {queueCount} заданий
              </span>
            )}
          </div>

          <button
            type="button"
            onClick={handleRestartSpooler}
            disabled={restartingSpooler}
            className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-neutral-50 px-2.5 py-1 text-[11px] font-medium text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 cursor-pointer disabled:opacity-50"
          >
            <IconRefresh size={11} className={restartingSpooler ? 'animate-spin text-blue-500' : ''} />
            <span>{restartingSpooler ? 'Перезапуск...' : 'Перезапустить Spooler'}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
