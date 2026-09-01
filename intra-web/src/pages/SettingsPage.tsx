import { useState, useEffect } from 'react';
import {
  fetchSystemStatus,
  saveServiceUser,
  deleteServiceUser,
  restartWorker,
  fetchTelegramUsers,
  addTelegramUser,
  toggleTelegramUser,
  deleteTelegramUser,
  fetchWorkerLogs,
} from '../lib/tasks';
import type { SystemStatusResponse, TelegramUserItem } from '../lib/types';

interface Props {
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
}

export default function SettingsPage({ theme, onToggleTheme }: Props) {
  // System Status State
  const [sysStatus, setSysStatus] = useState<SystemStatusResponse | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);

  // Service User State
  const [serviceLogin, setServiceLogin] = useState('');
  const [servicePassword, setServicePassword] = useState('');
  const [savingUser, setSavingUser] = useState(false);
  const [userMsg, setUserMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Telegram Users State
  const [users, setUsers] = useState<TelegramUserItem[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [newTgId, setNewTgId] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newFullName, setNewFullName] = useState('');
  const [addingUser, setAddingUser] = useState(false);

  // Logs State
  const [logs, setLogs] = useState<any[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const loadStatus = async () => {
    setStatusLoading(true);
    try {
      const data = await fetchSystemStatus();
      setSysStatus(data);
    } catch (err) {
      console.error('Ошибка загрузки статуса:', err);
    } finally {
      setStatusLoading(false);
    }
  };

  const loadUsers = async () => {
    setUsersLoading(true);
    try {
      const data = await fetchTelegramUsers();
      setUsers(data || []);
    } catch (err) {
      console.error('Ошибка загрузки пользователей:', err);
    } finally {
      setUsersLoading(false);
    }
  };

  const loadLogs = async () => {
    setLogsLoading(true);
    try {
      const data = await fetchWorkerLogs();
      setLogs(data.logs || []);
    } catch (err) {
      console.error('Ошибка загрузки логов:', err);
    } finally {
      setLogsLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
    loadUsers();
    loadLogs();
  }, []);

  const handleSaveServiceUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!serviceLogin || !servicePassword) return;
    setSavingUser(true);
    setUserMsg(null);
    try {
      await saveServiceUser(serviceLogin, servicePassword);
      setUserMsg({ type: 'success', text: 'Учетные данные сервисного пользователя успешно сохранены' });
      setServicePassword('');
      loadStatus();
    } catch (err: any) {
      setUserMsg({ type: 'error', text: err.message || 'Ошибка сохранения' });
    } finally {
      setSavingUser(false);
    }
  };

  const handleDeleteServiceUser = async () => {
    if (!confirm('Вы уверены, что хотите удалить сервисный аккаунт?')) return;
    try {
      await deleteServiceUser();
      setUserMsg({ type: 'success', text: 'Сервисный аккаунт удален' });
      loadStatus();
    } catch (err: any) {
      setUserMsg({ type: 'error', text: err.message });
    }
  };

  const handleRestartWorker = async () => {
    try {
      await restartWorker();
      alert('Воркер успешно перезапущен');
      loadStatus();
    } catch (err: any) {
      alert(`Ошибка перезапуска: ${err.message}`);
    }
  };

  const handleAddUser = async (e: React.FormEvent) => {
    e.preventDefault();
    const idNum = parseInt(newTgId, 10);
    if (!idNum) return;
    setAddingUser(true);
    try {
      await addTelegramUser({
        telegram_id: idNum,
        username: newUsername || undefined,
        full_name: newFullName || undefined,
      });
      setNewTgId('');
      setNewUsername('');
      setNewFullName('');
      loadUsers();
    } catch (err: any) {
      alert(`Ошибка добавления пользователя: ${err.message}`);
    } finally {
      setAddingUser(false);
    }
  };

  const handleToggleUser = async (tgId: number) => {
    try {
      await toggleTelegramUser(tgId);
      loadUsers();
    } catch (err: any) {
      console.error(err);
    }
  };

  const handleDeleteUser = async (tgId: number) => {
    if (!confirm(`Удалить оператора ${tgId}?`)) return;
    try {
      await deleteTelegramUser(tgId);
      loadUsers();
    } catch (err: any) {
      console.error(err);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-neutral-50 dark:bg-neutral-950 p-6 space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[18px] font-semibold text-neutral-900 dark:text-neutral-50 tracking-tight">
            Системные настройки и мониторинг
          </h1>
          <p className="text-[12px] text-neutral-500 dark:text-neutral-400 mt-0.5">
            Статус шлюза Core API, учетные записи IntraService, операторы Telegram и журналы
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onToggleTheme}
            className="px-3 py-1.5 border border-neutral-200 dark:border-neutral-800 rounded bg-white dark:bg-neutral-900 text-[12px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors"
          >
            Тема: {theme === 'dark' ? '🌙 Темная' : '☀️ Светлая'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left Column: System Status & IntraService Account */}
        <div className="space-y-6">
          {/* Status Box */}
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-5 space-y-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-[14px] font-semibold text-neutral-900 dark:text-neutral-100">
                  Состояние сервисов
                </h2>
                <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
                  Подключение к брокерам, БД и внешнему шлюзу
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={loadStatus}
                  disabled={statusLoading}
                  className="px-2.5 py-1 text-[11px] border border-neutral-200 dark:border-neutral-700 rounded text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors"
                >
                  {statusLoading ? 'Проверка...' : '🔄 Обновить'}
                </button>
                <button
                  onClick={handleRestartWorker}
                  className="px-2.5 py-1 text-[11px] bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300 border border-red-200 dark:border-red-800 rounded hover:bg-red-100 dark:hover:bg-red-900/60 transition-colors"
                >
                  ⚡ Перезапуск Worker
                </button>
              </div>
            </div>

            <div className="divide-y divide-neutral-100 dark:divide-neutral-800">
              <div className="py-2.5 flex items-center justify-between text-[12px]">
                <span className="text-neutral-700 dark:text-neutral-300 font-medium">PostgreSQL + pgvector</span>
                <span className={`px-2 py-0.5 rounded font-mono text-[11px] ${sysStatus?.db_connected ? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300' : 'bg-red-100 text-red-800'}`}>
                  {sysStatus?.db_connected ? 'Connected' : 'Offline'}
                </span>
              </div>

              <div className="py-2.5 flex items-center justify-between text-[12px]">
                <span className="text-neutral-700 dark:text-neutral-300 font-medium">Redis Streams & Pub/Sub</span>
                <span className={`px-2 py-0.5 rounded font-mono text-[11px] ${sysStatus?.redis_connected ? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300' : 'bg-red-100 text-red-800'}`}>
                  {sysStatus?.redis_connected ? 'Connected' : 'Offline'}
                </span>
              </div>

              <div className="py-2.5 flex items-center justify-between text-[12px]">
                <div>
                  <span className="text-neutral-700 dark:text-neutral-300 font-medium block">IntraService Gateway</span>
                  <span className="text-[10px] text-neutral-400 font-mono">
                    Circuit Breaker: {sysStatus?.circuit_breaker_state || 'CLOSED'}
                  </span>
                </div>
                <span className={`px-2 py-0.5 rounded font-mono text-[11px] ${sysStatus?.intraservice_connected ? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300' : 'bg-red-100 text-red-800'}`}>
                  {sysStatus?.intraservice_connected ? 'Available' : 'Unavailable'}
                </span>
              </div>

              <div className="py-2.5 flex items-center justify-between text-[12px]">
                <span className="text-neutral-700 dark:text-neutral-300 font-medium">Сервисный пользователь</span>
                <span className={`px-2 py-0.5 rounded font-mono text-[11px] ${sysStatus?.service_user_configured ? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300' : 'bg-amber-100 text-amber-800'}`}>
                  {sysStatus?.service_user_configured ? 'Настроен' : 'Не настроен'}
                </span>
              </div>
            </div>
          </div>

          {/* IntraService Account Setup */}
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-5 space-y-4 shadow-sm">
            <div>
              <h2 className="text-[14px] font-semibold text-neutral-900 dark:text-neutral-100">
                Сервисный аккаунт IntraService
              </h2>
              <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
                Используется фоновым воркером для централизованного опроса очереди и триажа
              </p>
            </div>

            <form onSubmit={handleSaveServiceUser} className="space-y-3">
              <div>
                <label className="text-[11px] text-neutral-600 dark:text-neutral-400 block mb-1">Логин IntraService</label>
                <input
                  value={serviceLogin}
                  onChange={e => setServiceLogin(e.target.value)}
                  placeholder="IntraService_dev"
                  className="w-full px-3 py-1.5 text-[12px] bg-neutral-50 dark:bg-neutral-950 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
              </div>

              <div>
                <label className="text-[11px] text-neutral-600 dark:text-neutral-400 block mb-1">Пароль</label>
                <input
                  type="password"
                  value={servicePassword}
                  onChange={e => setServicePassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full px-3 py-1.5 text-[12px] bg-neutral-50 dark:bg-neutral-950 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
              </div>

              {userMsg && (
                <div className={`p-2 rounded text-[11px] ${userMsg.type === 'success' ? 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300' : 'bg-red-50 text-red-700'}`}>
                  {userMsg.text}
                </div>
              )}

              <div className="flex items-center gap-2 pt-1">
                <button
                  type="submit"
                  disabled={savingUser || !serviceLogin || !servicePassword}
                  className="px-3.5 py-1.5 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded text-[12px] font-medium hover:bg-neutral-700 dark:hover:bg-neutral-300 disabled:opacity-50 transition-colors"
                >
                  {savingUser ? 'Сохранение...' : 'Сохранить аккаунт'}
                </button>
                {sysStatus?.service_user_configured && (
                  <button
                    type="button"
                    onClick={handleDeleteServiceUser}
                    className="px-3 py-1.5 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40 rounded text-[12px] transition-colors"
                  >
                    Удалить
                  </button>
                )}
              </div>
            </form>
          </div>
        </div>

        {/* Right Column: Telegram Users & System Logs */}
        <div className="space-y-6">
          {/* Telegram Operators */}
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-5 space-y-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-[14px] font-semibold text-neutral-900 dark:text-neutral-100">
                  Операторы Telegram-бота
                </h2>
                <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
                  Управление доступом операторов и дежурных
                </p>
              </div>
              <span className="text-[11px] font-mono text-neutral-400">
                Всего: {users.length}
              </span>
            </div>

            {/* Users Table */}
            <div className="divide-y divide-neutral-100 dark:divide-neutral-800 max-h-48 overflow-y-auto">
              {users.map(u => (
                <div key={u.telegram_id} className="py-2 flex items-center justify-between text-[12px]">
                  <div>
                    <div className="flex items-center gap-1.5 font-medium text-neutral-900 dark:text-neutral-100">
                      <span>{u.full_name || u.username || 'Без имени'}</span>
                      {u.username && <span className="text-neutral-400 text-[11px]">@{u.username}</span>}
                    </div>
                    <span className="font-mono text-[10px] text-neutral-400">ID: {u.telegram_id}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleToggleUser(u.telegram_id)}
                      className={`px-2 py-0.5 text-[10px] font-medium rounded ${u.is_active ? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300' : 'bg-neutral-200 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400'}`}
                    >
                      {u.is_active ? 'Активен' : 'Отключен'}
                    </button>
                    <button
                      onClick={() => handleDeleteUser(u.telegram_id)}
                      className="text-neutral-300 hover:text-red-500 dark:text-neutral-600 dark:hover:text-red-400 text-xs px-1"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              ))}
              {users.length === 0 && !usersLoading && (
                <div className="text-center py-3 text-[12px] text-neutral-400">
                  Нет зарегистрированных пользователей
                </div>
              )}
            </div>

            {/* Add User Form */}
            <form onSubmit={handleAddUser} className="pt-2 border-t border-neutral-100 dark:border-neutral-800 space-y-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 block">
                Добавить оператора
              </span>
              <div className="grid grid-cols-3 gap-2">
                <input
                  value={newTgId}
                  onChange={e => setNewTgId(e.target.value)}
                  placeholder="Telegram ID"
                  className="px-2.5 py-1.5 text-[12px] bg-neutral-50 dark:bg-neutral-950 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
                <input
                  value={newUsername}
                  onChange={e => setNewUsername(e.target.value)}
                  placeholder="Username"
                  className="px-2.5 py-1.5 text-[12px] bg-neutral-50 dark:bg-neutral-950 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
                <input
                  value={newFullName}
                  onChange={e => setNewFullName(e.target.value)}
                  placeholder="ФИО"
                  className="px-2.5 py-1.5 text-[12px] bg-neutral-50 dark:bg-neutral-950 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
              </div>
              <button
                type="submit"
                disabled={addingUser || !newTgId}
                className="w-full py-1.5 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded text-[11px] font-medium disabled:opacity-50 transition-colors"
              >
                + Добавить оператора
              </button>
            </form>
          </div>

          {/* System Logs Viewer */}
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-5 space-y-3 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-[14px] font-semibold text-neutral-900 dark:text-neutral-100">
                  Журнал событий Redis Streams
                </h2>
                <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
                  Поток push-уведомлений и системных событий воркера
                </p>
              </div>
              <button
                onClick={loadLogs}
                disabled={logsLoading}
                className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline"
              >
                {logsLoading ? 'Обновление...' : 'Обновить'}
              </button>
            </div>

            <div className="bg-neutral-950 text-neutral-300 font-mono text-[11px] p-3 rounded max-h-52 overflow-y-auto space-y-1.5">
              {logs.map((l, idx) => (
                <div key={idx} className="flex gap-2">
                  <span className="text-neutral-500 shrink-0">[{l.id?.slice(0, 10)}]</span>
                  <span className="text-green-400 shrink-0">[{l.type}]</span>
                  <span className="text-neutral-200 break-all">{l.message}</span>
                </div>
              ))}
              {logs.length === 0 && (
                <div className="text-neutral-500 text-center py-2">
                  Нет недавних событий в потоке stream:intraservice_events
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
