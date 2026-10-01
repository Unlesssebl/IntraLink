export type CaseDecisionState = "selected" | "multi_intent" | "ambiguous" | "unknown" | "degraded";
export type Disposition = "execute" | "clarify" | "consult" | "manual";

export interface CaseAssertion {
  id: string;
  kind: string;
  key: string;
  value: string;
  source_ref: string;
  text_span?: string | null;
  extraction_method: "deterministic" | "llm" | "operator";
  is_negated: boolean;
}

export interface CaseFrame {
  id: string;
  task_id: number;
  snapshot_hash: string;
  frame_version: string;
  assertions: CaseAssertion[];
  entities: Record<string, string>;
  unknown_facts: string[];
  conflicting_facts: string[];
  degraded_components: Record<string, string>;
  llm_attempted: boolean;
  created_at: string;
}

export interface CaseDecision {
  id: string;
  task_id: number;
  snapshot_hash: string;
  frame_id: string;
  router_version: string;
  prompt_version?: string | null;
  state: CaseDecisionState;
  primary_case_type?: string | null;
  secondary_case_types: string[];
  candidates: Array<{ case_type: string; case_type_version: string }>;
  evidence: Array<{ id: string; candidate_key: string; source: string; source_ref: string; text_span?: string | null }>;
  reason_codes: string[];
  degradation_reason?: string | null;
}

export interface WorkflowPlan {
  id: string;
  task_id: number;
  snapshot_hash: string;
  case_decision_id: string;
  workflow_key: string;
  workflow_version: string;
  state: string;
  disposition: Disposition;
  steps: Array<{ id: string; kind: string; key: string; params: Record<string, unknown> }>;
  missing_facts: string[];
  clarification_round: number;
  fact_provenance: Record<string, Array<Record<string, unknown>>>;
  fact_conflicts: Record<string, string[]>;
  fact_extraction_diagnostics: Record<string, unknown>;
  active_clarification_id?: string | null;
  reason_codes: string[];
}

export interface ActionPlan {
  id: string;
  task_id: number;
  snapshot_hash: string;
  workflow_plan_id: string;
  workflow_key: string;
  workflow_version: string;
  source_service_id?: number | null;
  service_binding_key?: string | null;
  service_binding_version?: string | null;
  catalog_hash?: string | null;
  state: string;
  disposition: Disposition;
  plan_hash: string;
  actions: Array<{
    id: string;
    capability_key: string;
    sequence_no: number;
    params: Record<string, unknown>;
    risk: string;
    requires_approval: boolean;
  }>;
}

export interface RedirectCandidate {
  service_id: number;
  service_path: string;
  binding_key: string;
  binding_version: string;
  redirect_strategy: "cancel_and_recreate" | "transfer_service" | "manual";
  evidence: string[];
  contradictions: string[];
  verifier_result?: "supported" | "contradicted" | "insufficient" | null;
}

export interface ServiceTargetCandidate {
  service_id: number;
  service_path: string;
  confidence: "high" | "medium" | "low" | string;
  score: number;
  score_components: Record<string, number>;
  evidence: string[];
}

export interface ServiceCompatibility {
  id: string;
  task_id: number;
  snapshot_hash: string;
  case_decision_id: string;
  source_service_id?: number | null;
  source_service_path?: string | null;
  source_task_type_id?: number | null;
  binding_key?: string | null;
  binding_version?: string | null;
  catalog_hash?: string | null;
  allowed_case_types: string[];
  target_service_id?: number | null;
  target_service_path?: string | null;
  target_selection_state: "source_match" | "target_suggested" | "ambiguous" | "not_found" | "unavailable";
  target_selection_method?: string | null;
  target_candidates: ServiceTargetCandidate[];
  routing_evidence: string[];
  routing_contradictions: string[];
  catalog_state: "available" | "stale" | "unavailable" | string;
  authorization_state: string;
  analysis_timings_ms: Record<string, number>;
  llm_used: boolean;
  target_reranker_verdict?: string | null;
  target_reranker_reason?: string | null;
  target_reranker_confidence?: number | null;
  candidates: RedirectCandidate[];
  evidence: string[];
  contradictions: string[];
  reason_codes: string[];
  degraded_component?: string | null;
  state: "compatible" | "mismatch" | "ambiguous" | "unknown" | "degraded";
}

export interface RedirectPlan {
  id: string;
  task_id: number;
  snapshot_hash: string;
  source_service_id: number;
  target_service_id: number;
  target_service_path: string;
  strategy: "cancel_and_recreate" | "transfer_service" | "manual";
  rendered_public_comment: string;
  plan_hash: string;
  state: string;
  approval_state: string;
  execution_state: string;
  execution_steps: Array<Record<string, unknown>>;
  version: number;
}

export interface ExecutionPolicy {
  allowed: boolean;
  phase: "approval" | "execution";
  capability_key: string;
  policy_revision: number;
  engine_version: string;
  context_hash: string;
  decision_hash: string;
  reasons: string[];
}

export interface TicketAutomation {
  task_id: number;
  snapshot_hash: string;
  case_frame: CaseFrame;
  case_decision: CaseDecision;
  service_compatibility: ServiceCompatibility;
  workflow_plan: WorkflowPlan;
  redirect_plan?: RedirectPlan | null;
  action_plan?: ActionPlan | null;
  preflight: Array<{
    action_id: string;
    capability_key: string;
    status: string;
    checks: string[];
    details: Record<string, unknown>;
    error?: string | null;
    expires_at: string;
  }>;
  approval: {
    state?: string;
    operator?: string | null;
    kind?: "operator" | "policy_auto" | null;
  };
  execution_policy?: ExecutionPolicy | null;

  execution: Array<{
    command_id: string;
    action_id?: string | null;
    capability_key?: string | null;
    status: string;
    outcome?: string | null;
  }>;
  readiness?: {
    state: string;
    current_stage: string;
    stages: Array<{ key: string; state: string; reason_code?: string | null }>;
    blockers: Array<{
      code: string;
      stage: string;
      fact_keys: string[];
      retryable: boolean;
      detail?: string | null;
    }>;
    actions: string[];
    clarification?: {
      id: string;
      state: string;
      round: number;
      missing_facts: string[];
      publish_attempts: number;
      last_error_code?: string | null;
      last_error_detail?: string | null;
    } | null;
    preflight: {
      state: string;
      reason_code?: string | null;
      checks: Array<Record<string, unknown>>;
    };
  } | null;
  human_actions?: HumanActionRequest[];
  context_header?: WorkflowContextHeader | null;
  execution_summary?: string[];
  suggested_corrections?: SuggestedCorrection[];
  autonomy_run?: {
    id: string;
    state: string;
    assignment_epoch: number;
    attempt_count: number;
    blocker_code?: string | null;
    awaiting_response: boolean;
    awaiting_operator_action: boolean;
    next_attempt_at?: string | null;
    private_note_state?: string | null;
  } | null;
}

export interface SuggestedCorrection {
  param_key: string;
  param_label: string;
  current_value: string;
  suggested_value: string;
  reason: string;
  text_span: string;
  comment_id?: number | null;
}

export interface WorkflowContextHeader {
  title: string;
  workflow_key: string;
  category_badge: string;
  icon: string;
  subject_label: string;
  subject_value: string;
  subject_caption?: string | null;
}

export interface HumanActionCandidate {
  candidate_id: string;
  label: string;
  score: number;
  evidence: string[];
}

export interface HumanActionRequest {
  id: string;
  task_id: number;
  action_id: string;
  target_param: string;
  type: string;
  prompt: string;
  candidates: HumanActionCandidate[];
  state: string;
  selected_candidate_id?: string | null;
  expires_at: string;
}

export interface AutomationCommand {
  id: string;
  task_id?: number | null;
  action_plan_id?: string | null;
  action_id?: string | null;
  capability_key?: string | null;
  sequence_no?: number | null;
  status: string;
  initiator: string;
  error_message?: string | null;
  result_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface CatalogResponse<T = Record<string, unknown>> {
  items: T[];
  total: number;
}

export interface CorrectedActionInput {
  capability_key: string;
  params: Record<string, unknown>;
}
