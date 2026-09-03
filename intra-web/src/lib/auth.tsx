import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { apiFetch, AUTH_UNAUTHORIZED_EVENT } from './api';

export interface UserSession {
  username: string;
}

interface AuthContextType {
  isLoggedIn: boolean;
  user: UserSession | null;
  loading: boolean;
  loginError: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  checkSession: () => Promise<boolean>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [user, setUser] = useState<UserSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [loginError, setLoginError] = useState<string | null>(null);

  const checkSession = useCallback(async (): Promise<boolean> => {
    setLoading(true);
    try {
      const data = await apiFetch<UserSession>('/admin/api/me');
      if (data && data.username) {
        setIsLoggedIn(true);
        setUser(data);
        return true;
      } else {
        setIsLoggedIn(false);
        setUser(null);
        return false;
      }
    } catch {
      setIsLoggedIn(false);
      setUser(null);
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setLoginError(null);
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

      setIsLoggedIn(true);
      await checkSession();
    } catch (error: any) {
      setLoginError(error.message || 'Ошибка входа');
      setIsLoggedIn(false);
      throw error;
    }
  }, [checkSession]);

  const logout = useCallback(async () => {
    try {
      await fetch('/admin/api/logout', { method: 'POST' });
    } catch (error) {
      console.error('Ошибка при логауте:', error);
    } finally {
      setIsLoggedIn(false);
      setUser(null);
    }
  }, []);

  useEffect(() => {
    checkSession();

    const handleUnauthorized = () => {
      setIsLoggedIn(false);
      setUser(null);
    };

    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
    return () => {
      window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
    };
  }, [checkSession]);

  return (
    <AuthContext.Provider
      value={{
        isLoggedIn,
        user,
        loading,
        loginError,
        login,
        logout,
        checkSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
