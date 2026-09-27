export type AutopilotMode = "FULL_AUTO" | "ASSISTED" | "DISABLED";

export type AnalysisState = "not_analyzed" | "in_progress" | "ready" | "stale" | "failed";

export type RoutingState =
  | "selected"
  | "needs_clarification"
  | "ambiguous"
  | "unmatched"
  | "degraded"
  | "refused";

export type ApprovalState =
  | "not_applicable"
  | "ready_for_approval"
  | "approved"
  | "corrected"
  | "rejected"
  | "manual_takeover"
  | "blocked";

export interface AutopilotPolicy {
  scenario_key: string;
  mode: AutopilotMode;
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
}

export interface AutopilotCommand {
  id: string;
  action: string;
  status: "pending" | "running" | "succeeded" | "failed" | "needs_review";
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

export interface PreflightCheckItem {
  name: string;
  status: "passed" | "failed" | "warning" | "skipped";
  details: Record<string, any>;
  message?: string | null;
}

export interface PreflightResult {
  id?: string | null;
  status: "passed" | "failed" | "degraded" | "not_applicable";
  scenario_key: string;
  params_hash: string;
  checks: PreflightCheckItem[];
  details: Record<string, any>;
  error_message?: string | null;
  expires_at?: string | null;
  created_at?: string | null;
  is_expired: boolean;
}

export interface EvidenceSummary {
  source_type: string;
  reason_code: string;
  description: string;
  scenario_key?: string | null;
  verdict_polarity: "supporting" | "contradicting" | "neutral";
  provider: string;
  version?: string | null;
  is_degraded: boolean;
}

export interface ScenarioFieldSchema {
  field_key: string;
  label: string;
  field_type: "string" | "integer" | "boolean" | "select";
  required: boolean;
  default_value?: any;
  options?: Array<{ value: string; label: string }>;
  hint?: string | null;
}

export interface ScenarioCatalogItem {
  scenario_key: string;
  name: string;
  description: string;
  policy_mode: AutopilotMode;
  is_enabled: boolean;
  required_facts: string[];
  editable_fields: ScenarioFieldSchema[];
  supported_executor: string;
  disabled_reason?: string | null;
}

export interface ScenarioCatalogResponse {
  scenarios: ScenarioCatalogItem[];
  total: number;
}

export interface AgentPlan {
  task_id: number;
  scenario_key: string;
  scenario_name: string;
  description: string;
  analysis_state: AnalysisState;
  routing_state: RoutingState;
  approval_state: ApprovalState;
  can_approve: boolean;
  can_correct: boolean;
  can_reject: boolean;
  blocking_reason_codes: string[];
  is_stale: boolean;
  freshness_expires_at?: string | null;
  has_terminal_feedback: boolean;
  command_id?: string | null;
  command_status?: string | null;
  decision_id?: string | null;
  snapshot_hash: string;
  plan_id?: string | null;
  plan_hash: string;
  decision_reason_codes: string[];
  degraded_components: Record<string, string>;
  missing_facts: string[];
  preflight?: PreflightResult | null;
  is_executable: boolean;
  evidence_summaries: EvidenceSummary[];
  extracted_entities: Record<string, any>;
  candidate_hosts: string[];
  proposed_action: string;
  proposed_params: Record<string, any>;
  suggested_comment: string;
  target_status_id: number;
  last_event_id?: number | null;
  is_circuit_broken: boolean;
  mode: string;
  is_tense?: boolean;
  tense_reason?: string | null;
  has_attachments?: boolean;
}

export interface ApprovePlanRequest {
  decision_id: string;
  plan_id: string;
  plan_hash: string;
  snapshot_hash: string;
  expected_status_id: number;
  last_event_id: number | null;
}

export interface CorrectPlanRequest {
  decision_id: string;
  plan_id: string;
  plan_hash: string;
  snapshot_hash: string;
  expected_status_id: number;
  last_event_id: number | null;
  corrected_scenario: string;
  corrected_params: Record<string, any>;
  corrected_comment?: string | null;
  correction_tag: string;
  operator_notes?: string | null;
}

export interface RejectPlanRequest {
  decision_id: string;
  plan_id: string;
  plan_hash: string;
  snapshot_hash: string;
  reason_tag: string;
  operator_notes?: string | null;
}

export interface ManualTakeoverRequest {
  decision_id: string;
  plan_id: string;
  plan_hash: string;
  snapshot_hash: string;
  reason_tag: string;
  operator_notes?: string | null;
}

export interface ManualTakeoverResponse {
  status: "manual_takeover";
  feedback_id: string;
  ticket_id: number;
  is_duplicate: boolean;
  external_update_succeeded?: boolean | null;
  warning?: string | null;
}

export interface RoutingFeedbackItem {
  id: string;
  decision_id?: string | null;
  prepared_plan_id?: string | null;
  command_id?: string | null;
  task_id: number;
  snapshot_hash?: string | null;
  operator_username: string;
  verdict: "approved" | "corrected" | "rejected" | "manual_takeover";
  original_scenario?: string | null;
  corrected_scenario?: string | null;
  original_params: Record<string, any>;
  corrected_params: Record<string, any>;
  reason_tag?: string | null;
  operator_notes?: string | null;
  router_version?: string | null;
  prompt_version?: string | null;
  verifier_used?: boolean;
  source: string;
  created_at?: string | null;
}

export type RoutingFeedback = RoutingFeedbackItem;

export interface FeedbackListResponse {
  feedback: RoutingFeedbackItem[];
  total: number;
}

export interface RoutingQualityMetrics {
  total_decisions: number;
  by_routing_state: Record<string, number>;
  approve_rate: number;
  correction_rate: number;
  reject_takeover_rate: number;
  by_scenario: Record<string, number>;
  scenario_transitions: Array<{ original_scenario: string; corrected_scenario: string; count: number }>;
  llm_verifier_call_rate: number;
  verifier_agreement_rate?: number | null;
  degraded_provider_rate: number;
  missing_facts_rate: number;
  preflight_failure_rate: number;
  command_success_rate: number;
  command_failure_rate: number;
  p50_latency_ms?: number | null;
  p95_latency_ms?: number | null;
}

export interface BatchAssignRequest {
  ticket_ids: number[];
}

export interface BatchAssignResponse {
  assigned_count: number;
  failed_ids: number[];
  details: Record<number, string>;
}
