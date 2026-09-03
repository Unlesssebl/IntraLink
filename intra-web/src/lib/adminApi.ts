export interface LdapsConfigDTO {
  server: string;
  port: number;
  use_ssl: boolean;
  user_dn: string;
  password?: string;
  is_password_set: boolean;
  base_dn: string;
  wlan_group_name: string;
  domain_name: string;
}

export interface HelpdeskConfigDTO {
  primary_executor_id: number;
  default_executor_ids: string;
  primary_filter_id: number;
  timezone: string;
}

export interface LocalAdminConfigDTO {
  username: string;
  password?: string;
  is_password_set: boolean;
}

export interface AllSettingsResponse {
  ldaps: LdapsConfigDTO;
  helpdesk: HelpdeskConfigDTO;
  local_admin: LocalAdminConfigDTO;
}

export interface ConnectionTestResult {
  success: boolean;
  latency_ms: number;
  message: string;
  details?: Record<string, any>;
}

export async function loginAdmin(username: string, password: string): Promise<{ access_token: string; expires_in: number }> {
  const res = await fetch('/api/v1/admin/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка аутентификации' }));
    throw new Error(err.detail || 'Ошибка авторизации администратора');
  }
  return res.json();
}

export async function checkCurrentAdminSession(): Promise<{ username: string; is_admin: boolean; role: string } | null> {
  try {
    const res = await fetch('/admin/api/me');
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchAdminSettings(token: string): Promise<AllSettingsResponse> {
  const res = await fetch('/api/v1/admin/settings', {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    throw new Error(`Ошибка загрузки настроек (${res.status})`);
  }
  return res.json();
}


export async function testLdapsSettings(token: string, payload?: LdapsConfigDTO): Promise<ConnectionTestResult> {
  const res = await fetch('/api/v1/admin/settings/ldaps/test', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload || null),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка теста соединения' }));
    throw new Error(err.detail || 'Не удалось выполнить проверку связи');
  }
  return res.json();
}

export async function saveHelpdeskSettings(token: string, payload: HelpdeskConfigDTO): Promise<HelpdeskConfigDTO> {
  const res = await fetch('/api/v1/admin/settings/helpdesk', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка сохранения Helpdesk настроек' }));
    throw new Error(err.detail || 'Не удалось сохранить настройки Helpdesk');
  }
  return res.json();
}


export interface KBExampleItem {
  task_id: number;
  original_name: string;
  problem: string;
  solution: string;
  service_id: number;
  service_name: string;
  status_name: string;
  root_cause?: string | null;
  root_id?: string | null;
  resolution_type?: string | null;
  resolution_label?: string | null;
  resolution_badge_color?: string | null;
  quality_score?: number;
}

export interface KBExamplesResponse {
  total: number;
  page: number;
  limit: number;
  examples: KBExampleItem[];
}

export interface KBSyncReadiness {
  ready: boolean;
  auth_source: 'operator_session' | 'service_account' | 'none';
  account_name?: string | null;
  message: string;
}

export interface KBEmbeddingReadiness {
  ready: boolean;
  message: string;
  model: string;
  dimension: number;
}

export interface KBRootServiceItem {
  root_id: string;
  root_service_id: number;
  name: string;
}

export interface KBStatsResponse {
  total_active_examples: number;
  total_blacklisted_examples: number;
  services_count: number;
  services: Record<string, { total: number; by_status: Record<string, number> }>;
  sync_readiness?: KBSyncReadiness;
  embedding_readiness?: KBEmbeddingReadiness;
  root_services?: KBRootServiceItem[];
  root_counts?: Record<string, number>;
}

export interface KBSyncResponse {
  status: string;
  message: string;
  details?: Record<string, any>;
}

export async function fetchKbStats(token: string): Promise<KBStatsResponse> {
  const res = await fetch('/api/v1/admin/kb/stats', {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    throw new Error('Не удалось загрузить статистику базы знаний');
  }
  return res.json();
}

export async function fetchKbExamples(
  token: string,
  page = 1,
  limit = 20,
  serviceId?: number,
  search?: string,
  rootId?: string | null
): Promise<KBExamplesResponse> {
  const params = new URLSearchParams({
    page: String(page),
    limit: String(limit),
  });
  if (serviceId) params.set('service_id', String(serviceId));
  if (rootId) params.set('root_id', rootId);
  if (search && search.trim()) params.set('search', search.trim());

  const res = await fetch(`/api/v1/admin/kb/examples?${params.toString()}`, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    throw new Error('Не удалось загрузить прецеденты базы знаний');
  }
  return res.json();
}

export async function blacklistKbExample(
  token: string,
  taskId: number
): Promise<{ status: string; task_id: number; message: string }> {
  const res = await fetch(`/api/v1/admin/kb/examples/${taskId}`, {
    method: 'DELETE',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    throw new Error(`Не удалось скрыть задачу #${taskId} из базы знаний`);
  }
  return res.json();
}

export async function purgeKnowledgeBase(
  token: string
): Promise<{ status: string; deleted: number; message: string }> {
  const res = await fetch('/api/v1/admin/kb/purge', {
    method: 'DELETE',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    throw new Error('Не удалось очистить базу знаний RAG');
  }
  return res.json();
}

export async function triggerKbSync(token: string, days = 30, limit = 100): Promise<KBSyncResponse> {
  const res = await fetch('/api/v1/admin/kb/sync', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ days, limit }),
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Не удалось запустить синхронизацию базы знаний');
  }
  return res.json();
}

export interface KBLogEntry {
  time: string;
  level: 'info' | 'warn' | 'error' | 'success';
  message: string;
}

export interface KBSyncProgressResponse {
  is_running: boolean;
  started_at?: string | null;
  updated_at?: string | null;
  target_root_id?: string | null;
  current_root?: string | null;
  current_service_name?: string | null;
  processed_roots: number;
  total_roots: number;
  percent: number;
  total_indexed: number;
  total_skipped: number;
  total_duplicates: number;
  total_ai_errors?: number;
  service_stats?: Record<string, {
    name: string;
    existing: number;
    indexed: number;
    skipped: number;
    duplicates: number;
    quota: number;
    status: string;
  }>;
  logs?: KBLogEntry[];
  error?: string | null;
  finished_at?: string | null;
}

export interface KBStatusItem {
  id: number;
  name: string;
  is_recommended: boolean;
}

export interface KBStratifiedSyncRequest {
  quota_per_service: number;
  days: number;
  root_id?: string | null;
  status_ids?: number[];
  ai_eval?: boolean;
}

export async function fetchAvailableStatuses(token: string): Promise<KBStatusItem[]> {
  try {
    const res = await fetch('/api/v1/admin/kb/available-statuses', {
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
    });
    if (res.ok) {
      return await res.json();
    }
  } catch (e) {
    console.warn('Не удалось загрузить доступные статусы:', e);
  }
  return [
    { id: 28, name: 'Закрыта', is_recommended: true },
    { id: 29, name: 'Выполнена', is_recommended: true },
    { id: 43, name: 'Обработано 1-й линией', is_recommended: true },
    { id: 30, name: 'Отменена', is_recommended: true },
    { id: 31, name: 'Открыта', is_recommended: false },
    { id: 27, name: 'В работе', is_recommended: false },
    { id: 35, name: 'Требует уточнения', is_recommended: false },
  ];
}

export async function triggerStratifiedKbSync(
  token: string,
  payload: KBStratifiedSyncRequest
): Promise<{ status: string; message: string }> {
  const res = await fetch('/api/v1/admin/kb/sync-stratified', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Не удалось запустить умную синхронизацию');
  }
  return res.json();
}

export async function fetchKbSyncStatus(token: string): Promise<KBSyncProgressResponse> {
  const res = await fetch('/api/v1/admin/kb/sync-status', {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    throw new Error('Не удалось получить статус синхронизации');
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Credentials Vault API (SSOT)
// ---------------------------------------------------------------------------

export interface VaultStatusResponse {
  is_ready: boolean;
  service_account: {
    is_configured: boolean;
    login: string | null;
    redis_synced: boolean;
    base_url: string;
  };
  domain: {
    is_configured: boolean;
    username: string | null;
    domain: string;
    dc_host: string;
    ldaps_port: number;
    base_dn?: string;
    wlan_group_name?: string;
    redis_synced: boolean;
  };
  local_admin: {
    is_configured: boolean;
    username: string;
  };
  execution_worker: {
    online: boolean;
    heartbeat_key: string;
  };
}

export async function fetchVaultStatus(token: string): Promise<VaultStatusResponse> {
  const res = await fetch('/api/v1/admin/vault/status', {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error('Сессия администратора истекла');
    throw new Error('Не удалось загрузить статус Vault');
  }
  return res.json();
}

export async function saveVaultServiceAccount(
  token: string,
  payload: { login: string; password?: string; base_url?: string }
): Promise<any> {
  const res = await fetch('/api/v1/admin/vault/service-account', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка сохранения' }));
    throw new Error(err.detail || 'Не удалось сохранить сервисный аккаунт');
  }
  return res.json();
}

export async function saveVaultDomain(
  token: string,
  payload: {
    username: string;
    password?: string;
    domain?: string;
    dc_host?: string;
    ldaps_port?: number;
    base_dn?: string;
    wlan_group_name?: string;
  }
): Promise<any> {
  const res = await fetch('/api/v1/admin/vault/domain', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка сохранения' }));
    throw new Error(err.detail || 'Не удалось сохранить доменную конфигурацию');
  }
  return res.json();
}

export async function saveVaultLocalAdmin(
  token: string,
  payload: { username: string; password?: string }
): Promise<any> {
  const res = await fetch('/api/v1/admin/vault/local-admin', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка сохранения' }));
    throw new Error(err.detail || 'Не удалось сохранить локального администратора');
  }
  return res.json();
}

export async function testVaultWinrm(
  token: string,
  payload: { target_host: string; port?: number; timeout_sec?: number }
): Promise<ConnectionTestResult> {
  const res = await fetch('/api/v1/admin/vault/test-winrm', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка проверки' }));
    throw new Error(err.detail || 'Ошибка тестирования WinRM');
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Skills Hub & Action Registry API
// ---------------------------------------------------------------------------

export interface SkillActionItem {
  id: string;
  name: string;
  category: string;
  description: string;
  default_mode: 'auto' | 'confirm' | 'disabled';
  effective_mode: 'auto' | 'confirm' | 'disabled';
  target_type: string;
  parameters_schema: Record<string, any>;
}

export async function fetchSkills(token: string): Promise<SkillActionItem[]> {
  const res = await fetch('/api/v1/skills', {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка загрузки навыков' }));
    throw new Error(err.detail || 'Не удалось загрузить каталог навыков');
  }
  return res.json();
}

export async function updateSkillPolicy(
  token: string,
  actionId: string,
  mode: 'auto' | 'confirm' | 'disabled'
): Promise<any> {
  const res = await fetch(`/api/v1/skills/${actionId}/policy`, {
    method: 'PATCH',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка обновления политики' }));
    throw new Error(err.detail || 'Не удалось обновить политику навыка');
  }
  return res.json();
}

export async function resetSkillPolicy(token: string, actionId: string): Promise<any> {
  const res = await fetch(`/api/v1/skills/${actionId}/policy`, {
    method: 'DELETE',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка сброса политики' }));
    throw new Error(err.detail || 'Не удалось сбросить политику навыка');
  }
  return res.json();
}


export interface KBNightlyAuditProgress {
  is_running: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  percent: number;
  total_records: number;
  total_audited: number;
  blacklisted_count: number;
  high_quality_count: number;
  logs: KBLogEntry[];
  error?: string | null;
}

export async function triggerNightlyAudit(token: string): Promise<{ status: string; message: string }> {
  const res = await fetch('/api/v1/admin/kb/nightly-audit', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка запуска ночного аудита' }));
    throw new Error(err.detail || 'Не удалось запустить ночной аудит');
  }
  return await res.json();
}

export async function fetchNightlyAuditStatus(token: string): Promise<KBNightlyAuditProgress> {
  const res = await fetch('/api/v1/admin/kb/nightly-audit-status', {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    throw new Error('Не удалось получить статус ночного аудита');
  }
  return await res.json();
}