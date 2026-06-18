import { useAuthStore } from './stores/auth';

export async function apiFetch(url, options = {}) {
    const defaultHeaders = {
        'Content-Type': 'application/json',
    };
    
    // Если передали FormData, не устанавливаем Content-Type
    if (options.body instanceof FormData) {
        delete defaultHeaders['Content-Type'];
    }

    const mergedOptions = {
        ...options,
        headers: {
            ...defaultHeaders,
            ...options.headers,
        },
    };

    try {
        const response = await fetch(url, mergedOptions);
        
        if (response.status === 401) {
            const authStore = useAuthStore();
            authStore.setUnauthenticated();
            throw new Error('Неавторизован');
        }

        if (!response.ok) {
            let errorText = response.statusText;
            try {
                const errData = await response.json();
                errorText = errData.detail || errData.message || JSON.stringify(errData);
            } catch {
                try {
                    errorText = await response.text();
                } catch {}
            }
            throw new Error(errorText || `Ошибка HTTP: ${response.status}`);
        }

        // Если нет контента (например, 204 No Content), не пытаемся парсить JSON
        if (response.status === 204) {
            return null;
        }

        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
            return await response.json();
        }
        return await response.text();
    } catch (error) {
        console.error(`Ошибка при запросе к ${url}:`, error);
        throw error;
    }
}
