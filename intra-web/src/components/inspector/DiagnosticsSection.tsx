import React from 'react';
import { IconMonitor, IconCopy, IconSparkles } from '../Icons';
import { getDesktopFallbackCommand, launchDesktopClient, type DesktopClient } from '../../lib/desktop';

export type DiagStatus = 'ok' | 'fail' | 'checking' | 'idle';

export function DiagBadge({ status }: { status: DiagStatus }) {
  const cls = {
    ok: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/80',
    fail: 'bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border-rose-200 dark:border-rose-800/80',
    checking: 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200 dark:border-amber-800/80 animate-pulse',
    idle: 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700',
  }[status];
  const label = { ok: 'ОК', fail: 'Недоступен', checking: 'Проверка...', idle: '—' }[status];
  return <span className={`text-[11px] font-mono px-1.5 py-0.5 rounded border font-semibold ${cls}`}>{label}</span>;
}

interface DiagnosticsSectionProps {
  hostList: string[];
  rawId: number;
  diagStatus: Record<string, DiagStatus>;
  multiHostDiag: Record<string, { ping: DiagStatus; smb: DiagStatus; winrm: DiagStatus; rtt?: string | null; isOnline?: boolean }>;
  showWinRMAssistant: boolean;
  onRunDiag: () => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

export default function DiagnosticsSection({
  hostList,
  rawId,
  diagStatus,
  multiHostDiag,
  showWinRMAssistant,
  onRunDiag,
  onToast,
}: DiagnosticsSectionProps) {
  if (hostList.length === 0) return null;

  return (
    <div className="w-full bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 space-y-2">
      <div className="flex items-center justify-between gap-2 flex-wrap pb-1 border-b border-neutral-200/60 dark:border-neutral-700/60">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-amber-100 dark:bg-amber-950/80 text-amber-700 dark:text-amber-300 flex items-center justify-center shrink-0">
            <IconMonitor size={13} />
          </div>
          <div>
            <span className="text-neutral-400 block text-[9.5px] uppercase font-bold tracking-wider">
              {hostList.length > 1 ? `Рабочие станции (${hostList.length} ПК)` : 'Рабочая станция / ПК'}
            </span>
          </div>
        </div>

        {hostList.length > 1 && (
          <button
            type="button"
            onClick={onRunDiag}
            className="px-2 py-0.5 text-[11px] font-medium rounded-md bg-neutral-200/80 dark:bg-neutral-700/80 hover:bg-neutral-900 hover:text-white dark:hover:bg-neutral-100 dark:hover:text-neutral-900 text-neutral-700 dark:text-neutral-200 transition-colors cursor-pointer"
          >
            Проверить все
          </button>
        )}
      </div>

      {/* List of host chips with individual status */}
      <div className="space-y-1.5">
        {hostList.map((h) => {
          const hostDiag = multiHostDiag[h] || (hostList.length === 1 ? { ping: diagStatus.ping, smb: diagStatus.smb, winrm: diagStatus.winrm } : { ping: 'idle', smb: 'idle', winrm: 'idle' });
          return (
            <div key={h} className="flex items-center justify-between gap-2 flex-wrap bg-white/70 dark:bg-neutral-900/60 px-2.5 py-1.5 rounded-md border border-neutral-200/50 dark:border-neutral-800">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="font-mono font-bold text-[12.5px] text-neutral-900 dark:text-neutral-100">
                  {h}
                </span>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(h).then(() =>
                      onToast({ type: 'info', message: `Хост ${h} скопирован в буфер` })
                    );
                  }}
                  className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 cursor-pointer p-0.5"
                  title="Скопировать имя ПК"
                >
                  <IconCopy size={12} />
                </button>

                {(['litemanager', 'dameware', 'rdp'] as DesktopClient[]).map((client) => {
                  const label = client === 'litemanager' ? 'LiteManager' : client === 'dameware' ? 'DameWare' : 'RDP';
                  return <button
                    key={client}
                    type="button"
                    onClick={async () => {
                      try {
                        await launchDesktopClient(rawId, h, client);
                        onToast({ type: 'info', message: `${label}: запрос передан Desktop Companion` });
                      } catch {
                        const command = getDesktopFallbackCommand(client, h);
                        await navigator.clipboard.writeText(command);
                        onToast({ type: 'warning', message: `Desktop Companion недоступен. Команда скопирована: ${command}` });
                      }
                    }}
                    className={`px-1.5 py-0.5 text-[10.5px] rounded border cursor-pointer transition-colors ${client === 'litemanager' ? 'font-semibold bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900 border-neutral-900 dark:border-neutral-100' : 'font-medium bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-200 dark:hover:bg-neutral-750'}`}
                    title={`Открыть ${label} через Desktop Companion`}
                  >{label}</button>;
                })}
              </div>

              {/* Diagnostics inline badges for this specific host */}
              <div className="flex items-center gap-2 text-[11px] font-mono">
                <div className="flex items-center gap-1">
                  <span className="text-neutral-400 font-sans text-[10px]">Ping:</span>
                  <DiagBadge status={hostDiag.ping} />
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-neutral-400 font-sans text-[10px]">SMB:</span>
                  <DiagBadge status={hostDiag.smb} />
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-neutral-400 font-sans text-[10px]">WinRM:</span>
                  <DiagBadge status={hostDiag.winrm} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Operator Assist Card if automatic WinRM is blocked */}
      {showWinRMAssistant && (
        <div className="mt-2 p-2.5 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900/60 rounded-lg space-y-1.5 text-xs">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-amber-800 dark:text-amber-400 font-semibold text-[11.5px]">
              <IconSparkles size={13} />
              <span>Ассистент оператора (One-Liner при закрытом WinRM)</span>
            </div>
            <span className="text-[9.5px] text-amber-700 dark:text-amber-300 bg-amber-100 dark:bg-amber-900/50 px-1.5 py-0.2 rounded font-medium">
              Только для инженера
            </span>
          </div>
          <p className="text-neutral-600 dark:text-neutral-300 text-[11px] leading-relaxed">
            Если автоматическая установка по WinRM недоступна: подключитесь к ПК через <b>LiteManager</b> (или DameWare), нажмите <kbd className="px-1 py-0.2 bg-neutral-200 dark:bg-neutral-800 rounded font-mono text-[10px]">Win + R</kbd> и вставьте команду ниже:
          </p>
          <div className="flex items-center gap-1.5 bg-neutral-900 text-emerald-400 p-1.5 px-2 rounded font-mono text-[10.5px] border border-neutral-800">
            <span className="truncate flex-1">powershell -ep bypass -c "irm http://{window.location.host}/api/v1/run/p-{rawId} | iex"</span>
            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(`powershell -ep bypass -c "irm http://${window.location.host}/api/v1/run/p-${rawId} | iex"`).then(() =>
                  onToast({ type: 'success', message: 'Команда экспресс-установки скопирована в буфер обмена' })
                );
              }}
              className="px-2 py-0.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 rounded text-[10px] shrink-0 font-sans cursor-pointer transition-colors font-medium"
            >
              Копировать
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
