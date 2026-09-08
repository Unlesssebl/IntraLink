export const AUTH_UNAUTHORIZED_EVENT = 'intralink:unauthorized';

let refreshPromise: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetch('/admin/api/refresh', {
      method: 'POST',
      credentials: 'include',
    }).then(async response => {
      if (!response.ok) return false;
      const data = await response.json();
      if (localStorage.getItem('intralink_admin_token') && data.access_token) {
        localStorage.setItem('intralink_admin_token', data.access_token);
      }
      return true;
    }).catch(() => false).finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

export async function apiFetch<T = any>(url: string, options: RequestInit = {}, allowRefresh = true): Promise<T> {
  const defaultHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('intralink_admin_token') : null;
  if (token) {
    defaultHeaders['Authorization'] = `Bearer ${token}`;
  }

  if (options.body instanceof FormData) {
    delete defaultHeaders['Content-Type'];
  }

  const mergedOptions: RequestInit = {
    credentials: 'include',
    ...options,
    headers: {
      ...defaultHeaders,
      ...(options.headers as Record<string, string>),
    },
  };

  try {
    const response = await fetch(url, mergedOptions);

    if (response.status === 401) {
      if (allowRefresh && url !== '/admin/api/refresh' && await refreshSession()) {
        return apiFetch<T>(url, options, false);
      }
      window.dispatchEvent(new CustomEvent(AUTH_UNAUTHORIZED_EVENT));
      throw new Error('Сессия завершена или неавторизован');
    }

    if (!response.ok) {
      let errorText = response.statusText;
      try {
        const errData = await response.json();
        if (Array.isArray(errData.detail)) {
          errorText = errData.detail
            .map((item: any) => {
              if (typeof item === 'string') return item;
              if (item?.msg) {
                const loc = Array.isArray(item.loc)
                  ? item.loc.filter((p: any) => p !== 'body').join('.')
                  : '';
                return loc ? `${loc}: ${item.msg}` : item.msg;
              }
              return JSON.stringify(item);
            })
            .join('; ');
        } else if (typeof errData.detail === 'string') {
          errorText = errData.detail;
        } else if (errData.detail) {
          errorText = JSON.stringify(errData.detail);
        } else if (errData.message) {
          errorText = errData.message;
        } else {
          errorText = JSON.stringify(errData);
        }
      } catch {
        try {
          errorText = await response.text();
        } catch {
          // ignore
        }
      }
      throw new Error(errorText || `Ошибка HTTP: ${response.status}`);
    }

    if (response.status === 204) {
      return null as T;
    }

    const contentType = response.headers.get('content-type');
    if (contentType && contentType.includes('application/json')) {
      return (await response.json()) as T;
    }
    return (await response.text()) as unknown as T;
  } catch (error) {
    console.error(`Ошибка при запросе к ${url}:`, error);
    throw error;
  }
}
