import { useState } from 'react';
import type { InspectorModuleProps } from './InspectorModuleProps';
import { useResolvedEntities } from '../useResolvedEntities';
import InspectorCard from '../primitives/InspectorCard';
import InspectorSectionHeader from '../primitives/InspectorSectionHeader';
import {
  IconPrinter,
  IconRefresh,
  IconCopy,
  IconAlertTriangle,
} from '../../Icons';

export default function PrinterModule({
  ticket,
  details,
  hostList,
  onToast,
  onExecuteAction,
}: InspectorModuleProps) {
  const [restartingSpooler, setRestartingSpooler] = useState(false);
  const entities = useResolvedEntities(ticket, details, hostList);
  const { printer } = entities;

  const handleRestartSpooler = async () => {
    if (!printer.targetHost) {
      onToast({ type: 'warning', message: 'Хост ПК не определен для перезапуска Spooler' });
      return;
    }
    setRestartingSpooler(true);
    try {
      if (onExecuteAction) {
        await onExecuteAction('restart_spooler', { host: printer.targetHost });
      } else {
        await navigator.clipboard.writeText(
          `Invoke-Command -ComputerName "${printer.targetHost}" -ScriptBlock { Restart-Service Spooler -Force }`
        );
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
      <InspectorCard variant="subtle">
        <InspectorSectionHeader
          title="Служба печати & Оргтехника"
          icon={<IconPrinter size={13} />}
          iconBgClass="bg-purple-50 text-purple-600 dark:bg-purple-950/60 dark:text-purple-400"
          badge={
            <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300">
              {ticket.serviceName || 'Печать / МФУ'}
            </span>
          }
        />

        {/* Fact Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          {/* 1. Printer Model / Name */}
          <div className="rounded-xl border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
            <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 mb-1">
              Устройство / Модель
            </div>
            <div className="font-semibold text-neutral-900 dark:text-neutral-100 flex items-center justify-between">
              <span>{printer.name || 'Сетевой принтер'}</span>
              {printer.ip && (
                <span className="font-mono text-[10px] text-neutral-500 bg-neutral-100 dark:bg-neutral-800 px-1.5 py-0.5 rounded">
                  {printer.ip}:9100
                </span>
              )}
            </div>
          </div>

          {/* 2. Target Workstation */}
          <div className="rounded-xl border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
            <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 mb-1">
              Рабочая станция (ПК)
            </div>
            <div className="font-semibold text-neutral-900 dark:text-neutral-100 flex items-center justify-between font-mono">
              <span>{printer.targetHost || 'Не привязан'}</span>
              {printer.targetHost && (
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard.writeText(printer.targetHost);
                    onToast({ type: 'info', message: `Имя ПК ${printer.targetHost} скопировано` });
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
        <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-xl bg-white dark:bg-neutral-900 border border-neutral-200/70 dark:border-neutral-800">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs">
              <span
                className={`h-2 w-2 rounded-full ${
                  printer.spoolerStatus === 'Running' ? 'bg-emerald-500' : 'bg-rose-500'
                }`}
              />
              <span className="font-medium text-neutral-700 dark:text-neutral-300">
                Spooler: {printer.spoolerStatus === 'Running' ? 'Работает (Running)' : printer.spoolerStatus}
              </span>
            </div>

            {printer.queueCount > 0 && (
              <span className="inline-flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400 font-medium">
                <IconAlertTriangle size={12} />
                Очередь: {printer.queueCount} заданий
              </span>
            )}
          </div>

          <button
            type="button"
            onClick={handleRestartSpooler}
            disabled={restartingSpooler}
            className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 bg-neutral-50 px-2.5 py-1 text-[11px] font-medium text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 cursor-pointer disabled:opacity-50"
          >
            <IconRefresh
              size={11}
              className={restartingSpooler ? 'animate-spin text-blue-500' : ''}
            />
            <span>{restartingSpooler ? 'Перезапуск...' : 'Перезапустить Spooler'}</span>
          </button>
        </div>
      </InspectorCard>
    </div>
  );
}
