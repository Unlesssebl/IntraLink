import type { Ticket } from '../../../data/mock';
import type { TaskDetails, HostDiagnostics, DiagStatus } from '../../../lib/types';

export interface InspectorModuleProps {
  ticket: Ticket;
  details: TaskDetails | null;
  rawId: number;
  scenarioKey: string;
  facts: Record<string, any>;
  hostList: string[];
  diagStatus: Record<string, DiagStatus>;
  hostDiagnostics?: HostDiagnostics | null;
  loadingDiagnostics?: boolean;
  onRunDiag: (host?: string) => void;
  onToast: (toast: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  onRefreshDetails?: () => void;
  onExecuteAction?: (actionId: string, params?: Record<string, any>) => Promise<void>;
}
