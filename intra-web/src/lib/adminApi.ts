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

export async function loginAdmin(password: string): Promise<{ access_token: string; expires_in: number }> {
  const res = await fetch('/api/v1/admin/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка аутентификации' }));
    throw new Error(err.detail || 'Неверный мастер-пароль администратора');
  }
  return res.json();
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

export async function saveLdapsSettings(token: string, payload: LdapsConfigDTO): Promise<LdapsConfigDTO> {
  const res = await fetch('/api/v1/admin/settings/ldaps', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка сохранения настроек' }));
    throw new Error(err.detail || 'Не удалось сохранить настройки LDAPS');
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

export async function saveLocalAdminSettings(token: string, payload: LocalAdminConfigDTO): Promise<LocalAdminConfigDTO> {
  const res = await fetch('/api/v1/admin/settings/local-admin', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка сохранения настроек локального админа' }));
    throw new Error(err.detail || 'Не удалось сохранить учетные данные локального администратора');
  }
  return res.json();
}
