export const AUTH_UNAUTHORIZED_EVENT = 'intralink:unauthorized';

export async function apiFetch<T = any>(url: string, options: RequestInit = {}): Promise<T> {
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
      window.dispatchEvent(new CustomEvent(AUTH_UNAUTHORIZED_EVENT));
      throw new Error('Сессия завершена или неавторизован');
    }

    if (!response.ok) {
      let errorText = response.statusText;
      try {
        const errData = await response.json();
        errorText = errData.detail || errData.message || JSON.stringify(errData);
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
