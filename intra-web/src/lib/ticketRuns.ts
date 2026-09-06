import { apiFetch } from './api';

export type TicketRunMode = 'manual' | 'autopilot';
export type TicketRunState =
  | 'pending'
  | 'running'
  | 'waiting_answer'
  | 'waiting_approval'
  | 'paused'
  | 'system_error'
  | 'completed';

export interface TicketRun {
  id: string;
  task_id: number;
  mode: TicketRunMode;
  state: TicketRunState;
  outcome: 'completed' | 'cancelled' | 'externally_stopped' | null;
  current_step: string | null;
  waiting_reason: string | null;
  waiting_until: string | null;
  clarification_count: number;
  pause_reason: string | null;
  error_code: string | null;
  error_message: string | null;
  version: number;
  trigger_kind: string;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
}

export interface TicketRunEvent {
  id: string;
  sequence: number;
  event_type: string;
  actor: string;
  details: Record<string, unknown>;
  created_at: string | null;
}

export interface TicketRunView {
  run: TicketRun | null;
  events: TicketRunEvent[];
  pending_command: TicketRunCommand | null;
}

export interface TicketRunCommand {
  command_id: string;
  action: string;
  status: string;
  parameters: Record<string, unknown>;
  initiator: string;
  error_message: string | null;
}

export interface AutopilotSetting {
  enabled: boolean;
  version: number;
  updated_by: string;
  updated_at: string | null;
  templates_ready: boolean;
  missing_templates: string[];
}

export const fetchTicketRun = (taskId: number) =>
  apiFetch<TicketRunView>(`/api/v2/ticket-runs/by-task/${taskId}`);

export const fetchTicketRuns = (taskIds: number[]) => {
  const query = new URLSearchParams();
  taskIds.forEach(taskId => query.append('task_ids', String(taskId)));
  return apiFetch<{ items: TicketRun[] }>(`/api/v2/ticket-runs?${query.toString()}`);
};

export const startTicketRun = (taskId: number, mode: TicketRunMode) =>
  apiFetch<TicketRun>(`/api/v2/ticket-runs/by-task/${taskId}`, {
    method: 'POST',
    body: JSON.stringify({ mode }),
  });

export const controlTicketRun = (
  runId: string,
  action: 'pause' | 'resume' | 'switch_mode',
  expectedVersion: number,
  mode?: TicketRunMode,
) =>
  apiFetch<TicketRun>(`/api/v2/ticket-runs/${runId}/control`, {
    method: 'POST',
    body: JSON.stringify({ action, expected_version: expectedVersion, mode }),
  });

export async function ensureManualTicketRun(taskId: number): Promise<TicketRun> {
  const current = (await fetchTicketRun(taskId)).run;
  if (!current || current.completed_at) {
    return startTicketRun(taskId, 'manual');
  }
  if (current.mode === 'autopilot') {
    return controlTicketRun(current.id, 'switch_mode', current.version, 'manual');
  }
  if (current.state === 'paused' || current.state === 'system_error') {
    return controlTicketRun(current.id, 'resume', current.version);
  }
  return current;
}

export const fetchAutopilotSetting = () =>
  apiFetch<AutopilotSetting>('/api/v2/autopilot');

export const updateAutopilotSetting = (
  enabled: boolean,
  expectedVersion: number,
  reason: string,
) =>
  apiFetch<AutopilotSetting>('/api/v2/autopilot', {
    method: 'PUT',
    body: JSON.stringify({ enabled, expected_version: expectedVersion, reason }),
  });
