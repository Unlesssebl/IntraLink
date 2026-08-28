import { defineStore } from 'pinia';
import { useCacheStore } from './cache';
import type { UserSession } from '../types/common';

export const useAuthStore = defineStore('auth', {
  state: () => ({
    isLoggedIn: false as boolean,
    user: null as UserSession | null,
    checkingSession: true as boolean,
    loginError: null as string | null,
  }),
  actions: {
    async checkSession(): Promise<boolean> {
      this.checkingSession = true;
      try {
        const response = await fetch('/admin/api/me');
        if (response.status === 200) {
          const data = (await response.json()) as UserSession;
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
    async login(username: string, password: string): Promise<void> {
      this.loginError = null;
      try {
        const response = await fetch('/admin/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        });

        if (!response.ok) {
          let errText = 'Неверный логин или пароль';
          try {
            const errData = await response.json();
            errText = errData.detail || errData.message || errText;
          } catch {
            // ignore
          }
          throw new Error(errText);
        }

        this.isLoggedIn = true;
        await this.checkSession();
      } catch (error: any) {
        this.loginError = error.message;
        this.isLoggedIn = false;
        throw error;
      }
    },
    async logout(): Promise<void> {
      try {
        await fetch('/admin/api/logout', { method: 'POST' });
      } catch (error) {
        console.error('Ошибка при логауте на сервере:', error);
      } finally {
        this.setUnauthenticated();
        const cacheStore = useCacheStore();
        cacheStore.invalidate();
      }
    },
    setUnauthenticated(): void {
      this.isLoggedIn = false;
      this.user = null;
    },
  },
});
