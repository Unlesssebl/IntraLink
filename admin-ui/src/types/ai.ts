export interface ServiceTreeNode {
  id: number;
  name: string;
  parent_id?: number | null;
  children?: ServiceTreeNode[];
  code?: string;
  is_active?: boolean;
}

export interface RAGExample {
  task_id: number;
  original_name: string;
  problem: string;
  solution: string;
  service_id: number;
  service_name: string;
  status_name: string;
}

export interface RAGExamplesResponse {
  total: number;
  page: number;
  limit: number;
  examples: RAGExample[];
}

export interface AIStatus {
  status?: string;
  stats?: Record<string, string>;
  auto_reply_service_ids?: number[];
  auto_reply_mode?: string;
}

export interface RAGQuotas {
  filter_id: number;
  global_quotas: Record<string, number>;
  service_quotas: Record<string, Record<string, number>>;
}
