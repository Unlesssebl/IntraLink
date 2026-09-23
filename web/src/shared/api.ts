// web/src/shared/api.ts
// Pragmatic typed API client for IntraLink v2 Backend

const BASE_URL = "/api/v2";

export class ApiError extends Error {
  constructor(public status: number, message: string, public data?: any) {
    super(message);
    this.name = "ApiError";
  }
}

export function getStoredAuth(): string | null {
  return localStorage.getItem("intralink_auth_b64");
}

export function getStoredUserLogin(): string | null {
  return localStorage.getItem("intralink_user_login");
}

export function setStoredAuth(token: string, login?: string): void {
  localStorage.setItem("intralink_auth_b64", token);
  if (login) localStorage.setItem("intralink_user_login", login);
}

export function clearStoredAuth(): void {
  localStorage.removeItem("intralink_auth_b64");
  localStorage.removeItem("intralink_user_login");
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const auth = getStoredAuth();
  if (auth && !headers.has("Authorization")) {
    headers.set("Authorization", `Basic ${auth}`);
  }

  const response = await fetch(`${BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errData;
    try {
      errData = await response.json();
    } catch {
      errData = null;
    }
    const message = errData?.detail || `HTTP ${response.status}: ${response.statusText}`;
    throw new ApiError(response.status, message, errData);
  }

  return response.json() as Promise<T>;
}

// -------------------------------------------------------------
// DTO Types
// -------------------------------------------------------------

export interface TicketListItem {
  id: number;
  name: string;
  created: string;
  applicant_name?: string | null;
  applicant_phone?: string | null;
  applicant_email?: string | null;
  service_id?: number | null;
  service_name?: string | null;
  status_id?: number | null;
  status_name?: string | null;
  priority_id?: number | null;
  executor_name?: string | null;
  pc_name?: string | null;
}

export interface TicketComment {
  id: number;
  created: string;
  author_name: string;
  text: string;
  is_private: boolean;
}

export interface TicketDetail extends TicketListItem {
  description: string;
  comments: TicketComment[];
}

export interface TriageResult {
  task_id: number;
  category: string;
  confidence: number;
  reasoning: string;
  suggested_service_id?: number | null;
  draft_reply?: string | null;
  is_duplicate: boolean;
  duplicate_of_task_id?: number | null;
  ping_status?: boolean | null;
}

export interface TriageAuditItem {
  id: number;
  task_id: number;
  category: string;
  confidence: number;
  is_duplicate: boolean;
  duplicate_of_id?: number | null;
  suggested_service_id?: number | null;
  created_at: string;
}

export interface KBSearchResultItem {
  task_id: number;
  problem: string;
  solution: string;
  similarity: number;
  service_name?: string | null;
}

export interface KBAskResponse {
  answer: string;
  sources: KBSearchResultItem[];
}

export interface HostDiagnostic {
  host: string;
  is_online: boolean;
  round_trip_ms?: number | null;
  ip_address?: string | null;
  smb_port_open: boolean;
  winrm_port_open: boolean;
  checked_at: string;
}

export interface LoadReport {
  year: number;
  month: number;
  total_tickets: number;
  closed_tickets: number;
  avg_resolution_hours: number;
  engineer_load: Record<string, number>;
  service_load: Record<string, number>;
  is_cached: boolean;
}

// -------------------------------------------------------------
// API Endpoints
// -------------------------------------------------------------

export const ticketsApi = {
  list: (params: {
    filter_id?: number;
    service_id?: number;
    status_id?: number;
    limit?: number;
    offset?: number;
  } = {}) => {
    const q = new URLSearchParams();
    if (params.filter_id !== undefined) q.set("filter_id", String(params.filter_id));
    if (params.service_id !== undefined) q.set("service_id", String(params.service_id));
    if (params.status_id !== undefined) q.set("status_id", String(params.status_id));
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));
    return request<TicketListItem[]>(`/tickets?${q.toString()}`);
  },

  get: (id: number) => request<TicketDetail>(`/tickets/${id}`),

  take: (id: number) =>
    request<{ status: string; task_id: number; action: string }>(`/tickets/${id}/take`, {
      method: "POST",
    }),

  cancel: (id: number, comment: string, reason?: string) =>
    request<{ status: string; task_id: number; action: string }>(`/tickets/${id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ comment, reason }),
    }),

  addComment: (id: number, comment: string, is_private: boolean = false) =>
    request<{ status: string; task_id: number; action: string }>(`/tickets/${id}/comment`, {
      method: "POST",
      body: JSON.stringify({ comment, is_private }),
    }),
};

export const triageApi = {
  classify: (task: {
    task_id: number;
    name: string;
    description: string;
    applicant_name?: string;
    pc_name?: string;
    service_name?: string;
  }) =>
    request<TriageResult>("/triage/classify", {
      method: "POST",
      body: JSON.stringify(task),
    }),

  getAudit: (limit: number = 50) =>
    request<TriageAuditItem[]>(`/triage/audit?limit=${limit}`),
};

export const kbApi = {
  search: (query: string, limit: number = 5, threshold: number = 0.5) =>
    request<KBSearchResultItem[]>("/kb/search", {
      method: "POST",
      body: JSON.stringify({ query, limit, threshold }),
    }),

  ask: (query: string, limit: number = 5) =>
    request<KBAskResponse>("/kb/ask", {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    }),
};

export const diagnosticsApi = {
  diagnose: (host: string) =>
    request<HostDiagnostic>("/diagnostics/host", {
      method: "POST",
      body: JSON.stringify({ host }),
    }),
};

export const reportsApi = {
  getLoad: (year: number, month: number) =>
    request<LoadReport>(`/reports/load?year=${year}&month=${month}`),

  exportLoad: (year: number, month: number, format: "csv" | "excel" = "csv") =>
    request<{ year: number; month: number; format: string; download_url: string; row_count: number }>(
      `/reports/export?year=${year}&month=${month}&format=${format}`
    ),
};

export const authApi = {
  login: (login: string, password: string) =>
    request<{ status: string; auth_b64: string; user_id: number; login: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
    }),
};
