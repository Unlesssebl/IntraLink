export interface SingleHostDiagnostics {
  host: string;
  resolved_ip?: string | null;
  is_online: boolean | null;
  avg_rtt: string | null;
  smb_ok?: boolean;
  winrm_ok?: boolean;
  rpc_ok?: boolean;
  status_label?: string;
}

export interface HostDiagnostics {
  host?: string;
  resolved_ip?: string | null;
  is_online: boolean | null;
  avg_rtt: string | null;
  smb_ok?: boolean;
  winrm_ok?: boolean;
  rpc_ok?: boolean;
  loading?: boolean;
  status_label?: string;
  hosts?: SingleHostDiagnostics[];
}

export interface TaskAttachment {
  id: number;
  name: string;
  size?: number;
  content_type?: string;
  url?: string;
}

export interface TaskComment {
  id: number;
  author: string;
  created: string;
  text: string;
  is_private: boolean;
}

export interface TicketAIPlan {
  actionType: 'grant_wlan' | 'clear_1c_cache' | 'install_printer' | 'redirect' | 'duplicate' | 'hardware_repair' | 'offline_host' | 'standard';
  actionBadge: string;
  actionTitle: string;
  targetStatusId: number;
  targetStatusName: string;
  comment: string;
  expensesMinutes: number;
  requiresDomainJob: boolean;
  domainJob?: {
    action: string;
    targetHost?: string;
    identity?: string;
    params?: Record<string, any>;
  };
  ocrErrorText?: string;
  confidenceScore: number;
  badgeClass: string;
}

export interface TaskClassification {
  rule_type: string;
  template_key: string;
  category_label: string;
  target_service_name: string;
  is_redirect: boolean;
  sources?: { rule: boolean; rag: boolean; ai: boolean };
  score: number;
  target_status_id: number;
  target_status_name: string;
  suggested_comment: string;
  expenses: number;
  badge_color: 'success' | 'warning' | 'primary' | 'info' | 'secondary';
}

export interface TaskItem {
  id: number;
  name: string;
  description: string;
  ai_summary?: string;
  creator: string;
  creator_login: string;
  created: string;
  service_id?: number;
  service_name: string;
  root_service_id?: number;
  root_service_name: string;
  service_path?: string;
  target_service_name: string;
  is_redirect: boolean;
  sources?: { rule: boolean; rag: boolean; ai: boolean };
  readiness?: { ready: boolean; blocked_reasons?: string[] };
  status_id: number;
  status_name: string;
  pc_name: string;
  printer_address?: string;
  phone: string;
  room: string;
  department: string;
  rule_type: string;
  template_key: string;
  category_label: string;
  score: number;
  target_status_id: number;
  target_status_name: string;
  suggested_comment: string;
  original_comment?: string;
  expenses: number;
  is_private?: boolean;
  badge_color: 'success' | 'warning' | 'primary' | 'info' | 'secondary';
  has_attachments: boolean;
  attachments?: TaskAttachment[];
  executors?: string;
  executor_ids?: Array<number | string>;
  analysis?: AnalysisState;
}

export interface TaskRights {
  to_statuses: number[];
  can_add_comment: boolean;
  can_add_expenses: boolean;
}

export interface TaskDetails {
  id: number;
  name: string;
  description: string;
  ai_summary?: string;
  creator: string;
  creator_login: string;
  created: string;
  service_id?: number;
  service_name: string;
  root_service_id?: number;
  root_service_name: string;
  service_path?: string;
  status_id: number;
  status_name: string;
  pc_name: string;
  printer_address?: string;
  phone: string;
  room: string;
  department: string;
  comments: TaskComment[];
  attachments: TaskAttachment[];
  cls_info: TaskClassification;
  rights?: TaskRights;
  task?: any;
  history?: any[];
  ai_suggested_resolution?: string;
  suggested_action?: any;
  decision_envelope?: DecisionEnvelope | null;
  kb_matches?: RAGMatchItem[];
  telemetry?: any;
  circuit?: 'red' | 'yellow' | 'green';
  circuit_reason?: string;
  requires_sanitization?: boolean;
  ai_suggestion?: AISuggestionState;
  decision?: DecisionRecord;
  sources?: DecisionSources;
  readiness?: DecisionReadiness;
  analysis?: AnalysisState;
}

export interface AnalysisState {
  has_result: boolean;
  state: 'not_analyzed' | 'analyzing' | 'ready' | 'failed';
  freshness: 'current' | 'stale' | 'unknown';
  disposition: 'available' | 'applied';
  decision_id?: string | null;
  decision_version?: number | null;
  analysis_revision?: string | null;
  scenario_key?: string | null;
  analyzed_at?: string | null;
  stale_reason?: string | null;
  can_quick_apply: boolean;
  blocked_reason?: string | null;
  last_attempt?: {
    state: 'succeeded' | 'failed';
    error_code?: string | null;
    finished_at?: string | null;
  } | null;
}

export interface AnalysisCounts {
  analyzed: number;
  not_analyzed: number;
  scope_total: number;
  is_complete_scope: boolean;
}

export interface DecisionFactSummary {
  state: 'missing' | 'valid' | 'invalid' | 'ambiguous' | 'conflicting' | 'stale';
  value?: unknown;
  source?: string | null;
  source_ref?: string | null;
}

export interface DecisionEnvelope {
  schema_version: number;
  decision_id?: string | null;
  decision_version: number;
  scenario_key: string;
  scenario_version: number;
  facts_revision: number;
  facts_summary: Record<string, DecisionFactSummary>;
  candidates: Array<{
    candidate_id: string;
    source: string;
    score: number;
    evidence_refs: string[];
    outcome: Record<string, any>;
  }>;
  outcome: Record<string, any>;
  policy: Record<string, any>;
  response_draft: string;
  evidence_refs: string[];
  confidence: number;
  requires_approval: boolean;
  status: string;
}

export interface FactOverridePayload {
  expected_decision_version?: number | null;
  facts: Record<string, any>;
  current_draft_text?: string | null;
}

export interface FactOverrideResponse {
  success: boolean;
  task_id: number;
  decision_envelope: DecisionEnvelope;
  run?: Record<string, any>;
  facts_summary: Record<string, DecisionFactSummary>;
  outcome: Record<string, any>;
  confidence: number;
}

export interface DecisionSources {
  rule: boolean;
  rag: boolean;
  ai: boolean;
}

export interface DecisionReadiness {
  ready: boolean;
  missing_data: string[];
  blocked_reasons: string[];
  stale: boolean;
}

export interface DecisionStep {
  id: string;
  sequence: number;
  component: 'rule' | 'rag' | 'ai' | 'policy' | string;
  status: string;
  input: Record<string, any>;
  output: Record<string, any>;
  metadata: Record<string, any>;
  error_code?: string | null;
  duration_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
}

export interface DecisionRecord {
  id: string;
  task_id: number;
  ticket_run_id?: string | null;
  version: number;
  analysis_kind: string;
  status: string;
  outcome: string;
  fingerprint: string;
  sources: DecisionSources;
  context: Record<string, any>;
  completeness: {
    complete: boolean;
    missing_data: string[];
    blocked_reasons: string[];
    limitations?: string[];
    history_total?: number;
    history_used?: number;
    attachments_total?: number;
    attachments_read?: number;
  };
  proposal: {
    action?: string;
    title?: string;
    comment?: string;
    status_id?: number;
    status_name?: string;
    expenses?: number;
    consequences?: string;
    ready: boolean;
    trigger_markers?: string[];
    risk_level?: 'normal' | 'warning' | 'critical';
    risk_warning?: string | null;
  };
  policy: Record<string, any>;
  steps?: DecisionStep[];
  feedback?: DecisionFeedbackRecord[];
  created_by?: string;
  created_at: string | null;
}

export type DecisionVerdict =
  | 'accepted'
  | 'modified'
  | 'rejected'
  | 'correct'
  | 'partial'
  | 'incorrect'
  | 'insufficient_data';

export type DecisionReasonCode =
  | 'wrong_context'
  | 'wrong_classification'
  | 'wrong_rule'
  | 'wrong_kb'
  | 'wrong_ai_text'
  | 'wrong_policy'
  | 'execution_error'
  | 'other';

export interface DecisionFeedbackRecord {
  id: string;
  decision_id: string;
  verdict: DecisionVerdict;
  reason_code?: string | null;
  comment?: string | null;
  final_action?: Record<string, any>;
  actor: string;
  created_at: string | null;
}

export interface DecisionFeedbackPayload {
  verdict: DecisionVerdict;
  reason_code?: string | null;
  comment?: string | null;
  final_action?: Record<string, any>;
}

export interface AISuggestionState {
  task_id: number;
  state: 'current' | 'stale';
  fingerprint: string;
  source: string;
  calculated_at: string;
  stale_at?: string;
  stale_reason?: string;
  policy: {
    action: string;
    mode: 'auto' | 'confirm' | 'disabled' | 'dry_run';
    allowed: boolean;
    blocked: boolean;
    reason: string;
  };
  missing_data: string[];
}

export interface TicketSummaryResult {
  core_problem: string;
  actions_taken: string[];
  current_status: string;
  recommended_next_step: string;
}

export interface AIHealthData {
  ollama_available: boolean;
  ollama_url: string;
  ollama_model: string;
  litellm_available: boolean;
  litellm_url: string;
  gpu_detected: boolean;
  gpu_name?: string | null;
  gpu_backend?: string | null;
  vram_allocated_bytes?: number | null;
}

export interface SanitizePreviewResult {
  original_text: string;
  sanitized_text: string;
  entity_map: Record<string, string>;
  detected_types: string[];
  route_decision: {
    circuit: 'red' | 'yellow' | 'green';
    reason: string;
    requires_sanitization: boolean;
  };
}


export interface QueueResponse {
  total: number;
  filter_id: number;
  root_services?: Array<{ id: number; name: string }>;
  subservices_by_root?: Record<number, Array<{ id: number; name: string; parent_id?: number }>>;
  tasks: TaskItem[];
  active_batch_id?: string | null;
}

export interface SingleApplyPayload {
  status_id: number;
  comment: string;
  minutes: number;
  executor_ids?: string;
  is_private?: boolean;
  verified_execution_job_id?: string;
  ticket_run_id?: string;
  decision_id?: string;
  decision_version?: number;
}

export interface BulkApplyItemPayload {
  task_id: number;
  status_id: number;
  comment: string;
  minutes: number;
  executor_ids?: string;
  is_private?: boolean;
}

export interface SmartBulkApplyItemPayload {
  task_id: number;
  status_id: number;
  comment: string;
  minutes: number;
  executor_ids?: string;
  is_private?: boolean;
  decision_id: string;
  decision_version: number;
  action_type?: string;
  requires_domain_job?: boolean;
  domain_job?: {
    action: string;
    target_host?: string;
    identity?: string;
    params?: Record<string, any>;
  };
}

export interface BulkApplyResponse {
  total: number;
  success_count: number;
  failed_count: number;
  applied: Array<{ task_id: number; res: any }>;
  failed: Array<{ task_id: number; error: string }>;
}

export interface RAGMatchItem {
  task_id: number;
  name: string;
  problem: string;
  solution: string;
  service_id: number;
  service_name: string;
  status_name: string;
  similarity_pct: number;
  distance: number;
  storage_tier: string;
}

export interface OutageIncident {
  id: string;
  title: string;
  service_id?: number | null;
  service_name: string;
  severity: 'critical' | 'warning';
  status: 'active' | 'resolved';
  master_ticket_id: number;
  ticket_ids: number[];
  detected_at: number;
  updated_at: number;
  root_cause_hypothesis?: string;
  sample_titles?: string[];
}

