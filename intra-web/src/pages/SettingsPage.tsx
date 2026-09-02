import { useState, useEffect, useCallback } from 'react';
import {
  fetchSystemStatus,
  fetchDomainAuth,
  saveDomainAuth,
  fetchTelegramUsers,
  addTelegramUser,
  toggleTelegramUser,
  deleteTelegramUser,
  restartWorkerService,
} from '../lib/tasks';
import { IconSun, IconMoon } from '../components/Icons';

interface Props {
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

export default function SettingsPage({ theme, onToggleTheme, onToast }: Props) {
  // System status state
  const [systemStatus, setSystemStatus] = useState<any>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [restartingWorker, setRestartingWorker] = useState(false);

  // Domain auth state
  const [domainAuth, setDomainAuth] = useState<{ is_configured: boolean; username: string | null }>({
    is_configured: false,
    username: null,
  });
  const [domainUsername, setDomainUsername] = useState('');
  const [domainPassword, setDomainPassword] = useState('');
  const [savingDomainAuth, setSavingDomainAuth] = useState(false);

  // Telegram users state
  const [tgUsers, setTgUsers] = useState<Array<{ tg_user_id: number; username?: string; full_name?: string; is_active: boolean }>>([]);
  const [loadingTgUsers, setLoadingTgUsers] = useState(false);
  const [newTgId, setNewTgId] = useState('');
  const [newTgName, setNewTgName] = useState('');
  const [newTgUsername, setNewTgUsername] = useState('');
  const [addingTgUser, setAddingTgUser] = useState(false);

  // UI preferences state
  const [tableDensity, setTableDensity] = useState<'compact' | 'normal' | 'comfortable'>(() => {
    return (localStorage.getItem('intralink_table_density') as any) || 'normal';
  });
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState<boolean>(() => {
    return localStorage.getItem('intralink_auto_refresh') !== 'false';
  });

  // Load all initial settings
  const loadAll = useCallback(async () => {
    setLoadingStatus(true);
    setLoadingTgUsers(true);
    try {
      const [sys, dom, usersRes] = await Promise.allSettled([
        fetchSystemStatus(),
        fetchDomainAuth(),
        fetchTelegramUsers(),
      ]);

      if (sys.status === 'fulfilled') setSystemStatus(sys.value);
      if (dom.status === 'fulfilled') {
        setDomainAuth(dom.value);
        if (dom.value.username) setDomainUsername(dom.value.username);
      }
      if (usersRes.status === 'fulfilled' && usersRes.value.users) {
        setTgUsers(usersRes.value.users);
      }
    } catch (err: any) {
      console.error('Ошибка загрузки настроек:', err);
    } finally {
      setLoadingStatus(false);
      setLoadingTgUsers(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Handle Domain Auth save
  const handleSaveDomainAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!domainUsername.trim()) {
      onToast({ type: 'warning', message: 'Укажите логин доменной учетной записи' });
      return;
    }

    setSavingDomainAuth(true);
    try {
      await saveDomainAuth({
        username: domainUsername.trim(),
        password: domainPassword ? domainPassword : undefined,
      });
      setDomainAuth({ is_configured: true, username: domainUsername.trim() });
      setDomainPassword('');
      onToast({ type: 'success', message: 'Доменные учетные данные успешно сохранены в защищенном хранилище' });
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка сохранения доменных данных: ${err.message || err}` });
    } finally {
      setSavingDomainAuth(false);
    }
  };

  // Handle Telegram user add
  const handleAddTgUser = async (e: React.FormEvent) => {
    e.preventDefault();
    const tgId = parseInt(newTgId.trim(), 10);
    if (isNaN(tgId) || tgId <= 0) {
      onToast({ type: 'warning', message: 'Введите корректный числовой Telegram ID' });
      return;
    }

    setAddingTgUser(true);
    try {
      await addTelegramUser({
        tg_user_id: tgId,
        username: newTgUsername.trim() || undefined,
        full_name: newTgName.trim() || undefined,
      });
      onToast({ type: 'success', message: `Пользователь ID ${tgId} добавлен в список доступа бота` });
      setNewTgId('');
      setNewTgName('');
      setNewTgUsername('');
      const updated = await fetchTelegramUsers();
      if (updated && updated.users) setTgUsers(updated.users);
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка добавления пользователя: ${err.message || err}` });
    } finally {
      setAddingTgUser(false);
    }
  };

  // Handle Telegram user toggle
  const handleToggleTgUser = async (tgUserId: number) => {
    try {
      const res = await toggleTelegramUser(tgUserId);
      setTgUsers(prev =>
        prev.map(u => (u.tg_user_id === tgUserId ? { ...u, is_active: res.is_active } : u))
      );
      onToast({
        type: 'info',
        message: `Пользователь ${tgUserId} ${res.is_active ? 'активирован' : 'деактивирован'}`,
      });
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка переключения статуса: ${err.message || err}` });
    }
  };

  // Handle Telegram user delete
  const handleDeleteTgUser = async (tgUserId: number) => {
    if (!confirm(`Удалить пользователя Telegram ID ${tgUserId} из списка доступа?`)) return;
    try {
      await deleteTelegramUser(tgUserId);
      setTgUsers(prev => prev.filter(u => u.tg_user_id !== tgUserId));
      onToast({ type: 'success', message: `Пользователь ${tgUserId} удален` });
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка удаления: ${err.message || err}` });
    }
  };

  // Handle Worker restart
  const handleRestartWorker = async () => {
    setRestartingWorker(true);
    try {
      await restartWorkerService();
      onToast({ type: 'success', message: 'Команда на перезапуск фонового воркера отправлена' });
      setTimeout(loadAll, 2000);
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка перезапуска воркера: ${err.message || err}` });
    } finally {
      setRestartingWorker(false);
    }
  };

  const handleDensityChange = (density: 'compact' | 'normal' | 'comfortable') => {
    setTableDensity(density);
    localStorage.setItem('intralink_table_density', density);
    onToast({ type: 'info', message: `Плотность таблицы: ${density === 'compact' ? 'Компактная' : density === 'normal' ? 'Стандартная' : 'Комфортная'}` });
  };

  const handleToggleAutoRefresh = () => {
    const next = !autoRefreshEnabled;
    setAutoRefreshEnabled(next);
    localStorage.setItem('intralink_auto_refresh', String(next));
    onToast({ type: 'info', message: `Фоновое автообновление очереди: ${next ? 'Включено (15 сек)' : 'Отключено'}` });
  };

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-neutral-200 dark:border-neutral-800 pb-4">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900 dark:text-neutral-100">Настройки системы</h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
            Управление интеграциями, доступом Active Directory, Telegram-пользователями и параметрами интерфейса
          </p>
        </div>
        <button
          onClick={loadAll}
          disabled={loadingStatus}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md hover:bg-neutral-50 dark:hover:bg-neutral-750 text-neutral-700 dark:text-neutral-300 transition-colors cursor-pointer"
        >
          <svg className={`w-3.5 h-3.5 ${loadingStatus ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
          </svg>
          <span>Обновить статус</span>
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Card 1: System Integrations Health */}
        <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">Интеграции и Сервисы</h2>
            </div>
            <button
              onClick={handleRestartWorker}
              disabled={restartingWorker}
              className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
            >
              {restartingWorker ? 'Перезапуск...' : 'Перезапустить воркер'}
            </button>
          </div>

          <div className="space-y-2.5 text-xs">
            <div className="flex items-center justify-between py-1.5 border-b border-neutral-100 dark:border-neutral-850">
              <span className="text-neutral-500 dark:text-neutral-400">IntraService API:</span>
              <span className={`px-2 py-0.5 rounded text-[11px] font-medium ${systemStatus?.intraservice_connected ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300'}`}>
                {systemStatus?.intraservice_connected ? 'Подключен (OK)' : 'Нет связи'}
              </span>
            </div>

            <div className="flex items-center justify-between py-1.5 border-b border-neutral-100 dark:border-neutral-850">
              <span className="text-neutral-500 dark:text-neutral-400">Circuit Breaker:</span>
              <span className={`px-2 py-0.5 rounded text-[11px] font-mono font-medium ${systemStatus?.circuit_breaker_state === 'CLOSED' ? 'bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300' : 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300'}`}>
                {systemStatus?.circuit_breaker_state || 'CLOSED'}
              </span>
            </div>

            <div className="flex items-center justify-between py-1.5 border-b border-neutral-100 dark:border-neutral-850">
              <span className="text-neutral-500 dark:text-neutral-400">Redis Streams / Кэш:</span>
              <span className={`px-2 py-0.5 rounded text-[11px] font-medium ${systemStatus?.redis_connected ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300'}`}>
                {systemStatus?.redis_connected ? 'Подключен' : 'Отключен'}
              </span>
            </div>

            <div className="flex items-center justify-between py-1.5 border-b border-neutral-100 dark:border-neutral-850">
              <span className="text-neutral-500 dark:text-neutral-400">PostgreSQL (pgvector RAG):</span>
              <span className={`px-2 py-0.5 rounded text-[11px] font-medium ${systemStatus?.db_connected ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300'}`}>
                {systemStatus?.db_connected ? 'База активна' : 'Ошибка соединения'}
              </span>
            </div>

            <div className="flex items-center justify-between py-1.5">
              <span className="text-neutral-500 dark:text-neutral-400">Фоновый воркер опроса:</span>
              <span className={`px-2 py-0.5 rounded text-[11px] font-medium ${systemStatus?.worker_running ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300'}`}>
                {systemStatus?.worker_running ? 'Работает (Active)' : 'Остановлен'}
              </span>
            </div>
          </div>
        </div>

        {/* Card 2: Domain Auth Configuration (Active Directory / WinRM) */}
        <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">Домен Active Directory & WinRM</h2>
            <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${domainAuth.is_configured ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300'}`}>
              {domainAuth.is_configured ? 'Настроена' : 'Не настроена'}
            </span>
          </div>

          <form onSubmit={handleSaveDomainAuth} className="space-y-3 text-xs">
            <div>
              <label className="block text-neutral-600 dark:text-neutral-400 mb-1 font-medium">
                Доменный логин (UPN или DOMAIN\user):
              </label>
              <input
                type="text"
                value={domainUsername}
                onChange={e => setDomainUsername(e.target.value)}
                placeholder="svc_helpdesk@corporate.loc"
                className="w-full px-2.5 py-1.5 bg-neutral-50 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded text-neutral-800 dark:text-neutral-200 focus:outline-none focus:border-blue-500 font-mono text-xs"
              />
            </div>

            <div>
              <label className="block text-neutral-600 dark:text-neutral-400 mb-1 font-medium">
                Пароль {domainAuth.is_configured && '(оставьте пустым, чтобы не менять)'}:
              </label>
              <input
                type="password"
                value={domainPassword}
                onChange={e => setDomainPassword(e.target.value)}
                placeholder={domainAuth.is_configured ? '••••••••••••' : 'Введите пароль'}
                className="w-full px-2.5 py-1.5 bg-neutral-50 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded text-neutral-800 dark:text-neutral-200 focus:outline-none focus:border-blue-500 text-xs"
              />
            </div>

            <div className="pt-1">
              <button
                type="submit"
                disabled={savingDomainAuth}
                className="w-full py-1.5 px-3 bg-neutral-900 dark:bg-neutral-100 hover:bg-neutral-800 dark:hover:bg-white text-white dark:text-neutral-900 font-medium rounded text-xs transition-colors cursor-pointer"
              >
                {savingDomainAuth ? 'Сохранение...' : 'Сохранить доменные данные'}
              </button>
            </div>
          </form>
        </div>
      </div>

      {/* Card 3: Telegram Allowed Users */}
      <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-5 space-y-4">
        <div className="flex items-center justify-between border-b border-neutral-100 dark:border-neutral-800 pb-3">
          <div>
            <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">Пользователи Telegram-бота</h2>
            <p className="text-[11px] text-neutral-500 dark:text-neutral-400">Список авторизованных операторов для мобильного взаимодействия</p>
          </div>
          <span className="text-xs font-mono text-neutral-500">{tgUsers.length} операторов</span>
        </div>

        {/* Add User Form */}
        <form onSubmit={handleAddTgUser} className="grid grid-cols-1 sm:grid-cols-4 gap-2 bg-neutral-50 dark:bg-neutral-850 p-3 rounded-md border border-neutral-200 dark:border-neutral-750">
          <input
            type="number"
            value={newTgId}
            onChange={e => setNewTgId(e.target.value)}
            placeholder="Telegram ID *"
            className="px-2.5 py-1.5 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded text-xs focus:outline-none focus:border-blue-500"
          />
          <input
            type="text"
            value={newTgName}
            onChange={e => setNewTgName(e.target.value)}
            placeholder="ФИО / Имя"
            className="px-2.5 py-1.5 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded text-xs focus:outline-none focus:border-blue-500"
          />
          <input
            type="text"
            value={newTgUsername}
            onChange={e => setNewTgUsername(e.target.value)}
            placeholder="@username"
            className="px-2.5 py-1.5 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded text-xs focus:outline-none focus:border-blue-500"
          />
          <button
            type="submit"
            disabled={addingTgUser}
            className="py-1.5 px-3 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded text-xs transition-colors cursor-pointer"
          >
            {addingTgUser ? 'Добавление...' : 'Добавить доступ'}
          </button>
        </form>

        {/* Users Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-xs text-left">
            <thead className="border-b border-neutral-200 dark:border-neutral-800 text-neutral-400 font-medium">
              <tr>
                <th className="py-2 px-3">TG ID</th>
                <th className="py-2 px-3">ФИО / Имя</th>
                <th className="py-2 px-3">Username</th>
                <th className="py-2 px-3">Статус</th>
                <th className="py-2 px-3 text-right">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100 dark:divide-neutral-850">
              {loadingTgUsers && tgUsers.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-4 text-center text-neutral-400">Загрузка списка операторов...</td>
                </tr>
              ) : tgUsers.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-4 text-center text-neutral-400">Операторы пока не добавлены</td>
                </tr>
              ) : (
                tgUsers.map(u => (
                  <tr key={u.tg_user_id} className="hover:bg-neutral-50/50 dark:hover:bg-neutral-800/40">
                    <td className="py-2.5 px-3 font-mono text-neutral-700 dark:text-neutral-300">{u.tg_user_id}</td>
                    <td className="py-2.5 px-3 font-medium text-neutral-800 dark:text-neutral-200">{u.full_name || '—'}</td>
                    <td className="py-2.5 px-3 text-neutral-500 font-mono">{u.username ? `@${u.username}` : '—'}</td>
                    <td className="py-2.5 px-3">
                      <button
                        onClick={() => handleToggleTgUser(u.tg_user_id)}
                        className={`px-2 py-0.5 rounded text-[11px] font-medium cursor-pointer transition-colors ${u.is_active ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400'}`}
                      >
                        {u.is_active ? 'Активен' : 'Отключен'}
                      </button>
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <button
                        onClick={() => handleDeleteTgUser(u.tg_user_id)}
                        className="text-neutral-400 hover:text-rose-600 transition-colors cursor-pointer p-1"
                        title="Удалить"
                      >
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                        </svg>
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Card 4: UI and Workspace Preferences */}
      <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-5 space-y-4">
        <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 border-b border-neutral-100 dark:border-neutral-800 pb-3">
          Параметры интерфейса и очереди
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
          {/* Theme switcher */}
          <div className="p-3 bg-neutral-50 dark:bg-neutral-850 rounded-md border border-neutral-200 dark:border-neutral-750 flex items-center justify-between">
            <div>
              <div className="font-medium text-neutral-800 dark:text-neutral-200">Тема оформления</div>
              <div className="text-[11px] text-neutral-500">{theme === 'dark' ? 'Тёмная' : 'Светлая'}</div>
            </div>
            <button
              onClick={onToggleTheme}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer shadow-2xs transition-colors text-neutral-800 dark:text-neutral-200"
            >
              {theme === 'dark' ? (
                <>
                  <IconSun size={13} className="text-amber-500" />
                  <span>Светлая</span>
                </>
              ) : (
                <>
                  <IconMoon size={13} className="text-indigo-500" />
                  <span>Тёмная</span>
                </>
              )}
            </button>
          </div>

          {/* Density switcher */}
          <div className="p-3 bg-neutral-50 dark:bg-neutral-850 rounded-md border border-neutral-200 dark:border-neutral-750 flex items-center justify-between">
            <div>
              <div className="font-medium text-neutral-800 dark:text-neutral-200">Плотность таблицы</div>
              <div className="text-[11px] text-neutral-500">
                {tableDensity === 'compact' ? 'Компактная' : tableDensity === 'normal' ? 'Стандартная' : 'Комфортная'}
              </div>
            </div>
            <select
              value={tableDensity}
              onChange={e => handleDensityChange(e.target.value as any)}
              className="px-2 py-1 text-xs bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded text-neutral-700 dark:text-neutral-300 cursor-pointer focus:outline-none"
            >
              <option value="compact">Компактная</option>
              <option value="normal">Стандартная</option>
              <option value="comfortable">Комфортная</option>
            </select>
          </div>

          {/* Auto Refresh toggle */}
          <div className="p-3 bg-neutral-50 dark:bg-neutral-850 rounded-md border border-neutral-200 dark:border-neutral-750 flex items-center justify-between">
            <div>
              <div className="font-medium text-neutral-800 dark:text-neutral-200">Автообновление (15с)</div>
              <div className="text-[11px] text-neutral-500">{autoRefreshEnabled ? 'Включено' : 'Отключено'}</div>
            </div>
            <button
              onClick={handleToggleAutoRefresh}
              className={`px-2.5 py-1 text-xs font-medium rounded transition-colors cursor-pointer ${autoRefreshEnabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800' : 'bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-400'}`}
            >
              {autoRefreshEnabled ? 'Активно' : 'Выкл'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
