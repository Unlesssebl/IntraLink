export type AutopilotMode = "FULL_AUTO" | "ASSISTED" | "DISABLED";

export interface AutopilotPolicy {
  scenario_key: string;
  mode: AutopilotMode;
  min_confidence: number;
  consecutive_failures: number;
  last_failure_at: string | null;
  is_circuit_broken: boolean;
  description: string | null;
}

export interface AutopilotPoliciesListResponse {
  policies: AutopilotPolicy[];
  total: number;
}

export interface UpdateAutopilotPolicyRequest {
  mode: AutopilotMode;
  min_confidence?: number;
}

export interface AutopilotCommand {
  id: string;
  action: string;
  status: "pending" | "running" | "succeeded" | "failed";
  task_id: number | null;
  initiator: string;
  target_json?: Record<string, any> | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AutopilotStats {
  total_automated_actions: number;
  hours_saved: number;
  active_scenarios_count: number;
  tripped_circuit_breakers: number;
}
