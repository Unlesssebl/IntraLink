import { defineStore } from 'pinia';
import { useCacheStore } from './cache';

export const useAuthStore = defineStore('auth', {
    state: () => ({
        isLoggedIn: false,
        user: null,
        checkingSession: true,
        loginError: null,
    }),
    actions: {
        async checkSession() {
            this.checkingSession = true;
            try {
                // Прямой fetch во избежание бесконечного цикла, если apiFetch кинет 401
                const response = await fetch('/admin/api/me');
                if (response.status === 200) {
                    const data = await response.json();
                    this.isLoggedIn = true;
                    this.user = data;
                    return true;
                } else {
                    this.isLoggedIn = false;
                    this.user = null;
                    return false;
                }
            } catch (error) {
                console.error('Ошибка проверки сессии:', error);
                this.isLoggedIn = false;
                this.user = null;
                return false;
            } finally {
                this.checkingSession = false;
            }
        },
        async login(username, password) {
            this.loginError = null;
            try {
                const response = await fetch('/admin/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });

                if (!response.ok) {
                    let errText = 'Неверный логин или пароль';
                    try {
                        const errData = await response.json();
                        errText = errData.detail || errData.message || errText;
                    } catch {}
                    throw new Error(errText);
                }

                this.isLoggedIn = true;
                await this.checkSession();
            } catch (error) {
                this.loginError = error.message;
                this.isLoggedIn = false;
                throw error;
            }
        },
        async logout() {
            try {
                await fetch('/admin/api/logout', { method: 'POST' });
            } catch (error) {
                console.error('Ошибка при логауте на сервере:', error);
            } finally {
                this.setUnauthenticated();
                // Очистка кэша
                const cacheStore = useCacheStore();
                cacheStore.invalidate();
            }
        },
        setUnauthenticated() {
            this.isLoggedIn = false;
            this.user = null;
        }
    }
});
