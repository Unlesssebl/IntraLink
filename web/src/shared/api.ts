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

export function getStoredUserId(): string | null {
  return localStorage.getItem("intralink_user_id");
}

export function setStoredAuth(token: string, login?: string, userId?: number | string): void {
  localStorage.setItem("intralink_auth_b64", token);
  if (login) localStorage.setItem("intralink_user_login", login);
  if (userId) localStorage.setItem("intralink_user_id", String(userId));
}

export function clearStoredAuth(): void {
  localStorage.removeItem("intralink_auth_b64");
  localStorage.removeItem("intralink_user_login");
  localStorage.removeItem("intralink_user_id");
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
  priority_name?: string | null;
  executor_name?: string | null;
  pc_name?: string | null;
}

export interface TicketEntities {
  pc_name?: string;
  phone?: string;
  room?: string;
  department?: string;
  user_name?: string;
  email?: string;
  inventory_number?: string;
  [key: string]: any;
}

export interface TicketAttachment {
  id: number;
  name: string;
  size?: number;
  [key: string]: any;
}

export interface TicketLifetimeEvent {
  id: number;
  task_id?: number;
  created?: string;
  user_name?: string;
  comment?: string;
  old_status_name?: string;
  new_status_name?: string;
  [key: string]: any;
}

export interface TicketDetail {
  id: number;
  name: string;
  description: string;
  created?: string | null;
  service_id?: number | null;
  service_name?: string | null;
  status_id: number;
  status_name: string;
  priority_name?: string | null;
  creator_name?: string | null;
  applicant_name?: string | null;
  applicant_phone?: string | null;
  applicant_email?: string | null;
  pc_name?: string | null;
  executor_ids?: string | null;
  entities?: TicketEntities;
  custom_fields?: Record<string, string>;
  attachments?: TicketAttachment[];
}

export interface ServiceItem {
  id: number;
  name: string;
  parent_id?: number | null;
  is_active?: boolean;
  description?: string | null;
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

export interface EngineerLoadMetric {
  user_id: number;
  user_name: string;
  total_assigned: number;
  total_closed: number;
  avg_resolution_hours: number;
  reopened_count: number;
}

export interface LoadReport {
  year: number;
  month: number;
  is_closed_month?: boolean;
  total_tickets: number;
  closed_tickets: number;
  avg_resolution_hours: number;
  engineer_load?: Record<string, number>;
  service_load?: Record<string, number>;
  engineers?: EngineerLoadMetric[];
  cached?: boolean;
  is_cached?: boolean;
}

// -------------------------------------------------------------
// API Endpoints
// -------------------------------------------------------------

export const ticketsApi = {
  list: (
    params: {
      filter_id?: number;
      service_id?: number;
      status_id?: number;
      limit?: number;
      offset?: number;
    } = {},
    signal?: AbortSignal
  ) => {
    const q = new URLSearchParams();
    if (params.filter_id !== undefined) q.set("filter_id", String(params.filter_id));
    if (params.service_id !== undefined) q.set("service_id", String(params.service_id));
    if (params.status_id !== undefined) q.set("status_id", String(params.status_id));
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));
    return request<TicketListItem[]>(`/tickets?${q.toString()}`, { signal });
  },

  get: (id: number, signal?: AbortSignal) => request<TicketDetail>(`/tickets/${id}`, { signal }),

  getLifetime: (id: number, signal?: AbortSignal) =>
    request<TicketLifetimeEvent[]>(`/tickets/${id}/lifetime`, { signal }),

  update: (
    id: number,
    data: { status_id?: number; comment?: string; executor_ids?: string; is_private?: boolean }
  ) =>
    request<{ ticket_id: number; updated: boolean }>(`/tickets/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  addComment: (id: number, comment: string, is_private: boolean = false) =>
    request<{ ticket_id: number; comment_added: boolean }>(`/tickets/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ comment, is_private }),
    }),

  take: (id: number) =>
    ticketsApi.update(id, {
      status_id: 2,
      executor_ids: getStoredUserId() || undefined,
    }),

  resolve: (id: number, comment: string) =>
    ticketsApi.update(id, {
      status_id: 5,
      comment,
      is_private: false,
    }),

  cancel: (id: number, comment: string, reason?: string) =>
    ticketsApi.update(id, {
      status_id: 30,
      comment: reason ? `[${reason}] ${comment}` : comment,
      is_private: false,
    }),

  redirect: (id: number, newServiceId: number, comment: string) =>
    ticketsApi.update(id, {
      status_id: 30,
      comment: `[Перенаправлено в сервис ID ${newServiceId}] ${comment}`,
      is_private: false,
    }),

  getServices: () => request<ServiceItem[]>("/tickets/services/catalog"),

  getAttachmentUrl: (ticketId: number, fileId: number) =>
    `${BASE_URL}/tickets/${ticketId}/attachments/${fileId}`,
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
  diagnose: (host: string, signal?: AbortSignal) =>
    request<HostDiagnostic>(`/diagnostics/host/${encodeURIComponent(host)}`, { signal }),
};

export const reportsApi = {
  getLoad: (year: number, month: number) =>
    request<LoadReport>(`/reports/load?year=${year}&month=${month}`),

  getExportUrl: (year: number, month: number) =>
    `${BASE_URL}/reports/export?year=${year}&month=${month}`,

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
