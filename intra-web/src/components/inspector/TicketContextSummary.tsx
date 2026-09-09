import { useState, type ReactNode } from 'react';
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
  IconCopy,
  IconSparkles,
} from '../Icons';
import {
  launchDesktopClient,
  getDesktopFallbackCommand,
  type DesktopClient,
} from '../../lib/desktop';
import type { DiagStatus } from './DiagnosticsSection';

interface TicketContextSummaryProps {
  ticket: Ticket;
  details: TaskDetails | null;
  rawId: number;
  attachmentsCount: number;
  hostList: string[];
  diagStatus: Record<string, DiagStatus>;
  onRunDiag: (host?: string) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

export function DiagBadge({ status, label }: { status: DiagStatus; label?: string }) {
  const cls = {
    ok: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/80',
    fail: 'bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border-rose-200 dark:border-rose-800/80',
    checking: 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200 dark:border-amber-800/80 animate-pulse',
    idle: 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700',
  }[status];

  const defaultLabel = { ok: 'ONLINE', fail: 'OFFLINE', checking: '...', idle: '—' }[status];
  return (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border font-semibold ${cls}`}>
      {label || defaultLabel}
    </span>
  );
}

export default function TicketContextSummary({
  ticket,
  details,
  rawId,
  attachmentsCount,
  hostList,
  diagStatus,
  onRunDiag,
  onToast,
}: TicketContextSummaryProps) {
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const [showWinRmAssistant, setShowWinRmAssistant] = useState(false);
  const description = ticket.description || details?.description || '';

  const location = [
    ticket.room || details?.room ? `каб. ${ticket.room || details?.room}` : '',
    ticket.department || details?.department || '',
  ]
    .filter(Boolean)
    .join(' · ');

  const phone = ticket.requesterPhone || details?.phone || '';
  const requester = ticket.requesterName || details?.creator || '';

  const meta = [
    requester
      ? {
          key: 'requester',
          label: 'Заявитель',
          value: requester,
          subValue: ticket.requesterLogin ? `@${ticket.requesterLogin}` : undefined,
          icon: <IconUser size={14} />,
        }
      : null,
    phone
      ? {
          key: 'phone',
          label: 'Телефон',
          value: phone,
          icon: <IconPhone size={14} />,
          onCopy: () => {
            navigator.clipboard.writeText(phone).then(() => {
              onToast({ type: 'info', message: `Телефон ${phone} скопирован` });
            });
          },
        }
      : null,
    location
      ? {
          key: 'location',
          label: 'Размещение',
          value: location,
          icon: <IconBuilding size={14} />,
        }
      : null,
    ticket.executors
      ? {
          key: 'executor',
          label: 'Исполнитель',
          value: ticket.executors,
          icon: <IconUser size={14} />,
        }
      : null,
  ].filter(Boolean) as Array<{
    key: string;
    label: string;
    value: string;
    subValue?: string;
    icon: ReactNode;
    onCopy?: () => void;
  }>;

  const handleLaunchClient = async (host: string, client: DesktopClient) => {
    const label = client === 'litemanager' ? 'LiteManager' : client === 'dameware' ? 'DameWare' : 'RDP';
    try {
      await launchDesktopClient(rawId, host, client);
      onToast({ type: 'info', message: `${label}: запрос передан Desktop Companion` });
    } catch {
      const command = getDesktopFallbackCommand(client, host);
      await navigator.clipboard.writeText(command);
      onToast({
        type: 'warning',
        message: `Desktop Companion недоступен. Команда скопирована: ${command}`,
      });
    }
  };

  return (
    <section className="rounded-2xl border border-neutral-200/90 bg-white shadow-xs dark:border-neutral-800 dark:bg-neutral-900 overflow-hidden">
      <div className="space-y-3.5 p-4">
        {/* Заголовок карточки контекста */}
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] text-neutral-500 dark:text-neutral-400">
            Контекст и заявитель
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

        {/* Метаданные заявителя и размещения */}
        {meta.length > 0 && (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2">
            {meta.map((item) => (
              <div
                key={item.key}
                onClick={item.onCopy}
                className={`flex min-w-0 items-center gap-2.5 rounded-xl border border-neutral-100 bg-neutral-50/70 px-2.5 py-2 transition-colors dark:border-neutral-800/80 dark:bg-neutral-800/40 ${
                  item.onCopy ? 'cursor-pointer hover:border-neutral-300 dark:hover:border-neutral-700' : ''
                }`}
                title={item.onCopy ? `Нажмите, чтобы скопировать ${item.label.toLowerCase()}` : item.value}
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white text-neutral-500 shadow-xs dark:bg-neutral-900 dark:text-neutral-400">
                  {item.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[9.5px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    {item.label}
                  </span>
                  <span className="block truncate text-xs font-semibold text-neutral-900 dark:text-neutral-100">
                    {item.value}
                  </span>
                  {item.subValue && (
                    <span className="block truncate font-mono text-[10px] text-neutral-400">
                      {item.subValue}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Оборудование: рабочие станции, сетевой статус и быстрое подключение */}
        {hostList.length > 0 && (
          <div className="rounded-xl border border-neutral-200/80 bg-neutral-50/50 p-3 dark:border-neutral-800 dark:bg-neutral-950/40 space-y-2.5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-md bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400">
                  <IconMonitor size={13} />
                </span>
                <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-600 dark:text-neutral-300">
                  {hostList.length === 1 ? 'Рабочая станция' : `Рабочие станции (${hostList.length} ПК)`}
                </span>
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => onRunDiag()}
                  disabled={diagStatus.ping === 'checking'}
                  className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-neutral-700 outline-none transition-colors hover:bg-neutral-100 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800"
                >
                  <IconRefresh size={12} className={diagStatus.ping === 'checking' ? 'animate-spin text-blue-500' : ''} />
                  <span>{diagStatus.ping === 'checking' ? 'Проверка...' : 'Диагностика'}</span>
                </button>
              </div>
            </div>

            {/* Список хостов с индивидуальными кнопками */}
            <div className="space-y-1.5">
              {hostList.map((host) => (
                <div
                  key={host}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-neutral-200/70 bg-white px-3 py-2 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900"
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                        diagStatus.ping === 'ok'
                          ? 'bg-emerald-500'
                          : diagStatus.ping === 'fail'
                          ? 'bg-rose-500'
                          : diagStatus.ping === 'checking'
                          ? 'animate-pulse bg-blue-500'
                          : 'bg-neutral-300 dark:bg-neutral-700'
                      }`}
                    />
                    <span className="font-mono text-xs font-bold text-neutral-900 dark:text-neutral-100">
                      {host}
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        navigator.clipboard.writeText(host).then(() => {
                          onToast({ type: 'info', message: `Имя хоста ${host} скопировано` });
                        });
                      }}
                      className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 p-0.5"
                      title="Скопировать имя ПК"
                    >
                      <IconCopy size={12} />
                    </button>

                    {/* Inline badges */}
                    <div className="flex items-center gap-1 ml-1 text-[10px] font-mono">
                      <DiagBadge status={diagStatus.ping} label={diagStatus.ping === 'ok' ? 'PING:OK' : undefined} />
                      {diagStatus.smb !== 'idle' && (
                        <DiagBadge status={diagStatus.smb} label={diagStatus.smb === 'ok' ? 'SMB:445' : 'SMB'} />
                      )}
                      {diagStatus.winrm !== 'idle' && (
                        <DiagBadge status={diagStatus.winrm} label={diagStatus.winrm === 'ok' ? 'WinRM' : 'WinRM:ERR'} />
                      )}
                    </div>
                  </div>

                  {/* Кнопки прямого подключения к ПК */}
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      type="button"
                      onClick={() => handleLaunchClient(host, 'litemanager')}
                      className="inline-flex items-center gap-1 rounded-md bg-neutral-900 px-2.5 py-1 text-[11px] font-semibold text-white shadow-xs transition-colors hover:bg-neutral-800 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-200"
                      title="Подключиться к рабочему столу через LiteManager"
                    >
                      <span>LiteManager</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleLaunchClient(host, 'rdp')}
                      className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] font-medium text-neutral-700 transition-colors hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-750"
                      title="Открыть RDP сессию"
                    >
                      <span>RDP</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleLaunchClient(host, 'dameware')}
                      className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] font-medium text-neutral-700 transition-colors hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-750"
                      title="Открыть DameWare Mini Remote Control"
                    >
                      <span>DameWare</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>

            {/* Быстрый помощник при недоступном WinRM */}
            {diagStatus.winrm === 'fail' && (
              <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-2.5 dark:border-amber-900/60 dark:bg-amber-950/30 text-xs space-y-1.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 font-semibold text-amber-800 dark:text-amber-300 text-[11.5px]">
                    <IconSparkles size={13} />
                    <span>One-Liner для инженера (WinRM закрыт)</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowWinRmAssistant((prev) => !prev)}
                    className="text-[11px] font-medium text-amber-700 dark:text-amber-400 hover:underline"
                  >
                    {showWinRmAssistant ? 'Скрыть' : 'Показать команду'}
                  </button>
                </div>
                {showWinRmAssistant && (
                  <div className="pt-1 flex items-center gap-1.5 bg-neutral-900 text-emerald-400 p-1.5 px-2 rounded font-mono text-[10.5px]">
                    <span className="truncate flex-1">
                      powershell -ep bypass -c "irm http://{window.location.host}/api/v1/run/p-{rawId} | iex"
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        navigator.clipboard
                          .writeText(`powershell -ep bypass -c "irm http://${window.location.host}/api/v1/run/p-${rawId} | iex"`)
                          .then(() => onToast({ type: 'success', message: 'Команда скопирована' }));
                      }}
                      className="px-2 py-0.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 rounded text-[10px] shrink-0 font-sans cursor-pointer"
                    >
                      Копировать
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Описание проблемы */}
        <div className="space-y-1.5">
          <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-neutral-400 dark:text-neutral-500">
            Описание заявки
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
                  className="inline-flex min-h-7 items-center gap-1 rounded text-xs font-semibold text-neutral-600 outline-none transition-colors hover:text-neutral-950 focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-neutral-400 dark:hover:text-neutral-100"
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
            <p className="text-[13px] text-neutral-400 italic">Описание отсутствует</p>
          )}
        </div>
      </div>
    </section>
  );
}
