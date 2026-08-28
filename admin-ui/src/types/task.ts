export interface HostDiagnostics {
  host?: string;
  is_online: boolean | null;
  avg_rtt: string | null;
  smb_ok?: boolean;
  winrm_ok?: boolean;
  loading?: boolean;
  status_label?: string;
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

export type AISolutionFilter = 'all' | 'ready' | 'manual';

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
}

export interface TaskTemplate {
  key: string;
  name: string;
  status_id: number;
  status_name: string;
  expenses: number;
  template: string;
  badge_color: string;
}

export interface TemplatesResponse {
  templates: TaskTemplate[];
  map: Record<string, TaskTemplate>;
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
}

export interface BulkApplyItemPayload {
  task_id: number;
  status_id: number;
  comment: string;
  minutes: number;
  executor_ids?: string;
  is_private?: boolean;
}

export interface BulkApplyRequest {
  tasks: BulkApplyItemPayload[];
}

export interface BulkApplyResponse {
  total: number;
  success_count: number;
  failed_count: number;
  applied: Array<{ task_id: number; res: any }>;
  failed: Array<{ task_id: number; error: string }>;
}

export interface ServiceTabInfo {
  id: number;
  name: string;
  short_name?: string;
  key: string;
  icon: string;
  count: number;
}
