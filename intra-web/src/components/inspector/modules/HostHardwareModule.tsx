import { useState } from 'react';
import type { InspectorModuleProps } from './InspectorModuleProps';
import { useResolvedEntities } from '../useResolvedEntities';
import HardwareSpecsCard from '../HardwareSpecsCard';
import InspectorCard from '../primitives/InspectorCard';
import {
  IconMonitor,
  IconCopy,
  IconRefresh,
  IconPlay,
  IconWrench,
} from '../../Icons';
import {
  launchDesktopClientWithFallback,
  type DesktopClient,
} from '../../../lib/desktop';
import DiagBadge from '../DiagBadge';

export default function HostHardwareModule({
  ticket,
  details,
  rawId,
  hostList: rawHostList,
  diagStatus,
  hostDiagnostics,
  loadingDiagnostics = false,
  onRunDiag,
  onToast,
}: InspectorModuleProps) {
  const [selectedHostIndex, setSelectedHostIndex] = useState(0);
  const [showWinRmAssistant, setShowWinRmAssistant] = useState(false);

  const entities = useResolvedEntities(ticket, details, rawHostList);
  const { host } = entities;
  const hostList = host.hostList;
  const activeHost = hostList[selectedHostIndex] || host.primaryHost;

  // Get specs for active host
  const getActiveHostSpecs = () => {
    if (!hostDiagnostics) return null;
    if (hostDiagnostics.hosts && hostDiagnostics.hosts.length > 0) {
      const match = hostDiagnostics.hosts.find(
        (h) => h.host.toUpperCase() === activeHost.toUpperCase()
      );
      if (match?.specs) return match.specs;
    }
    return hostDiagnostics.specs || null;
  };

  const activeSpecs = getActiveHostSpecs();

  const handleLaunchClient = (targetHost: string, client: DesktopClient) => {
    launchDesktopClientWithFallback(rawId, targetHost, client, onToast);
  };

  if (!activeHost) {
    return (
      <InspectorCard variant="subtle">
        <div className="flex flex-col items-center justify-center gap-2 py-6 text-neutral-500 dark:text-neutral-400 text-center">
          <IconMonitor size={24} className="text-neutral-300 dark:text-neutral-600" />
          <div className="text-xs font-medium">Рабочая станция (ПК) не указана в заявке</div>
          <p className="text-[11px] text-neutral-400 max-w-sm">
            Если пользователь упомянул имя ПК в тексте, добавьте его или запустите диагностику по IP заявителя.
          </p>
        </div>
      </InspectorCard>
    );
  }

  return (
    <div className="space-y-3">
      {/* 1. Multi-Host Switcher if multiple hosts exist */}
      {hostList.length > 1 && (
        <div className="flex items-center gap-1.5 p-1 bg-neutral-100 dark:bg-neutral-800/60 rounded-xl border border-neutral-200/70 dark:border-neutral-700/60 overflow-x-auto">
          <span className="text-[10px] uppercase font-bold text-neutral-400 px-2 shrink-0">
            Хосты ({hostList.length}):
          </span>
          {hostList.map((h, idx) => (
            <button
              key={h}
              type="button"
              onClick={() => setSelectedHostIndex(idx)}
              className={`px-2.5 py-1 rounded-lg text-xs font-mono font-medium transition-colors cursor-pointer shrink-0 ${
                selectedHostIndex === idx
                  ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-xs border border-neutral-200/90 dark:border-neutral-700'
                  : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
              }`}
            >
              {h}
            </button>
          ))}
        </div>
      )}

      {/* 2. Hardware Specs Card */}
      <HardwareSpecsCard
        specs={activeSpecs}
        loading={loadingDiagnostics}
        onRefresh={() => onRunDiag(activeHost)}
      />

      {/* 3. Network Diagnostics & Remote Access Toolbar */}
      <InspectorCard variant="subtle">
        <div className="flex items-center justify-between gap-2">
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
              {activeHost}
            </span>
            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(activeHost).then(() => {
                  onToast({ type: 'info', message: `Имя хоста ${activeHost} скопировано` });
                });
              }}
              className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 p-0.5 cursor-pointer"
              title="Скопировать имя ПК"
            >
              <IconCopy size={12} />
            </button>

            {/* Badges */}
            <div className="flex items-center gap-1 ml-1">
              <DiagBadge status={diagStatus.ping} label={diagStatus.ping === 'ok' ? 'PING:OK' : undefined} />
              {diagStatus.smb !== 'idle' && (
                <DiagBadge status={diagStatus.smb} label={diagStatus.smb === 'ok' ? 'SMB:445' : 'SMB'} />
              )}
              {diagStatus.winrm !== 'idle' && (
                <DiagBadge status={diagStatus.winrm} label={diagStatus.winrm === 'ok' ? 'WinRM' : 'WinRM:ERR'} />
              )}
            </div>
          </div>

          <button
            type="button"
            onClick={() => onRunDiag(activeHost)}
            disabled={diagStatus.ping === 'checking'}
            className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 bg-white px-2.5 py-1 text-[10.5px] font-semibold text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800 disabled:opacity-50 cursor-pointer"
          >
            <IconRefresh size={11} className={diagStatus.ping === 'checking' ? 'animate-spin text-blue-500' : ''} />
            <span>{diagStatus.ping === 'checking' ? 'Опрос...' : 'Диагностика'}</span>
          </button>
        </div>

        {/* Remote Access Clients */}
        <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-neutral-200/60 dark:border-neutral-800/60">
          <span className="text-[10px] uppercase font-bold text-neutral-400 mr-1">Подключение:</span>
          <button
            type="button"
            onClick={() => handleLaunchClient(activeHost, 'litemanager')}
            className="inline-flex items-center gap-1 rounded-lg border border-blue-200 bg-blue-50/80 px-2.5 py-1 text-[11px] font-semibold text-blue-700 hover:bg-blue-100 dark:border-blue-900/60 dark:bg-blue-950/40 dark:text-blue-300 cursor-pointer"
          >
            <IconPlay size={11} />
            <span>LiteManager</span>
          </button>
          <button
            type="button"
            onClick={() => handleLaunchClient(activeHost, 'dameware')}
            className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 bg-white px-2.5 py-1 text-[11px] font-medium text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 cursor-pointer"
          >
            <IconPlay size={11} />
            <span>DameWare</span>
          </button>
          <button
            type="button"
            onClick={() => handleLaunchClient(activeHost, 'rdp')}
            className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 bg-white px-2.5 py-1 text-[11px] font-medium text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 cursor-pointer"
          >
            <IconPlay size={11} />
            <span>RDP</span>
          </button>

          {/* WinRM Assistant toggle if WinRM is failing */}
          {diagStatus.winrm === 'fail' && (
            <button
              type="button"
              onClick={() => setShowWinRmAssistant((prev) => !prev)}
              className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-amber-600 dark:text-amber-400 hover:underline cursor-pointer"
            >
              <IconWrench size={11} />
              <span>{showWinRmAssistant ? 'Скрыть команду' : 'Включить WinRM'}</span>
            </button>
          )}
        </div>

        {/* WinRM Assistant Box */}
        {showWinRmAssistant && (
          <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-2.5 text-xs dark:border-amber-900/60 dark:bg-amber-950/30">
            <div className="text-[11px] text-amber-900 dark:text-amber-200 font-medium mb-1">
              Команда удалённого включения службы WinRM через WMI:
            </div>
            <div className="flex items-center justify-between gap-2 font-mono text-[10px] bg-white dark:bg-neutral-900 p-1.5 rounded-lg border border-neutral-200 dark:border-neutral-800">
              <span className="truncate">wmic /node:{activeHost} process call create "winrm quickconfig -q"</span>
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard.writeText(`wmic /node:${activeHost} process call create "winrm quickconfig -q"`);
                  onToast({ type: 'info', message: 'Команда WinRM скопирована' });
                }}
                className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 shrink-0 cursor-pointer"
              >
                <IconCopy size={12} />
              </button>
            </div>
          </div>
        )}
      </InspectorCard>
    </div>
  );
}
