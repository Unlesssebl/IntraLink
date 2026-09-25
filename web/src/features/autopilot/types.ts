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

export interface PreconditionResult {
  is_valid: boolean;
  missing_facts: string[];
  environment_barriers: string[];
  clarification_prompt?: string | null;
}

export interface AgentPlan {
  task_id: number;
  scenario_key: string;
  scenario_name: string;
  description: string;
  confidence: number;
  matched: boolean;
  factor_breakdown: Record<string, number>;
  preconditions: PreconditionResult;
  host_diagnostic?: {
    hostname: string;
    is_online: boolean;
    avg_rtt?: string | null;
    ports: Record<string, boolean>;
  } | null;
  extracted_entities: Record<string, any>;
  candidate_hosts: string[];
  proposed_action: string;
  proposed_params: Record<string, any>;
  suggested_comment: string;
  target_status_id: number;
  dialogue_state?: {
    rounds: number;
    is_waiting_for_applicant: boolean;
    total_events: number;
  } | null;
  command_id?: string | null;
  last_event_id?: number | null;
  is_circuit_broken: boolean;
  mode: string;
  is_tense?: boolean;
  tense_reason?: string | null;
  has_attachments?: boolean;
}

export interface ApprovePlanRequest {
  expected_status_id?: number;
  last_event_id?: number;
  override_comment?: string;
}

export interface CorrectPlanRequest {
  expected_status_id?: number;
  last_event_id?: number;
  corrected_scenario: string;
  corrected_params: Record<string, any>;
  corrected_comment?: string;
  correction_tag: string;
  operator_notes?: string;
}

export interface AutopilotCorrection {
  id: string;
  task_id: number;
  original_scenario: string;
  corrected_scenario: string;
  original_params: Record<string, any>;
  corrected_params: Record<string, any>;
  original_comment?: string | null;
  corrected_comment?: string | null;
  confidence: number;
  factors_snapshot: Record<string, any>;
  correction_tag: string;
  operator_notes?: string | null;
  operator_username: string;
  created_at: string;
}

export interface CorrectionsListResponse {
  corrections: AutopilotCorrection[];
  total: number;
}

export interface CommandExecutionStatus {
  command_id: string;
  idempotency_key: string;
  action: string;
  status: "pending" | "running" | "succeeded" | "failed";
  initiator: string;
  task_id?: number | null;
  target_json?: Record<string, any>;
  params_json?: Record<string, any>;
  result_json?: Record<string, any> | null;
  error_message?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface BatchAssignRequest {
  ticket_ids: number[];
}

export interface BatchAssignResponse {
  assigned_count: number;
  failed_ids: number[];
  details: Record<string, string>;
}

