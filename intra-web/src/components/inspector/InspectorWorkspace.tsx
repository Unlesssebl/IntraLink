import { useState, useEffect, useMemo } from 'react';
import type { Ticket } from '../../data/mock';
import type { TaskDetails, HostDiagnostics } from '../../lib/types';
import type { DiagStatus } from './DiagnosticsSection';
import HostHardwareModule from './modules/HostHardwareModule';
import PrinterModule from './modules/PrinterModule';
import IdentityModule from './modules/IdentityModule';
import RedirectModule from './modules/RedirectModule';
import KnowledgeBaseModule from './modules/KnowledgeBaseModule';
import {
  IconMonitor,
  IconPrinter,
  IconUser,
  IconRedirect,
  IconBookOpen,
} from '../Icons';

export type WorkspaceModuleTab = 'host' | 'printer' | 'identity' | 'redirect' | 'kb';

interface InspectorWorkspaceProps {
  ticket: Ticket;
  details: TaskDetails | null;
  rawId: number;
  hostList: string[];
  diagStatus: Record<string, DiagStatus>;
  hostDiagnostics?: HostDiagnostics | null;
  loadingDiagnostics?: boolean;
  onRunDiag: (host?: string) => void;
  onToast: (toast: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  onRefreshDetails?: () => void;
  onExecuteAction?: (actionId: string, params?: Record<string, any>) => Promise<void>;
}

export default function InspectorWorkspace({
  ticket,
  details,
  rawId,
  hostList,
  diagStatus,
  hostDiagnostics,
  loadingDiagnostics,
  onRunDiag,
  onToast,
  onRefreshDetails,
  onExecuteAction,
}: InspectorWorkspaceProps) {
  const envelope = details?.decision_envelope;
  const scenarioKey = envelope?.rule?.scenario_key || envelope?.rule?.rule_type || '';
  const facts = envelope?.facts || {};

  // Auto-detect default module from scenario_key & catalog
  const defaultTab = useMemo<WorkspaceModuleTab>(() => {
    const sKey = scenarioKey.toLowerCase();
    const sName = (ticket.serviceName || '').toLowerCase();
    const desc = ((ticket.description || '') + ' ' + (ticket.title || '')).toLowerCase();

    // 1. Printer / Spooler scenario
    if (sKey.includes('printer') || sName.includes('печат') || sName.includes('оргтехник') || facts.printer_name) {
      return 'printer';
    }

    // 2. Identity / AD / Access scenario
    if (sKey.includes('create_user') || sKey.includes('wlan') || sKey.includes('identity') || sName.includes('учетн') || sName.includes('доступ')) {
      return 'identity';
    }

    // 3. Catalog redirect
    if (sKey.includes('redirect') || sName.includes('перенаправлен')) {
      return 'redirect';
    }

    // 4. Host Hardware / Health scenario (offline_host, hardware complaints, or valid host present)
    if (
      sKey.includes('offline') ||
      sKey.includes('hardware') ||
      desc.includes('тормоз') ||
      desc.includes('завис') ||
      desc.includes('перезагруз') ||
      hostList.length > 0
    ) {
      return 'host';
    }

    // 5. Fallback
    return hostList.length > 0 ? 'host' : 'kb';
  }, [scenarioKey, ticket.serviceName, ticket.description, ticket.title, facts, hostList]);

  const [activeTab, setActiveTab] = useState<WorkspaceModuleTab>(defaultTab);

  // Reset active tab to default profile whenever rawId changes
  useEffect(() => {
    setActiveTab(defaultTab);
  }, [rawId, defaultTab]);

  // Data presence indicators for tab dots
  const hasHostData = hostList.length > 0 || Boolean(ticket.host || details?.pc_name);
  const hasPrinterData = (ticket.serviceName || '').toLowerCase().includes('печат') || Boolean(facts.printer_name);
  const hasIdentityData = Boolean(ticket.requesterLogin || ticket.requesterName || details?.creator);
  const hasRedirectData = Boolean(envelope?.rule?.rule_type === 'redirect' || facts.target_service_name);
  const hasKbData = Boolean(details?.rag_results && details.rag_results.length > 0);

  const tabs: Array<{ id: WorkspaceModuleTab; label: string; icon: any; hasData: boolean }> = [
    { id: 'host', label: 'Хост / Железо', icon: IconMonitor, hasData: hasHostData },
    { id: 'printer', label: 'Печать / МФУ', icon: IconPrinter, hasData: hasPrinterData },
    { id: 'identity', label: 'Учётка / AD', icon: IconUser, hasData: hasIdentityData },
    { id: 'redirect', label: 'Редирект', icon: IconRedirect, hasData: hasRedirectData },
    { id: 'kb', label: 'База знаний', icon: IconBookOpen, hasData: hasKbData },
  ];

  const commonProps = {
    ticket,
    details,
    rawId,
    scenarioKey,
    facts,
    hostList,
    diagStatus,
    hostDiagnostics,
    loadingDiagnostics,
    onRunDiag,
    onToast,
    onRefreshDetails,
    onExecuteAction,
  };

  return (
    <div className="space-y-2.5">
      {/* Tab bar header */}
      <div className="flex items-center gap-1 p-1 bg-neutral-100/80 dark:bg-neutral-800/60 rounded-xl border border-neutral-200/80 dark:border-neutral-700/80 overflow-x-auto text-xs">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-all cursor-pointer shrink-0 ${
                isActive
                  ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs font-semibold'
                  : 'text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100'
              }`}
            >
              <Icon size={13} className={isActive ? 'text-blue-600 dark:text-blue-400' : ''} />
              <span>{tab.label}</span>
              {tab.hasData && (
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    isActive ? 'bg-blue-600 dark:bg-blue-400' : 'bg-neutral-300 dark:bg-neutral-600'
                  }`}
                  title="Есть данные по текущей заявке"
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Module Workspace Content */}
      <div className="transition-opacity duration-150">
        {activeTab === 'host' && <HostHardwareModule {...commonProps} />}
        {activeTab === 'printer' && <PrinterModule {...commonProps} />}
        {activeTab === 'identity' && <IdentityModule {...commonProps} />}
        {activeTab === 'redirect' && <RedirectModule {...commonProps} />}
        {activeTab === 'kb' && <KnowledgeBaseModule {...commonProps} />}
      </div>
    </div>
  );
}
