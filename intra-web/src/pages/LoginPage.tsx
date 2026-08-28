import { useState } from 'react';
import { useAuth } from '../lib/auth';

interface Props {
  onLogin?: () => void;
}

export default function LoginPage({ onLogin }: Props) {
  const { login: doLogin } = useAuth();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!login || !password) {
      setError('Введите логин и пароль');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await doLogin(login, password);
      onLogin?.();
    } catch (err: any) {
      setError(err.message || 'Неверный логин или пароль');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-full flex items-center justify-center bg-neutral-50 dark:bg-neutral-950">
      <div className="w-full max-w-sm px-4">
        <div className="mb-10">
          <div className="flex items-center gap-2.5 mb-8">
            <div className="w-7 h-7 bg-neutral-900 dark:bg-neutral-100 rounded flex items-center justify-center">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <rect x="1" y="1" width="5" height="5" rx="1" fill="currentColor" className="text-neutral-100 dark:text-neutral-900"/>
                <rect x="8" y="1" width="5" height="5" rx="1" fill="currentColor" className="text-neutral-100 dark:text-neutral-900"/>
                <rect x="1" y="8" width="5" height="5" rx="1" fill="currentColor" className="text-neutral-100 dark:text-neutral-900"/>
                <rect x="8" y="8" width="5" height="5" rx="1" fill="currentColor" className="text-neutral-400"/>
              </svg>
            </div>
            <span className="text-sm font-semibold tracking-tight text-neutral-900 dark:text-neutral-100">IntraLink</span>
          </div>
          <h1 className="text-[22px] font-semibold text-neutral-900 dark:text-neutral-50 tracking-tight leading-tight">
            Вход в Helpdesk
          </h1>
          <p className="mt-1.5 text-sm text-neutral-500 dark:text-neutral-400">
            Используйте корпоративные учётные данные IntraService
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1.5">
              Логин
            </label>
            <input
              type="text"
              value={login}
              onChange={e => setLogin(e.target.value)}
              placeholder="логин IntraService"
              autoFocus
              className="w-full px-3 py-2 text-sm bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 dark:placeholder-neutral-600 focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500 dark:focus:border-blue-500 transition-colors font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1.5">
              Пароль
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full px-3 py-2 text-sm bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 dark:placeholder-neutral-600 focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500 dark:focus:border-blue-500 transition-colors"
            />
          </div>

          {error && (
            <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 px-4 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-sm font-medium rounded hover:bg-neutral-700 dark:hover:bg-neutral-300 disabled:opacity-50 transition-colors mt-1 cursor-pointer"
          >
            {loading ? 'Проверка...' : 'Войти'}
          </button>
        </form>

        <p className="mt-6 text-xs text-neutral-400 dark:text-neutral-600 text-center">
          IntraLink Helpdesk · Core Gateway
        </p>
      </div>
    </div>
  );
}
