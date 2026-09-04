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
  has_ai_solution?: boolean;
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
  has_ai_solution?: boolean;
  status_id: number;
  status_name: string;
  pc_name: string;
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
  kb_matches?: RAGMatchItem[];
  telemetry?: any;
  circuit?: 'red' | 'yellow' | 'green';
  circuit_reason?: string;
  requires_sanitization?: boolean;
  ai_suggestion?: AISuggestionState;
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
}

export interface SingleApplyPayload {
  status_id: number;
  comment: string;
  minutes: number;
  executor_ids?: string;
  is_private?: boolean;
  verified_execution_job_id?: string;
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

