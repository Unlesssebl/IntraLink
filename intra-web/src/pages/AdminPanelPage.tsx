import React, { useState, useEffect, useCallback } from 'react';
import {
  loginAdmin,
  fetchAdminSettings,
  saveLdapsSettings,
  testLdapsSettings,
  saveHelpdeskSettings,
  saveLocalAdminSettings,
  fetchKbStats,
  fetchKbExamples,
  blacklistKbExample,
  triggerKbSync,
  type LdapsConfigDTO,
  type HelpdeskConfigDTO,
  type LocalAdminConfigDTO,
  type ConnectionTestResult,
  type KBExampleItem,
  type KBStatsResponse,
} from '../lib/adminApi';

interface AdminPanelPageProps {
  theme?: 'light' | 'dark';
}

export default function AdminPanelPage({ theme = 'light' }: AdminPanelPageProps) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('intralink_admin_token'));
  const [passwordInput, setPasswordInput] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  // Settings State
  const [activeTab, setActiveTab] = useState<'ldaps' | 'helpdesk' | 'kb' | 'security'>('ldaps');
  const [loadingSettings, setLoadingSettings] = useState(false);
  const [saveLoading, setSaveLoading] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [statusMessage, setStatusMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Knowledge Base State
  const [kbStats, setKbStats] = useState<KBStatsResponse | null>(null);
  const [kbExamples, setKbExamples] = useState<KBExampleItem[]>([]);
  const [kbTotal, setKbTotal] = useState<number>(0);
  const [kbPage, setKbPage] = useState<number>(1);
  const [kbLimit] = useState<number>(10);
  const [kbSearch, setKbSearch] = useState<string>('');
  const [kbLoading, setKbLoading] = useState<boolean>(false);
  const [kbSyncLoading, setKbSyncLoading] = useState<boolean>(false);
  const [kbSyncDays, setKbSyncDays] = useState<number>(30);
  const [blacklistingTaskId, setBlacklistingTaskId] = useState<number | null>(null);

  const [ldapsConfig, setLdapsConfig] = useState<LdapsConfigDTO>({
    server: 'dc.corporate.loc',
    port: 636,
    use_ssl: true,
    user_dn: 'svc_intralink@corporate.loc',
    password: '',
    is_password_set: false,
    base_dn: 'DC=corporate,DC=loc',
    wlan_group_name: 'WLAN-WORKNET',
    domain_name: 'corporate.loc',
  });

  const [helpdeskConfig, setHelpdeskConfig] = useState<HelpdeskConfigDTO>({
    primary_executor_id: 8664,
    default_executor_ids: '8664,10502',
    primary_filter_id: 984,
    timezone: 'Europe/Moscow',
  });

  const [localAdminConfig, setLocalAdminConfig] = useState<LocalAdminConfigDTO>({
    username: '.\\Администратор',
    password: '',
    is_password_set: false,
  });

  // Login handler
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!passwordInput.trim()) return;
    setLoginLoading(true);
    setLoginError(null);
    try {
      const res = await loginAdmin(passwordInput.trim());
      localStorage.setItem('intralink_admin_token', res.access_token);
      setToken(res.access_token);
      setPasswordInput('');
    } catch (err: any) {
      setLoginError(err.message || 'Ошибка входа');
    } finally {
      setLoginLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('intralink_admin_token');
    setToken(null);
  };

  // Load settings once token is set
  const loadSettings = useCallback(async (authToken: string) => {
    setLoadingSettings(true);
    setStatusMessage(null);
    try {
      const data = await fetchAdminSettings(authToken);
      setLdapsConfig(data.ldaps);
      setHelpdeskConfig(data.helpdesk);
      if (data.local_admin) {
        setLocalAdminConfig(data.local_admin);
      }
    } catch (err: any) {
      if (err.message?.includes('истекла')) {
        handleLogout();
      } else {
        setStatusMessage({ type: 'error', text: err.message || 'Ошибка загрузки настроек' });
      }
    } finally {
      setLoadingSettings(false);
    }
  }, []);

  const handleSaveLocalAdmin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSaveLoading(true);
    setStatusMessage(null);
    try {
      const saved = await saveLocalAdminSettings(token, localAdminConfig);
      setLocalAdminConfig(saved);
      setStatusMessage({ type: 'success', text: 'Учетные данные локального администратора (fallback) сохранены' });
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка сохранения данных локального админа' });
    } finally {
      setSaveLoading(false);
    }
  };

  // KB Handlers
  const loadKbData = useCallback(
    async (authToken: string, page = 1, search = '') => {
      setKbLoading(true);
      try {
        const [stats, examplesData] = await Promise.all([
          fetchKbStats(authToken).catch(() => null),
          fetchKbExamples(authToken, page, kbLimit, undefined, search),
        ]);
        if (stats) setKbStats(stats);
        setKbExamples(examplesData.examples || []);
        setKbTotal(examplesData.total || 0);
        setKbPage(examplesData.page || 1);
      } catch (err: any) {
        console.error('Ошибка загрузки данных базы знаний:', err);
      } finally {
        setKbLoading(false);
      }
    },
    [kbLimit]
  );

  useEffect(() => {
    if (token && activeTab === 'kb') {
      loadKbData(token, kbPage, kbSearch);
    }
  }, [token, activeTab, kbPage]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (token) {
      setKbPage(1);
      loadKbData(token, 1, kbSearch);
    }
  };

  const handleBlacklistExample = async (taskId: number) => {
    if (!token) return;
    if (
      !window.confirm(
        `Вы уверены, что хотите занести задачу #${taskId} в черный список RAG? Она перестанет участвовать в семантическом поиске.`
      )
    ) {
      return;
    }
    setBlacklistingTaskId(taskId);
    try {
      await blacklistKbExample(token, taskId);
      setStatusMessage({ type: 'success', text: `Задача #${taskId} занесена в черный список RAG.` });
      await loadKbData(token, kbPage, kbSearch);
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка добавления в черный список' });
    } finally {
      setBlacklistingTaskId(null);
    }
  };

  const handleTriggerSync = async () => {
    if (!token) return;
    setKbSyncLoading(true);
    setStatusMessage(null);
    try {
      const res = await triggerKbSync(token, kbSyncDays, 100);
      setStatusMessage({ type: 'success', text: res.message || 'Синхронизация завершена' });
      await loadKbData(token, 1, kbSearch);
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка синхронизации' });
    } finally {
      setKbSyncLoading(false);
    }
  };

  useEffect(() => {
    if (token) {
      loadSettings(token);
    }
  }, [token, loadSettings]);

  // Save LDAPS
  const handleSaveLdaps = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSaveLoading(true);
    setStatusMessage(null);
    try {
      const saved = await saveLdapsSettings(token, ldapsConfig);
      setLdapsConfig(saved);
      setStatusMessage({ type: 'success', text: 'Параметры подключения к Active Directory (LDAPS) сохранены в базе данных' });
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка сохранения' });
    } finally {
      setSaveLoading(false);
    }
  };

  // Test LDAPS
  const handleTestLdaps = async () => {
    if (!token) return;
    setTestLoading(true);
    setTestResult(null);
    setStatusMessage(null);
    try {
      const res = await testLdapsSettings(token, ldapsConfig);
      setTestResult(res);
    } catch (err: any) {
      setTestResult({
        success: false,
        latency_ms: 0,
        message: err.message || 'Не удалось связаться с сервером',
      });
    } finally {
      setTestLoading(false);
    }
  };

  // Save Helpdesk
  const handleSaveHelpdesk = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSaveLoading(true);
    setStatusMessage(null);
    try {
      const saved = await saveHelpdeskSettings(token, helpdeskConfig);
      setHelpdeskConfig(saved);
      setStatusMessage({ type: 'success', text: 'Профили инженеров и фильтр очереди успешно обновлены' });
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка сохранения' });
    } finally {
      setSaveLoading(false);
    }
  };

  // Nav to Operator Panel
  const goToOperatorPanel = () => {
    window.location.href = '/operator-panel';
  };

  // Render Login View if unauthenticated
  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-neutral-900 text-neutral-100 p-4 font-sans">
        <div className="w-full max-w-md bg-neutral-950 border border-neutral-800 rounded-2xl p-8 shadow-2xl">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-xl bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
              </svg>
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight">Панель управления IntraLink</h1>
              <p className="text-xs text-neutral-400">Маршрут /admin (Защищенная консоль)</p>
            </div>
          </div>

          <p className="text-sm text-neutral-300 mb-6 leading-relaxed">
            Вход в раздел системных интеграций (Active Directory, LDAPS, учетные данные и профили Helpdesk) защищен мастер-паролем администратора.
          </p>

          {loginError && (
            <div className="mb-5 p-3 rounded-lg bg-red-950/40 border border-red-800 text-red-300 text-xs flex items-center gap-2">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
              </svg>
              <span>{loginError}</span>
            </div>
          )}

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-neutral-400 mb-1.5">Мастер-пароль администратора</label>
              <input
                type="password"
                value={passwordInput}
                onChange={e => setPasswordInput(e.target.value)}
                placeholder="Введите ADMIN_PASSWORD"
                required
                className="w-full px-3.5 py-2.5 bg-neutral-900 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>

            <button
              type="submit"
              disabled={loginLoading}
              className="w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-xl text-sm font-medium transition-colors flex items-center justify-center gap-2 cursor-pointer shadow-lg shadow-blue-600/20"
            >
              {loginLoading ? (
                <>
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <circle cx="12" cy="12" r="10" strokeOpacity="0.25"></circle>
                    <path d="M4 12a8 8 0 0 1 8-8"></path>
                  </svg>
                  <span>Проверка...</span>
                </>
              ) : (
                <span>Войти в консоль /admin</span>
              )}
            </button>
          </form>

          <div className="mt-6 pt-6 border-t border-neutral-800/80 text-center">
            <button
              onClick={goToOperatorPanel}
              className="text-xs text-neutral-400 hover:text-neutral-200 transition-colors inline-flex items-center gap-1.5 cursor-pointer"
            >
              <span>← Перейти в операторскую панель заявок</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Render Authenticated Admin Console
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 font-sans flex flex-col">
      {/* Top Header */}
      <header className="h-16 border-b border-neutral-800 bg-neutral-900/60 backdrop-blur-md px-6 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <div className="w-8 h-8 rounded-lg bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path>
            </svg>
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-semibold tracking-tight">IntraLink Administration</h1>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                Admin Session
              </span>
            </div>
            <p className="text-[11px] text-neutral-400">Централизованная конфигурация и доменные шлюзы</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={goToOperatorPanel}
            className="px-3 py-1.5 text-xs font-medium text-neutral-300 bg-neutral-800 hover:bg-neutral-700 rounded-lg border border-neutral-700 transition-colors flex items-center gap-1.5 cursor-pointer"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="m15 18-6-6 6-6"></path>
            </svg>
            <span>В операторскую панель (/operator-panel)</span>
          </button>

          <button
            onClick={handleLogout}
            className="px-3 py-1.5 text-xs font-medium text-red-400 hover:text-red-300 hover:bg-red-950/30 rounded-lg border border-red-900/50 transition-colors cursor-pointer"
          >
            Выйти
          </button>
        </div>
      </header>

      {/* Main Container */}
      <div className="flex-1 max-w-5xl w-full mx-auto p-6 md:p-8 space-y-6">
        {/* Navigation Tabs */}
        <div className="flex items-center gap-2 border-b border-neutral-800 pb-1">
          <button
            onClick={() => setActiveTab('ldaps')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 cursor-pointer ${
              activeTab === 'ldaps'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900'
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
              <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
              <line x1="6" y1="6" x2="6.01" y2="6"></line>
              <line x1="6" y1="18" x2="6.01" y2="18"></line>
            </svg>
            <span>Active Directory & LDAPS (порт 636)</span>
          </button>

          <button
            onClick={() => setActiveTab('helpdesk')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 cursor-pointer ${
              activeTab === 'helpdesk'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900'
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
              <circle cx="9" cy="7" r="4"></circle>
              <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
              <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
            </svg>
            <span>Параметры инженеров и очереди</span>
          </button>

          <button
            onClick={() => setActiveTab('kb')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 cursor-pointer ${
              activeTab === 'kb'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900'
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
            </svg>
            <span>База знаний (RAG)</span>
          </button>

          <button
            onClick={() => setActiveTab('security')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 cursor-pointer ${
              activeTab === 'security'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900'
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
            </svg>
            <span>Безопасность & Fallback One-Liner</span>
          </button>
        </div>

        {/* Status Message Banner */}
        {statusMessage && (
          <div
            className={`p-4 rounded-xl border text-sm flex items-center gap-3 ${
              statusMessage.type === 'success'
                ? 'bg-emerald-950/30 border-emerald-800 text-emerald-300'
                : 'bg-red-950/30 border-red-800 text-red-300'
            }`}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              {statusMessage.type === 'success' ? (
                <path d="M20 6 9 17l-5-5"></path>
              ) : (
                <circle cx="12" cy="12" r="10"></circle>
              )}
            </svg>
            <span>{statusMessage.text}</span>
          </div>
        )}

        {/* Tab 1: LDAPS Config */}
        {activeTab === 'ldaps' && (
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-6">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-base font-semibold">Конфигурация Active Directory LDAPS</h2>
                <p className="text-xs text-neutral-400 mt-0.5">
                  Прямое защищенное управление учетными записями и выдача доступа к Wi-Fi (WLAN-WORKNET) без участия клиентских ПК.
                </p>
              </div>
              <span className="px-2.5 py-1 rounded-md text-[11px] font-mono bg-blue-950/60 border border-blue-800 text-blue-300">
                Порт 636 SSL/TLS
              </span>
            </div>

            <form onSubmit={handleSaveLdaps} className="space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">Контроллер домена (DC Host / IP)</label>
                  <input
                    type="text"
                    value={ldapsConfig.server}
                    onChange={e => setLdapsConfig({ ...ldapsConfig, server: e.target.value })}
                    placeholder="dc.corporate.loc или 10.x.x.x"
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">Порт LDAPS</label>
                  <input
                    type="number"
                    value={ldapsConfig.port}
                    onChange={e => setLdapsConfig({ ...ldapsConfig, port: Number(e.target.value) })}
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">Имя домена (Domain FQDN)</label>
                  <input
                    type="text"
                    value={ldapsConfig.domain_name}
                    onChange={e => setLdapsConfig({ ...ldapsConfig, domain_name: e.target.value })}
                    placeholder="corporate.loc"
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">Базовый DN каталога (Base DN)</label>
                  <input
                    type="text"
                    value={ldapsConfig.base_dn}
                    onChange={e => setLdapsConfig({ ...ldapsConfig, base_dn: e.target.value })}
                    placeholder="DC=corporate,DC=loc"
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">Сервисная учетная запись (UPN)</label>
                  <input
                    type="text"
                    value={ldapsConfig.user_dn}
                    onChange={e => setLdapsConfig({ ...ldapsConfig, user_dn: e.target.value })}
                    placeholder="svc_intralink@corporate.loc"
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="block text-xs font-medium text-neutral-400">Пароль сервисной УЗ</label>
                    {ldapsConfig.is_password_set && (
                      <span className="text-[10px] text-emerald-400 bg-emerald-950/40 px-2 py-0.5 rounded border border-emerald-800/60">
                        ✓ Зашифрован в Fernet
                      </span>
                    )}
                  </div>
                  <input
                    type="password"
                    value={ldapsConfig.password || ''}
                    onChange={e => setLdapsConfig({ ...ldapsConfig, password: e.target.value })}
                    placeholder={ldapsConfig.is_password_set ? '•••••••• (Оставьте пустым, чтобы не менять)' : 'Введите пароль'}
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">Целевая группа AD для Wi-Fi</label>
                  <input
                    type="text"
                    value={ldapsConfig.wlan_group_name}
                    onChange={e => setLdapsConfig({ ...ldapsConfig, wlan_group_name: e.target.value })}
                    placeholder="WLAN-WORKNET"
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>

              <div className="pt-2 flex items-center justify-between border-t border-neutral-800">
                <button
                  type="button"
                  onClick={handleTestLdaps}
                  disabled={testLoading}
                  className="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-200 rounded-xl text-xs font-medium border border-neutral-700 transition-colors flex items-center gap-2 cursor-pointer"
                >
                  {testLoading ? (
                    <>
                      <svg className="animate-spin h-3.5 w-3.5 text-blue-400" viewBox="0 0 24 24" fill="none">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" strokeOpacity="0.25"></circle>
                        <path fill="currentColor" d="M4 12a8 8 0 0 1 8-8v8H4z"></path>
                      </svg>
                      <span>Проверка соединения...</span>
                    </>
                  ) : (
                    <>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
                      </svg>
                      <span>Проверить подключение (Test Connection)</span>
                    </>
                  )}
                </button>

                <button
                  type="submit"
                  disabled={saveLoading}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-xl text-xs font-semibold shadow-md shadow-blue-600/20 transition-colors cursor-pointer"
                >
                  {saveLoading ? 'Сохранение...' : 'Сохранить параметры LDAPS'}
                </button>
              </div>
            </form>

            {/* Test Result Display */}
            {testResult && (
              <div
                className={`p-4 rounded-xl border text-xs space-y-2 ${
                  testResult.success
                    ? 'bg-emerald-950/20 border-emerald-800/80 text-emerald-300'
                    : 'bg-red-950/20 border-red-800/80 text-red-300'
                }`}
              >
                <div className="flex items-center justify-between font-semibold">
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${testResult.success ? 'bg-emerald-400' : 'bg-red-400'}`}></span>
                    <span>{testResult.message}</span>
                  </div>
                  <span className="font-mono text-[11px] opacity-80">{testResult.latency_ms} ms</span>
                </div>
                {testResult.details && Object.keys(testResult.details).length > 0 && (
                  <pre className="mt-2 p-2 bg-neutral-950/80 rounded-lg text-[11px] font-mono overflow-x-auto text-neutral-300 border border-neutral-800">
                    {JSON.stringify(testResult.details, null, 2)}
                  </pre>
                )}
              </div>
            )}
            {/* Fallback Local Admin Account for LiteManager / DameWare */}
            <div className="pt-6 border-t border-neutral-800/80">
              <form onSubmit={handleSaveLocalAdmin} className="space-y-4">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-neutral-200">
                      Локальная учетная запись администратора (Fallback для LiteManager / DameWare)
                    </h3>
                    <span className="text-[10px] font-mono text-emerald-400 bg-emerald-950/60 border border-emerald-800/60 px-2 py-0.5 rounded-md">
                      Fernet Encrypted
                    </span>
                  </div>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    Используется в качестве резервной аутентификации, когда контроллер домена недоступен или машина отсоединена от домена.
                  </p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-xs text-neutral-400">Логин локального администратора</label>
                    <input
                      type="text"
                      value={localAdminConfig.username}
                      onChange={(e) => setLocalAdminConfig({ ...localAdminConfig, username: e.target.value })}
                      placeholder=".\Администратор или .\Administrator"
                      className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-3.5 py-2 text-xs font-mono text-neutral-200 focus:outline-none focus:border-blue-500"
                    />
                  </div>

                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <label className="text-xs text-neutral-400">Пароль локального администратора</label>
                      {localAdminConfig.is_password_set && (
                        <span className="text-[10px] text-emerald-400">✓ Пароль зашифрован и сохранен</span>
                      )}
                    </div>
                    <input
                      type="password"
                      value={localAdminConfig.password || ''}
                      onChange={(e) => setLocalAdminConfig({ ...localAdminConfig, password: e.target.value })}
                      placeholder={localAdminConfig.is_password_set ? '•••••••• (введите новый для изменения)' : 'Введите локальный пароль'}
                      className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-3.5 py-2 text-xs font-mono text-neutral-200 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                </div>

                <div className="flex justify-end pt-2">
                  <button
                    type="submit"
                    disabled={saveLoading}
                    className="px-5 py-2 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-200 rounded-xl text-xs font-semibold border border-neutral-700 transition-colors cursor-pointer"
                  >
                    {saveLoading ? 'Сохранение...' : 'Сохранить данные локального админа'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Tab 2: Helpdesk Parameters */}
        {activeTab === 'helpdesk' && (
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-6">
            <div>
              <h2 className="text-base font-semibold">Параметры Helpdesk и Очереди</h2>
              <p className="text-xs text-neutral-400 mt-0.5">
                Назначение исполнителей по умолчанию для списания трудозатрат и привязка фильтра первой линии.
              </p>
            </div>

            <form onSubmit={handleSaveHelpdesk} className="space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">ID основного инженера (Primary Assignee)</label>
                  <input
                    type="number"
                    value={helpdeskConfig.primary_executor_id}
                    onChange={e => setHelpdeskConfig({ ...helpdeskConfig, primary_executor_id: Number(e.target.value) })}
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                  <span className="text-[11px] text-neutral-500 mt-1 block">ID инженера, на которого списываются трудозатраты (по умолчанию 8664)</span>
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">ID списка исполнителей (Default Assignees)</label>
                  <input
                    type="text"
                    value={helpdeskConfig.default_executor_ids}
                    onChange={e => setHelpdeskConfig({ ...helpdeskConfig, default_executor_ids: e.target.value })}
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono"
                  />
                  <span className="text-[11px] text-neutral-500 mt-1 block">Через запятую: основной инженер и ассистент (например: 8664,10502)</span>
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">ID основного фильтра очереди IntraService</label>
                  <input
                    type="number"
                    value={helpdeskConfig.primary_filter_id}
                    onChange={e => setHelpdeskConfig({ ...helpdeskConfig, primary_filter_id: Number(e.target.value) })}
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                  <span className="text-[11px] text-neutral-500 mt-1 block">Номер фильтра очереди 1-й линии в IntraService (по умолчанию 984)</span>
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">Часовой пояс системы (Timezone)</label>
                  <input
                    type="text"
                    value={helpdeskConfig.timezone}
                    onChange={e => setHelpdeskConfig({ ...helpdeskConfig, timezone: e.target.value })}
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  />
                  <span className="text-[11px] text-neutral-500 mt-1 block">Часовой пояс API IntraService (например: Europe/Moscow)</span>
                </div>
              </div>

              <div className="pt-2 flex justify-end border-t border-neutral-800">
                <button
                  type="submit"
                  disabled={saveLoading}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-xl text-xs font-semibold shadow-md shadow-blue-600/20 transition-colors cursor-pointer"
                >
                  {saveLoading ? 'Сохранение...' : 'Сохранить параметры Helpdesk'}
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Tab 3: Knowledge Base RAG & Moderation */}
        {activeTab === 'kb' && (
          <div className="space-y-6">
            {/* Top Metric Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 shadow-sm flex items-center justify-between">
                <div>
                  <p className="text-[11px] font-medium text-neutral-400">Активных прецедентов RAG</p>
                  <p className="text-2xl font-bold text-neutral-100 mt-1">
                    {kbStats?.total_active_examples ?? kbTotal}
                  </p>
                  <span className="text-[10px] text-emerald-400 mt-0.5 block">Участвуют в AI-поиске</span>
                </div>
                <div className="w-10 h-10 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
                    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
                  </svg>
                </div>
              </div>

              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 shadow-sm flex items-center justify-between">
                <div>
                  <p className="text-[11px] font-medium text-neutral-400">В черном списке (Blacklist)</p>
                  <p className="text-2xl font-bold text-red-400 mt-1">
                    {kbStats?.total_blacklisted_examples ?? 0}
                  </p>
                  <span className="text-[10px] text-neutral-500 mt-0.5 block">Исключены модератором</span>
                </div>
                <div className="w-10 h-10 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
                  </svg>
                </div>
              </div>

              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 shadow-sm flex items-center justify-between">
                <div>
                  <p className="text-[11px] font-medium text-neutral-400">Охвачено категорий</p>
                  <p className="text-2xl font-bold text-neutral-100 mt-1">
                    {kbStats?.services_count ?? 0}
                  </p>
                  <span className="text-[10px] text-neutral-500 mt-0.5 block">Разделов каталога</span>
                </div>
                <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect>
                    <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path>
                  </svg>
                </div>
              </div>
            </div>

            {/* Sync & Search Control Panel */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <h2 className="text-base font-semibold">Синхронизация и Модерация</h2>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    Управление векторными знаниями pgvector: обучение по закрытым заявкам и удаление ошибок.
                  </p>
                </div>

                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-neutral-400">Глубина:</span>
                    <select
                      value={kbSyncDays}
                      onChange={e => setKbSyncDays(Number(e.target.value))}
                      className="px-2.5 py-1.5 bg-neutral-950 border border-neutral-700 rounded-lg text-xs text-neutral-200 focus:outline-none focus:border-blue-500 cursor-pointer"
                    >
                      <option value={14}>14 дней</option>
                      <option value={30}>30 дней</option>
                      <option value={60}>60 дней</option>
                      <option value={90}>90 дней</option>
                      <option value={180}>180 дней</option>
                    </select>
                  </div>

                  <button
                    onClick={handleTriggerSync}
                    disabled={kbSyncLoading}
                    className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-medium shadow-sm transition-colors flex items-center gap-1.5 cursor-pointer"
                  >
                    {kbSyncLoading ? (
                      <>
                        <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
                        <span>Обучение...</span>
                      </>
                    ) : (
                      <>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path>
                        </svg>
                        <span>Запустить синхронизацию</span>
                      </>
                    )}
                  </button>
                </div>
              </div>

              {/* Search input */}
              <form onSubmit={handleSearchSubmit} className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type="text"
                    value={kbSearch}
                    onChange={e => setKbSearch(e.target.value)}
                    placeholder="Поиск по теме, сути проблемы или тексту решения..."
                    className="w-full pl-9 pr-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-xs text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-blue-500"
                  />
                  <svg
                    className="absolute left-3 top-2.5 text-neutral-500"
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                  </svg>
                </div>
                <button
                  type="submit"
                  disabled={kbLoading}
                  className="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 rounded-xl text-xs font-medium border border-neutral-700 transition-colors cursor-pointer"
                >
                  Найти
                </button>
                {kbSearch && (
                  <button
                    type="button"
                    onClick={() => {
                      setKbSearch('');
                      if (token) {
                        setKbPage(1);
                        loadKbData(token, 1, '');
                      }
                    }}
                    className="px-3 py-2 text-xs text-neutral-400 hover:text-neutral-200 transition-colors cursor-pointer"
                  >
                    Сброс
                  </button>
                )}
              </form>
            </div>

            {/* Knowledge Base Examples Table */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-neutral-800 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold">Проиндексированные прецеденты</h3>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-neutral-800 text-neutral-300">
                    {kbTotal} записей
                  </span>
                </div>
                {kbLoading && (
                  <span className="text-xs text-neutral-400 flex items-center gap-1.5">
                    <span className="w-3 h-3 border-2 border-neutral-400/30 border-t-neutral-400 rounded-full animate-spin"></span>
                    Загрузка...
                  </span>
                )}
              </div>

              {kbExamples.length === 0 && !kbLoading ? (
                <div className="p-12 text-center text-neutral-500 text-xs">
                  {kbSearch ? 'По вашему запросу прецедентов не найдено.' : 'База знаний пока пуста. Запустите синхронизацию выше.'}
                </div>
              ) : (
                <div className="divide-y divide-neutral-800/60">
                  {kbExamples.map(item => (
                    <div key={item.task_id} className="p-5 hover:bg-neutral-800/30 transition-colors space-y-2.5">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex items-center gap-2.5">
                          <span className="px-2 py-0.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-400 text-xs font-mono font-semibold">
                            #{item.task_id}
                          </span>
                          <span className="text-xs font-semibold text-neutral-200">
                            {item.original_name || 'Без названия'}
                          </span>
                          <span className="px-2 py-0.5 rounded text-[10px] bg-neutral-800 text-neutral-400">
                            {item.service_name}
                          </span>
                        </div>

                        <button
                          onClick={() => handleBlacklistExample(item.task_id)}
                          disabled={blacklistingTaskId === item.task_id}
                          title="Занести в черный список RAG"
                          className="px-2.5 py-1 text-[11px] font-medium text-red-400 hover:text-red-300 hover:bg-red-950/40 rounded-lg border border-red-900/40 transition-colors flex items-center gap-1 cursor-pointer"
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <circle cx="12" cy="12" r="10"></circle>
                            <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
                          </svg>
                          <span>{blacklistingTaskId === item.task_id ? 'Блокировка...' : 'В черный список'}</span>
                        </button>
                      </div>

                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                        <div className="p-3 bg-neutral-950/60 rounded-xl border border-neutral-800/80">
                          <p className="text-[10px] uppercase font-semibold text-neutral-500 tracking-wider mb-1">
                            Суть проблемы / Запрос
                          </p>
                          <p className="text-neutral-300 leading-relaxed line-clamp-3">
                            {item.problem || '—'}
                          </p>
                        </div>

                        <div className="p-3 bg-emerald-950/10 rounded-xl border border-emerald-900/20">
                          <p className="text-[10px] uppercase font-semibold text-emerald-500 tracking-wider mb-1">
                            Решение / Ответ
                          </p>
                          <p className="text-neutral-300 leading-relaxed line-clamp-3">
                            {item.solution || '—'}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Pagination Bar */}
              {kbTotal > kbLimit && (
                <div className="px-6 py-3 border-t border-neutral-800 flex items-center justify-between text-xs text-neutral-400">
                  <span>
                    Страница {kbPage} из {Math.ceil(kbTotal / kbLimit)}
                  </span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setKbPage(prev => Math.max(prev - 1, 1))}
                      disabled={kbPage <= 1 || kbLoading}
                      className="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40 text-neutral-300 rounded-lg border border-neutral-700 transition-colors cursor-pointer"
                    >
                      ← Назад
                    </button>
                    <button
                      onClick={() => setKbPage(prev => (prev * kbLimit < kbTotal ? prev + 1 : prev))}
                      disabled={kbPage * kbLimit >= kbTotal || kbLoading}
                      className="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40 text-neutral-300 rounded-lg border border-neutral-700 transition-colors cursor-pointer"
                    >
                      Вперед →
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tab 4: Security & Fallback One-Liner */}
        {activeTab === 'security' && (
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-6">
            <div>
              <h2 className="text-base font-semibold">Архитектура безопасности и Fallback One-Liner</h2>
              <p className="text-xs text-neutral-400 mt-0.5">
                Исполнение задач без доменного GPO и при заблокированных брандмауэром портах WinRM/WMI.
              </p>
            </div>

            <div className="space-y-4 text-xs text-neutral-300 leading-relaxed">
              <div className="p-4 rounded-xl bg-neutral-950 border border-neutral-800 space-y-2">
                <div className="flex items-center gap-2 text-blue-400 font-semibold">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="12" y1="16" x2="12" y2="12"></line>
                    <line x1="12" y1="8" x2="12.01" y2="8"></line>
                  </svg>
                  <span>Принцип действия Fallback One-Liner (Self-Service)</span>
                </div>
                <p>
                  Если брандмауэр Windows на ПК заявителя блокирует входящие порты <code className="text-blue-300">5985</code> (WinRM) и <code className="text-blue-300">135</code> (WMI), система генерирует токенизированную ссылку на PowerShell скрипт. Заявитель или дежурный инженер запускает ее через <kbd className="px-1.5 py-0.5 bg-neutral-800 border border-neutral-700 rounded text-[10px]">Win + R</kbd>:
                </p>
                <div className="p-2.5 bg-black/60 rounded-lg font-mono text-[11px] text-emerald-400 border border-neutral-800">
                  powershell -ep bypass -c "irm http://&lt;core-api&gt;:8000/api/v1/run/&lt;token&gt; | iex"
                </div>
                <p className="text-neutral-400">
                  Скрипт локально регистрирует порт и драйвер принтера, рапортует об успехе в Core API по исходящему HTTPS (порт 443 всегда открыт) и завершает инцидент.
                </p>
              </div>

              <div className="p-4 rounded-xl bg-neutral-950 border border-neutral-800 space-y-2">
                <div className="flex items-center gap-2 text-emerald-400 font-semibold">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                  </svg>
                  <span>Шифрование чувствительных данных (Fernet SSOT)</span>
                </div>
                <p>
                  Все учетные данные Active Directory, сохраняемые через веб-интерфейс, шифруются симметричным ключом Fernet перед записью в PostgreSQL (<code className="text-emerald-300">system_settings</code>). Они никогда не передаются в открытом виде обратно в браузер.
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
