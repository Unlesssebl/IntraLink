import React, { useState, useEffect, useCallback } from 'react';
import {
  loginAdmin,
  checkCurrentAdminSession,
  fetchAdminSettings,
  testLdapsSettings,
  saveHelpdeskSettings,
  fetchKbStats,
  fetchKbExamples,
  blacklistKbExample,
  triggerKbSync,
  fetchVaultStatus,
  saveVaultServiceAccount,
  saveVaultDomain,
  saveVaultLocalAdmin,
  testVaultWinrm,
  type HelpdeskConfigDTO,
  type ConnectionTestResult,
  type KBExampleItem,
  type KBStatsResponse,
  type VaultStatusResponse,
} from '../lib/adminApi';
import SkillsHub from '../components/SkillsHub';
import { fetchAIHealth, fetchSanitizePreview } from '../lib/tasks';
import type { AIHealthData, SanitizePreviewResult } from '../lib/types';

interface AdminPanelPageProps {
  theme?: 'light' | 'dark';
}

export default function AdminPanelPage({ theme = 'light' }: AdminPanelPageProps) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('intralink_admin_token'));
  const [usernameInput, setUsernameInput] = useState('');
  const [passwordInput, setPasswordInput] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [adminUser, setAdminUser] = useState<{ username: string; is_admin: boolean; role?: string } | null>(null);

  // Settings State
  const [activeTab, setActiveTab] = useState<'vault' | 'skills' | 'ai-hub' | 'helpdesk' | 'kb'>('vault');
  const [loadingSettings, setLoadingSettings] = useState(false);
  const [saveLoading, setSaveLoading] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [statusMessage, setStatusMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // AI Hub Telemetry & Playground State
  const [aiHealth, setAiHealth] = useState<AIHealthData | null>(null);
  const [loadingAiHealth, setLoadingAiHealth] = useState(false);
  const [sanitizeInput, setSanitizeInput] = useState(
    'Заявитель Иванов Иван (тел. 49-87, ПК NTEMW0144, IP 192.168.1.105): сбросьте доменный пароль Secret123!'
  );
  const [sanitizeResult, setSanitizeResult] = useState<SanitizePreviewResult | null>(null);
  const [testingSanitize, setTestingSanitize] = useState(false);

  // Vault State (SSOT Credentials)
  const [vaultStatus, setVaultStatus] = useState<VaultStatusResponse | null>(null);
  const [loadingVault, setLoadingVault] = useState(false);

  const [vaultServiceLogin, setVaultServiceLogin] = useState('');
  const [vaultServicePassword, setVaultServicePassword] = useState('');
  const [vaultServiceUrl, setVaultServiceUrl] = useState('');
  const [savingVaultService, setSavingVaultService] = useState(false);

  const [vaultDomainUser, setVaultDomainUser] = useState('');
  const [vaultDomainPassword, setVaultDomainPassword] = useState('');
  const [vaultDomainName, setVaultDomainName] = useState('corporate.loc');
  const [vaultDomainDcHost, setVaultDomainDcHost] = useState('dc01.corporate.loc');
  const [vaultDomainPort, setVaultDomainPort] = useState(636);
  const [vaultDomainBaseDn, setVaultDomainBaseDn] = useState('DC=corporate,DC=loc');
  const [vaultDomainWlanGroup, setVaultDomainWlanGroup] = useState('WLAN-WORKNET');
  const [savingVaultDomain, setSavingVaultDomain] = useState(false);

  const [vaultLocalAdminUser, setVaultLocalAdminUser] = useState('.\\Администратор');
  const [vaultLocalAdminPassword, setVaultLocalAdminPassword] = useState('');
  const [savingVaultLocal, setSavingVaultLocal] = useState(false);

  const [showSecurityInfo, setShowSecurityInfo] = useState(false);

  const [winrmHost, setWinrmHost] = useState('');
  const [winrmPort, setWinrmPort] = useState(5985);
  const [testingWinrm, setTestingWinrm] = useState(false);
  const [winrmResult, setWinrmResult] = useState<ConnectionTestResult | null>(null);

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

  const [helpdeskConfig, setHelpdeskConfig] = useState<HelpdeskConfigDTO>({
    primary_executor_id: 8664,
    default_executor_ids: '8664,10502',
    primary_filter_id: 984,
    timezone: 'Europe/Moscow',
  });

  // Автоматическая проверка текущей сессии оператора (SSO)
  useEffect(() => {
    let isMounted = true;
    (async () => {
      try {
        const session = await checkCurrentAdminSession();
        if (isMounted && session) {
          setAdminUser(session);
          if (session.is_admin) {
            // Если сессия уже валидна как админ, используем её
            const existingToken = localStorage.getItem('intralink_admin_token') || 'sso_session';
            setToken(existingToken);
          }
        }
      } catch (e) {
        console.warn('Ошибка проверки SSO сессии:', e);
      } finally {
        if (isMounted) setCheckingSession(false);
      }
    })();
    return () => { isMounted = false; };
  }, []);

  // Login handler
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!usernameInput.trim() || !passwordInput.trim()) return;
    setLoginLoading(true);
    setLoginError(null);
    try {
      const res = await loginAdmin(usernameInput.trim(), passwordInput.trim());
      localStorage.setItem('intralink_admin_token', res.access_token);
      setToken(res.access_token);
      setAdminUser({ username: usernameInput.trim(), is_admin: true, role: 'admin' });
      setPasswordInput('');
    } catch (err: any) {
      setLoginError(err.message || 'Ошибка входа');
    } finally {
      setLoginLoading(false);
    }
  };

  const handleLogout = async () => {
    localStorage.removeItem('intralink_admin_token');
    setToken(null);
    setAdminUser(null);
    try {
      await fetch('/admin/api/logout', { method: 'POST' });
    } catch {}
  };

  // Load settings once token is set
  const loadSettings = useCallback(async (authToken: string) => {
    setLoadingSettings(true);
    setStatusMessage(null);
    try {
      const data = await fetchAdminSettings(authToken);
      setHelpdeskConfig(data.helpdesk);
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

  const loadVault = useCallback(async (authToken: string) => {
    setLoadingVault(true);
    try {
      const data = await fetchVaultStatus(authToken);
      setVaultStatus(data);
      if (data.service_account.login) setVaultServiceLogin(data.service_account.login);
      if (data.service_account.base_url) setVaultServiceUrl(data.service_account.base_url);
      if (data.domain.username) setVaultDomainUser(data.domain.username);
      if (data.domain.domain) setVaultDomainName(data.domain.domain);
      if (data.domain.dc_host) {
        setVaultDomainDcHost(data.domain.dc_host);
        setWinrmHost(prev => prev || data.domain.dc_host);
      }
      if (data.domain.ldaps_port) setVaultDomainPort(data.domain.ldaps_port);
      if (data.domain.base_dn) setVaultDomainBaseDn(data.domain.base_dn);
      if (data.domain.wlan_group_name) setVaultDomainWlanGroup(data.domain.wlan_group_name);
      if (data.local_admin.username) setVaultLocalAdminUser(data.local_admin.username);
    } catch (err: any) {
      if (err.message?.includes('истекла')) {
        handleLogout();
      } else {
        console.error('Ошибка загрузки Vault:', err);
      }
    } finally {
      setLoadingVault(false);
    }
  }, []);

  const handleCopyCurrentUserToService = () => {
    if (adminUser?.username) {
      setVaultServiceLogin(adminUser.username);
      setStatusMessage({
        type: 'success',
        text: `Логин '${adminUser.username}' подставлен в поле сервисного аккаунта. Введите пароль для подтверждения.`,
      });
    }
  };

  const handleSaveVaultService = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSavingVaultService(true);
    setStatusMessage(null);
    try {
      await saveVaultServiceAccount(token, {
        login: vaultServiceLogin.trim(),
        password: vaultServicePassword ? vaultServicePassword.trim() : undefined,
        base_url: vaultServiceUrl.trim() || undefined,
      });
      setVaultServicePassword('');
      setStatusMessage({ type: 'success', text: 'Сервисный аккаунт IntraService сохранен и синхронизирован с Redis (SSOT)' });
      await loadVault(token);
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка сохранения сервисного аккаунта' });
    } finally {
      setSavingVaultService(false);
    }
  };

  const handleSaveVaultDomain = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSavingVaultDomain(true);
    setStatusMessage(null);
    try {
      await saveVaultDomain(token, {
        username: vaultDomainUser.trim(),
        password: vaultDomainPassword ? vaultDomainPassword.trim() : undefined,
        domain: vaultDomainName.trim(),
        dc_host: vaultDomainDcHost.trim(),
        ldaps_port: Number(vaultDomainPort) || 636,
        base_dn: vaultDomainBaseDn.trim() || undefined,
        wlan_group_name: vaultDomainWlanGroup.trim() || undefined,
      });
      setVaultDomainPassword('');
      setStatusMessage({ type: 'success', text: 'Единые доменные учетные данные (WinRM + LDAPS) сохранены в Vault и Redis' });
      await loadVault(token);
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка сохранения доменных данных' });
    } finally {
      setSavingVaultDomain(false);
    }
  };

  const handleSaveVaultLocal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSavingVaultLocal(true);
    setStatusMessage(null);
    try {
      await saveVaultLocalAdmin(token, {
        username: vaultLocalAdminUser.trim(),
        password: vaultLocalAdminPassword ? vaultLocalAdminPassword.trim() : undefined,
      });
      setVaultLocalAdminPassword('');
      setStatusMessage({ type: 'success', text: 'Учетные данные локального администратора сохранены в Vault' });
      await loadVault(token);
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка сохранения локального администратора' });
    } finally {
      setSavingVaultLocal(false);
    }
  };

  const handleTestWinrm = async () => {
    if (!token || !winrmHost.trim()) return;
    setTestingWinrm(true);
    setWinrmResult(null);
    try {
      const res = await testVaultWinrm(token, {
        target_host: winrmHost.trim(),
        port: winrmPort,
      });
      setWinrmResult(res);
    } catch (err: any) {
      setWinrmResult({
        success: false,
        latency_ms: 0,
        message: err.message || 'Ошибка проверки WinRM соединения',
      });
    } finally {
      setTestingWinrm(false);
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
      loadVault(token);
    }
  }, [token, loadSettings, loadVault]);

  // Test LDAPS
  const handleTestLdaps = async () => {
    if (!token) return;
    setTestLoading(true);
    setTestResult(null);
    setStatusMessage(null);
    try {
      const res = await testLdapsSettings(token, {
        server: vaultDomainDcHost.trim() || vaultDomainName.trim(),
        port: Number(vaultDomainPort) || 636,
        use_ssl: true,
        user_dn: vaultDomainUser.trim(),
        password: vaultDomainPassword ? vaultDomainPassword.trim() : undefined,
        is_password_set: Boolean(vaultStatus?.domain.is_configured),
        base_dn: vaultDomainBaseDn.trim(),
        wlan_group_name: vaultDomainWlanGroup.trim(),
        domain_name: vaultDomainName.trim(),
      });
      setTestResult(res);
    } catch (err: any) {
      setTestResult({
        success: false,
        latency_ms: 0,
        message: err.message || 'Не удалось связаться с контроллером домена',
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

  // AI Hub Telemetry & Test Handlers
  const loadAiHealthData = useCallback(async () => {
    setLoadingAiHealth(true);
    try {
      const data = await fetchAIHealth();
      setAiHealth(data);
    } catch (err: any) {
      console.error('Не удалось загрузить статус AI Hub:', err);
    } finally {
      setLoadingAiHealth(false);
    }
  }, []);

  useEffect(() => {
    if (token && activeTab === 'ai-hub') {
      loadAiHealthData();
    }
  }, [token, activeTab, loadAiHealthData]);

  const handleTestSanitize = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!sanitizeInput.trim() || testingSanitize) return;
    setTestingSanitize(true);
    try {
      const res = await fetchSanitizePreview(sanitizeInput.trim());
      setSanitizeResult(res);
    } catch (err: any) {
      console.error('Ошибка проверки десенсибилизации:', err);
      setStatusMessage({ type: 'error', text: 'Ошибка при проверке маскирования PII' });
    } finally {
      setTestingSanitize(false);
    }
  };

  // Nav to Operator Panel
  const goToOperatorPanel = () => {
    window.location.href = '/operator-panel';
  };

  // State: Проверка активной сессии
  if (checkingSession) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-neutral-950 text-neutral-100 font-sans">
        <div className="flex items-center gap-3 text-sm text-neutral-400">
          <svg className="animate-spin h-5 w-5 text-blue-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
          </svg>
          <span>Проверка прав доступа администратора...</span>
        </div>
      </div>
    );
  }

  // State: Пользователь авторизован в IntraService, но не входит в список ADMIN_LOGINS
  if (!token && adminUser && !adminUser.is_admin) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-neutral-900 text-neutral-100 p-4 font-sans">
        <div className="w-full max-w-md bg-neutral-950 border border-neutral-800 rounded-2xl p-8 shadow-2xl">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-xl bg-amber-600/20 border border-amber-500/40 flex items-center justify-center text-amber-400">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
              </svg>
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight">Доступ ограничен</h1>
              <p className="text-xs text-neutral-400">Ролевая модель безопасности IntraLink</p>
            </div>
          </div>

          <div className="p-4 rounded-xl bg-neutral-900 border border-neutral-800 text-sm text-neutral-300 mb-6 space-y-2">
            <p>Вы вошли как <strong className="text-white font-mono">{adminUser.username}</strong>.</p>
            <p className="text-xs text-neutral-400 leading-relaxed">
              Данная учетная запись не входит в список администраторов системы (<code className="text-amber-400">ADMIN_LOGINS</code>). Раздел предназначен только для системных администраторов.
            </p>
          </div>

          <div className="flex flex-col gap-3">
            <button
              onClick={goToOperatorPanel}
              className="w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-sm font-medium transition-colors flex items-center justify-center gap-2 cursor-pointer shadow-lg shadow-blue-600/20"
            >
              <span>← В операторскую панель заявок</span>
            </button>
            <button
              onClick={handleLogout}
              className="w-full py-2.5 px-4 bg-neutral-900 hover:bg-neutral-800 text-neutral-400 hover:text-neutral-200 border border-neutral-800 rounded-xl text-sm font-medium transition-colors cursor-pointer"
            >
              Сменить пользователя
            </button>
          </div>
        </div>
      </div>
    );
  }

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
            Вход осуществляется по вашей <strong>корпоративной учетной записи IntraService</strong>. Доступ имеют сотрудники из утвержденного списка администраторов.
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
              <label className="block text-xs font-medium text-neutral-400 mb-1.5">Логин IntraService</label>
              <input
                type="text"
                value={usernameInput}
                onChange={e => setUsernameInput(e.target.value)}
                placeholder="например, belikov.a"
                required
                autoFocus
                className="w-full px-3.5 py-2.5 bg-neutral-900 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-neutral-400 mb-1.5">Пароль</label>
              <input
                type="password"
                value={passwordInput}
                onChange={e => setPasswordInput(e.target.value)}
                placeholder="Пароль учетной записи"
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
                  <span>Проверка прав...</span>
                </>
              ) : (
                <span>Войти с правами администратора</span>
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
                {adminUser?.username ? `Admin: ${adminUser.username}` : 'Admin Session'}
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
            onClick={() => setActiveTab('vault')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 cursor-pointer ${
              activeTab === 'vault'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900'
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
              <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
            </svg>
            <span>Хранилище секретов (Vault SSOT)</span>
          </button>

          <button
            onClick={() => setActiveTab('skills')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 cursor-pointer ${
              activeTab === 'skills'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900'
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
            </svg>
            <span>Навыки & Диспетчер (Skills Hub)</span>
          </button>

          <button
            onClick={() => setActiveTab('ai-hub')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 cursor-pointer ${
              activeTab === 'ai-hub'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900'
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 14.93V17a1 1 0 0 1-2 0v-.07A8 8 0 0 1 4.07 10H5a1 1 0 0 1 0-2h-.93A8 8 0 0 1 11 4.07V5a1 1 0 0 1 2 0v-.93A8 8 0 0 1 19.93 11H19a1 1 0 0 1 0 2h.93A8 8 0 0 1 13 16.93zM12 8a4 4 0 1 0 4 4 4 4 0 0 0-4-4z"/>
            </svg>
            <span>AI Hub & Инференс</span>
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

        {/* Tab 0: Vault SSOT */}
        {activeTab === 'vault' && (
          <div className="space-y-6">
            {/* Header & Refresh */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-semibold">Единое хранилище секретов (Credentials Vault SSOT)</h2>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-blue-500/10 text-blue-400 border border-blue-500/30">
                    Fernet + Redis
                  </span>
                </div>
                <p className="text-xs text-neutral-400 mt-1">
                  Централизованное защищенное хранилище учетных записей в PostgreSQL (system_settings) с авто-прогревом токенов в Redis.
                </p>
              </div>

              <button
                type="button"
                onClick={() => token && loadVault(token)}
                disabled={loadingVault}
                className="px-3.5 py-1.5 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-200 rounded-xl text-xs font-medium border border-neutral-700 transition-colors flex items-center gap-1.5 cursor-pointer"
              >
                <svg className={`w-3.5 h-3.5 ${loadingVault ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path>
                </svg>
                <span>{loadingVault ? 'Опрос...' : 'Обновить статус'}</span>
              </button>
            </div>

            {/* Readiness Cards Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {/* IntraService Status */}
              <div className="bg-neutral-900/90 border border-neutral-800 rounded-xl p-4 flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between text-xs text-neutral-400 mb-1">
                    <span>IntraService API</span>
                    <span className={`w-2 h-2 rounded-full ${vaultStatus?.service_account.is_configured ? 'bg-emerald-400' : 'bg-red-400'}`}></span>
                  </div>
                  <div className="text-sm font-semibold truncate">
                    {vaultStatus?.service_account.login || 'Не настроен'}
                  </div>
                </div>
                <div className="mt-3 pt-2 border-t border-neutral-800/80 flex items-center justify-between text-[11px]">
                  <span className="text-neutral-500">Redis кэш:</span>
                  <span className={vaultStatus?.service_account.redis_synced ? 'text-emerald-400 font-mono' : 'text-amber-400 font-mono'}>
                    {vaultStatus?.service_account.redis_synced ? '🟢 Прогрет' : '🟡 Ожидает'}
                  </span>
                </div>
              </div>

              {/* Domain & WinRM Status */}
              <div className="bg-neutral-900/90 border border-neutral-800 rounded-xl p-4 flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between text-xs text-neutral-400 mb-1">
                    <span>Domain & WinRM</span>
                    <span className={`w-2 h-2 rounded-full ${vaultStatus?.domain.is_configured ? 'bg-emerald-400' : 'bg-red-400'}`}></span>
                  </div>
                  <div className="text-sm font-semibold truncate">
                    {vaultStatus?.domain.username || 'Не настроен'}
                  </div>
                </div>
                <div className="mt-3 pt-2 border-t border-neutral-800/80 flex items-center justify-between text-[11px]">
                  <span className="text-neutral-500">Redis токен:</span>
                  <span className={vaultStatus?.domain.redis_synced ? 'text-emerald-400 font-mono' : 'text-amber-400 font-mono'}>
                    {vaultStatus?.domain.redis_synced ? '🟢 Прогрет' : '🟡 Ожидает'}
                  </span>
                </div>
              </div>

              {/* Local Admin Status */}
              <div className="bg-neutral-900/90 border border-neutral-800 rounded-xl p-4 flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between text-xs text-neutral-400 mb-1">
                    <span>Локальный администратор</span>
                    <span className={`w-2 h-2 rounded-full ${vaultStatus?.local_admin.is_configured ? 'bg-emerald-400' : 'bg-neutral-600'}`}></span>
                  </div>
                  <div className="text-sm font-semibold truncate">
                    {vaultStatus?.local_admin.username || '.\\Администратор'}
                  </div>
                </div>
                <div className="mt-3 pt-2 border-t border-neutral-800/80 flex items-center justify-between text-[11px]">
                  <span className="text-neutral-500">Пароль fallback:</span>
                  <span className={vaultStatus?.local_admin.is_configured ? 'text-emerald-400' : 'text-neutral-400'}>
                    {vaultStatus?.local_admin.is_configured ? 'Зашифрован' : 'Не задан'}
                  </span>
                </div>
              </div>

              {/* Execution Worker Status */}
              <div className="bg-neutral-900/90 border border-neutral-800 rounded-xl p-4 flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between text-xs text-neutral-400 mb-1">
                    <span>Execution Worker</span>
                    <span className={`w-2 h-2 rounded-full ${vaultStatus?.execution_worker.online ? 'bg-emerald-400 animate-pulse' : 'bg-neutral-600'}`}></span>
                  </div>
                  <div className="text-sm font-semibold truncate">
                    {vaultStatus?.execution_worker.online ? '🟢 Онлайн' : '⚪ Ожидание воркера'}
                  </div>
                </div>
                <div className="mt-3 pt-2 border-t border-neutral-800/80 flex items-center justify-between text-[11px]">
                  <span className="text-neutral-500">Heartbeat:</span>
                  <span className="font-mono text-neutral-400 text-[10px]">win_daemon</span>
                </div>
              </div>
            </div>

            {/* Forms Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Form 1: IntraService Service User */}
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>
                      <circle cx="9" cy="7" r="4"></circle>
                      <path d="M22 21v-2a4 4 0 0 0-3-3.87"></path>
                      <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
                    </svg>
                    <span>Сервисный аккаунт IntraService</span>
                  </h3>
                  <span className="text-[11px] font-mono text-neutral-400">worker:service_auth_b64</span>
                </div>

                <div className="p-3 rounded-xl bg-blue-950/30 border border-blue-800/60 text-xs text-blue-300 flex items-start gap-2.5">
                  <svg className="w-4 h-4 text-blue-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="12" y1="8" x2="12" y2="12"></line>
                    <line x1="12" y1="8" x2="12.01" y2="8"></line>
                  </svg>
                  <div className="leading-relaxed">
                    <span>Используется фоновым демоном опроса (Poller) и Telegram-ботом 24/7 для автономного мониторинга новых заявок, когда веб-интерфейс закрыт.</span>
                    {adminUser?.username && (
                      <div className="mt-1.5">
                        <button
                          type="button"
                          onClick={handleCopyCurrentUserToService}
                          className="inline-flex items-center gap-1 text-[11px] font-semibold text-blue-400 hover:text-blue-300 underline cursor-pointer"
                        >
                          <span>← Использовать логин текущей сессии ({adminUser.username})</span>
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                <form onSubmit={handleSaveVaultService} className="space-y-4">
                  <div>
                    <label className="block text-xs font-medium text-neutral-400 mb-1">Логин сервисного аккаунта</label>
                    <input
                      type="text"
                      value={vaultServiceLogin}
                      onChange={e => setVaultServiceLogin(e.target.value)}
                      placeholder="svc_intraservice"
                      required
                      className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-neutral-400 mb-1">Пароль аккаунта</label>
                    <input
                      type="password"
                      value={vaultServicePassword}
                      onChange={e => setVaultServicePassword(e.target.value)}
                      placeholder={vaultStatus?.service_account.is_configured ? '•••••••• (оставьте пустым для сохранения текущего)' : 'Введите пароль'}
                      className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-neutral-400 mb-1">URL IntraService API</label>
                    <input
                      type="text"
                      value={vaultServiceUrl}
                      onChange={e => setVaultServiceUrl(e.target.value)}
                      placeholder="http://192.168.1.55/api"
                      className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={savingVaultService}
                    className="w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-xl text-xs font-medium transition-colors flex items-center justify-center gap-2 cursor-pointer shadow-sm"
                  >
                    {savingVaultService ? 'Сохранение и синхронизация...' : 'Сохранить и синхронизировать с Redis'}
                  </button>
                </form>
              </div>

              {/* Form 2: Domain Credentials (WinRM + LDAPS SSOT) */}
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
                      <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
                    </svg>
                    <span>Единый доменный доступ (WinRM + LDAPS + Wi-Fi)</span>
                  </h3>
                  <span className="text-[11px] font-mono text-neutral-400">worker:domain_auth</span>
                </div>

                <form onSubmit={handleSaveVaultDomain} className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-neutral-400 mb-1">UPN логин пользователя</label>
                      <input
                        type="text"
                        value={vaultDomainUser}
                        onChange={e => setVaultDomainUser(e.target.value)}
                        placeholder="svc_intralink@corporate.loc"
                        required
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Доменный пароль</label>
                      <input
                        type="password"
                        value={vaultDomainPassword}
                        onChange={e => setVaultDomainPassword(e.target.value)}
                        placeholder={vaultStatus?.domain.is_configured ? '•••••••• (сохранен)' : 'Введите доменный пароль'}
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div className="sm:col-span-1">
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Домен</label>
                      <input
                        type="text"
                        value={vaultDomainName}
                        onChange={e => setVaultDomainName(e.target.value)}
                        placeholder="corporate.loc"
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                      />
                    </div>

                    <div className="sm:col-span-1">
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Контроллер домена</label>
                      <input
                        type="text"
                        value={vaultDomainDcHost}
                        onChange={e => {
                          setVaultDomainDcHost(e.target.value);
                          if (!winrmHost) setWinrmHost(e.target.value);
                        }}
                        placeholder="dc01.corporate.loc"
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                      />
                    </div>

                    <div className="sm:col-span-1">
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Порт LDAPS</label>
                      <input
                        type="number"
                        value={vaultDomainPort}
                        onChange={e => setVaultDomainPort(Number(e.target.value))}
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Базовый DN каталога (Base DN)</label>
                      <input
                        type="text"
                        value={vaultDomainBaseDn}
                        onChange={e => setVaultDomainBaseDn(e.target.value)}
                        placeholder="DC=corporate,DC=loc"
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Целевая группа AD для Wi-Fi</label>
                      <input
                        type="text"
                        value={vaultDomainWlanGroup}
                        onChange={e => setVaultDomainWlanGroup(e.target.value)}
                        placeholder="WLAN-WORKNET"
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 text-xs"
                      />
                    </div>
                  </div>

                  <div className="flex items-center gap-3 pt-1">
                    <button
                      type="submit"
                      disabled={savingVaultDomain}
                      className="flex-1 py-2.5 px-4 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-xl text-xs font-medium transition-colors cursor-pointer shadow-sm text-center"
                    >
                      {savingVaultDomain ? 'Сохранение...' : 'Сохранить доменные данные'}
                    </button>

                    <button
                      type="button"
                      onClick={handleTestLdaps}
                      disabled={testLoading}
                      className="py-2.5 px-4 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-200 border border-neutral-700 rounded-xl text-xs font-medium transition-colors cursor-pointer flex items-center gap-1.5"
                    >
                      {testLoading ? 'Проверка...' : 'Тест LDAPS (636)'}
                    </button>
                  </div>

                  {testResult && (
                    <div
                      className={`p-3 rounded-xl border text-xs flex items-center justify-between gap-3 mt-3 ${
                        testResult.success
                          ? 'bg-emerald-950/30 border-emerald-800 text-emerald-300'
                          : 'bg-red-950/30 border-red-800 text-red-300'
                      }`}
                    >
                      <span>{testResult.message}</span>
                      {testResult.latency_ms > 0 && (
                        <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-black/40 border border-white/10">
                          {testResult.latency_ms} ms
                        </span>
                      )}
                    </div>
                  )}
                </form>
              </div>
            </div>

            {/* Diagnostics & Fallback Row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Экспресс-тест WinRM (порт 5985) */}
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
                    </svg>
                    <span>Экспресс-тест порта WinRM (HTTP 5985)</span>
                  </h3>
                  <span className="text-[11px] font-mono text-neutral-400">Windows RPC</span>
                </div>

                <p className="text-xs text-neutral-400 leading-relaxed">
                  Проверяет сетевую доступность службы Windows Remote Management (WinRM) на контроллере домена или рабочей станции заявителя.
                </p>

                <div className="flex flex-col sm:flex-row gap-3">
                  <input
                    type="text"
                    value={winrmHost}
                    onChange={e => setWinrmHost(e.target.value)}
                    placeholder="Хост или IP (например: dc01.corporate.loc или WS-001)"
                    className="flex-1 px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                  />
                  <button
                    type="button"
                    onClick={handleTestWinrm}
                    disabled={testingWinrm || !winrmHost.trim()}
                    className="py-2 px-4 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white rounded-xl text-xs font-medium transition-colors flex items-center justify-center gap-1.5 cursor-pointer whitespace-nowrap"
                  >
                    {testingWinrm ? 'Проверка...' : 'Проверить WinRM'}
                  </button>
                </div>

                {winrmResult && (
                  <div
                    className={`p-3 rounded-xl border text-xs flex items-center justify-between gap-3 ${
                      winrmResult.success
                        ? 'bg-emerald-950/30 border-emerald-800 text-emerald-300'
                        : 'bg-red-950/30 border-red-800 text-red-300'
                    }`}
                  >
                    <span>{winrmResult.message}</span>
                    {winrmResult.latency_ms > 0 && (
                      <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-black/40 border border-white/10">
                        {winrmResult.latency_ms} ms
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Local Admin Fallback Form */}
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                      <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                    </svg>
                    <span>Резервный локальный администратор</span>
                  </h3>
                  <span className="text-[11px] font-mono text-neutral-400">DameWare / Fallback</span>
                </div>

                <form onSubmit={handleSaveVaultLocal} className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Имя пользователя</label>
                      <input
                        type="text"
                        value={vaultLocalAdminUser}
                        onChange={e => setVaultLocalAdminUser(e.target.value)}
                        placeholder=".\Администратор"
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Пароль администратора</label>
                      <input
                        type="password"
                        value={vaultLocalAdminPassword}
                        onChange={e => setVaultLocalAdminPassword(e.target.value)}
                        placeholder={vaultStatus?.local_admin.is_configured ? '•••••••• (сохранен)' : 'Введите пароль'}
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={savingVaultLocal}
                    className="w-full py-2.5 px-4 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-200 border border-neutral-700 rounded-xl text-xs font-medium transition-colors flex items-center justify-center gap-2 cursor-pointer"
                  >
                    {savingVaultLocal ? 'Сохранение...' : 'Сохранить локального администратора'}
                  </button>
                </form>
              </div>
            </div>

            {/* Security Architecture & Fallback One-Liner Accordion */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 shadow-sm space-y-3">
              <div
                onClick={() => setShowSecurityInfo(prev => !prev)}
                className="flex items-center justify-between cursor-pointer select-none"
              >
                <div className="flex items-center gap-2.5 text-sm font-semibold text-neutral-200">
                  <div className="w-6 h-6 rounded-md bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    </svg>
                  </div>
                  <span>Архитектура безопасности & Fallback One-Liner</span>
                  <span className="text-[10px] font-mono text-neutral-400 bg-neutral-800 px-2 py-0.5 rounded border border-neutral-700">
                    Fernet SSOT + Self-Service
                  </span>
                </div>
                <button
                  type="button"
                  className="text-xs text-neutral-400 hover:text-neutral-200 cursor-pointer"
                >
                  {showSecurityInfo ? 'Свернуть ▲' : 'Развернуть ▼'}
                </button>
              </div>

              {showSecurityInfo && (
                <div className="pt-3 border-t border-neutral-800/80 space-y-3 text-xs text-neutral-300 leading-relaxed">
                  <div className="p-3.5 rounded-xl bg-neutral-950 border border-neutral-800 space-y-1.5">
                    <div className="flex items-center gap-2 text-emerald-400 font-semibold">
                      <span>✓ Шифрование чувствительных данных (Fernet SSOT)</span>
                    </div>
                    <p className="text-neutral-400">
                      Все учетные данные Active Directory, сохраняемые через веб-интерфейс, шифруются симметричным ключом Fernet перед записью в PostgreSQL (<code className="text-emerald-300">system_settings</code>). Пароли никогда не передаются в браузер в открытом виде, а прогреваются в Redis для демонов с ограничением по ключам.
                    </p>
                  </div>

                  <div className="p-3.5 rounded-xl bg-neutral-950 border border-neutral-800 space-y-1.5">
                    <div className="flex items-center gap-2 text-blue-400 font-semibold">
                      <span>✓ Принцип действия Fallback One-Liner (Self-Service)</span>
                    </div>
                    <p className="text-neutral-400">
                      Если брандмауэр Windows на ПК заявителя блокирует входящие порты <code className="text-blue-300">5985</code> (WinRM) и <code className="text-blue-300">135</code> (WMI), агент генерирует одноразовую команду запуска для <kbd className="px-1.5 py-0.5 bg-neutral-800 border border-neutral-700 rounded text-[10px]">Win + R</kbd>:
                    </p>
                    <div className="p-2.5 bg-black/60 rounded-lg font-mono text-[11px] text-emerald-400 border border-neutral-800 overflow-x-auto">
                      powershell -ep bypass -c "irm http://&lt;core-api&gt;:8000/api/v1/run/&lt;token&gt; | iex"
                    </div>
                    <p className="text-neutral-500 text-[11px]">
                      Скрипт локально регистрирует порт и драйвер принтера, рапортует об успехе в Core API по исходящему HTTPS (порт 443 всегда открыт) и завершает инцидент.
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tab Skills: Skills Hub & Action Registry */}
        {activeTab === 'skills' && (
          <SkillsHub token={token || ''} />
        )}

        {/* Tab AI Hub: AI Hub & Inference Monitoring */}
        {activeTab === 'ai-hub' && (
          <div className="space-y-6">
            {/* Header / Refresh Bar */}
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-semibold text-neutral-100 flex items-center gap-2">
                  <span>Централизованный AI Hub & LLM Инференс</span>
                  {loadingAiHealth && <span className="text-xs text-blue-400 animate-pulse">(обновление...)</span>}
                </h2>
                <p className="text-xs text-neutral-400 mt-0.5">
                  Мониторинг локального и облачного контуров, статус GPU NVIDIA RTX 3050 и Zero Trust DLP.
                </p>
              </div>
              <button
                type="button"
                onClick={loadAiHealthData}
                disabled={loadingAiHealth}
                className="px-3 py-1.5 text-xs font-medium text-neutral-200 bg-neutral-800 hover:bg-neutral-700 rounded-lg border border-neutral-700 transition-colors flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
              >
                <span>Обновить телеметрию</span>
              </button>
            </div>

            {/* Grid: Ollama vs LiteLLM */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Ollama Local Card */}
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 space-y-3.5 shadow-sm">
                <div className="flex items-center justify-between border-b border-neutral-800 pb-3">
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-lg bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-rose-400 font-bold text-xs">
                      🔴
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-neutral-100">Локальный инференс Ollama</h3>
                      <span className="text-[11px] text-neutral-400">Закрытый контур On-Prem (RED)</span>
                    </div>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[11px] font-medium border ${
                      aiHealth?.ollama_available
                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                        : 'bg-rose-500/10 text-rose-400 border-rose-500/30'
                    }`}
                  >
                    {aiHealth?.ollama_available ? '🟢 Доступен' : '🔴 Недоступен'}
                  </span>
                </div>

                <div className="space-y-2 text-xs">
                  <div className="flex justify-between py-1 border-b border-neutral-800/60">
                    <span className="text-neutral-400">Адрес сервиса:</span>
                    <span className="font-mono text-neutral-200">{aiHealth?.ollama_url || 'http://localhost:11434'}</span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-neutral-800/60">
                    <span className="text-neutral-400">Активная модель:</span>
                    <span className="font-mono text-blue-400 font-semibold">{aiHealth?.ollama_model || 'qwen2.5:1.5b'}</span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-neutral-800/60">
                    <span className="text-neutral-400">Аппаратное ускорение:</span>
                    <span className="font-medium text-emerald-400">
                      {aiHealth?.gpu_detected
                        ? aiHealth.gpu_name || aiHealth.gpu_backend || 'NVIDIA GeForce RTX 3050 (CUDA)'
                        : 'CPU (Без GPU)'}
                    </span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-neutral-800/60">
                    <span className="text-neutral-400">Видеопамять VRAM:</span>
                    <span className="font-mono text-neutral-300">
                      {aiHealth?.vram_allocated_bytes
                        ? `${(aiHealth.vram_allocated_bytes / (1024 * 1024)).toFixed(0)} МБ в памяти`
                        : 'Динамическое выделение (до 8 ГБ)'}
                    </span>
                  </div>
                  <div className="flex justify-between py-1">
                    <span className="text-neutral-400">Скорость генерации:</span>
                    <span className="font-mono text-neutral-300">~115 токенов/сек</span>
                  </div>
                </div>
              </div>

              {/* LiteLLM Cloud Card */}
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 space-y-3.5 shadow-sm">
                <div className="flex items-center justify-between border-b border-neutral-800 pb-3">
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400 font-bold text-xs">
                      🟡
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-neutral-100">Облачный шлюз LiteLLM</h3>
                      <span className="text-[11px] text-neutral-400">Контуры YELLOW (Sanitized) & GREEN</span>
                    </div>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[11px] font-medium border ${
                      aiHealth?.litellm_available
                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                        : 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                    }`}
                  >
                    {aiHealth?.litellm_available ? '🟢 Прокси активен' : '🟡 Standby / Автономно'}
                  </span>
                </div>

                <div className="space-y-2 text-xs">
                  <div className="flex justify-between py-1 border-b border-neutral-800/60">
                    <span className="text-neutral-400">Прокси адрес:</span>
                    <span className="font-mono text-neutral-200">{aiHealth?.litellm_url || 'http://localhost:4000'}</span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-neutral-800/60">
                    <span className="text-neutral-400">Модели инференса:</span>
                    <span className="font-mono text-purple-400 font-semibold">gemini-2.0-flash / embedding-2</span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-neutral-800/60">
                    <span className="text-neutral-400">Защита данных (DLP):</span>
                    <span className="text-emerald-400 font-medium">Redis PII Vault (Токенизация на лету)</span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-neutral-800/60">
                    <span className="text-neutral-400">Fail-Safe режим:</span>
                    <span className="text-neutral-300">Автоматический fallback на Ollama On-Prem</span>
                  </div>
                  <div className="flex justify-between py-1">
                    <span className="text-neutral-400">Circuit Breaker:</span>
                    <span className="font-mono text-emerald-400">CLOSED (В норме)</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Zero Trust DLP Interactive Playground */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-4">
              <div>
                <h3 className="text-sm font-semibold text-neutral-100 flex items-center gap-2">
                  <span>🛡 Интерактивная проверка Zero Trust DLP (Sanitize Playground)</span>
                </h3>
                <p className="text-xs text-neutral-400 mt-1">
                  Проверьте, как классификатор контуров безопасности и движок токенизации PII обрабатывают чувствительные данные.
                </p>
              </div>

              <form onSubmit={handleTestSanitize} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-neutral-400 mb-1">
                    Исходный текст инцидента или запроса для анализа:
                  </label>
                  <textarea
                    rows={3}
                    value={sanitizeInput}
                    onChange={e => setSanitizeInput(e.target.value)}
                    className="w-full bg-neutral-950 border border-neutral-800 rounded-xl p-3 text-xs text-neutral-100 focus:outline-hidden focus:border-blue-500 font-mono leading-relaxed"
                    placeholder="Введите текст с ФИО, паролями, IP или именами хостов..."
                  />
                </div>

                <button
                  type="submit"
                  disabled={testingSanitize || !sanitizeInput.trim()}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-xs font-semibold cursor-pointer transition-colors flex items-center gap-2"
                >
                  {testingSanitize ? (
                    <>
                      <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                      </svg>
                      <span>Анализ...</span>
                    </>
                  ) : (
                    <span>Проверить контур и маскирование PII</span>
                  )}
                </button>
              </form>

              {/* Test Result Display */}
              {sanitizeResult && (
                <div className="border border-neutral-800 rounded-xl p-4 bg-neutral-950/80 space-y-3 mt-4 text-xs">
                  <div className="flex items-center justify-between border-b border-neutral-800/80 pb-2">
                    <span className="font-semibold text-neutral-300">Результат классификации контура:</span>
                    <span
                      className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold border ${
                        sanitizeResult.route_decision.circuit === 'red'
                          ? 'bg-rose-500/10 text-rose-400 border-rose-500/30'
                          : sanitizeResult.route_decision.circuit === 'yellow'
                          ? 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                          : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                      }`}
                    >
                      {sanitizeResult.route_decision.circuit.toUpperCase()} КОНТУР
                    </span>
                  </div>

                  <div className="text-[11.5px] text-neutral-400">
                    <strong>Причина классификации:</strong> {sanitizeResult.route_decision.reason}
                  </div>

                  <div>
                    <span className="font-semibold text-neutral-300 block mb-1">
                      Обезличенный текст (отправляется в Cloud AI только в этом виде):
                    </span>
                    <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800 font-mono text-[11.5px] text-emerald-400 whitespace-pre-wrap">
                      {sanitizeResult.sanitized_text}
                    </div>
                  </div>

                  {Object.keys(sanitizeResult.entity_map).length > 0 && (
                    <div>
                      <span className="font-semibold text-neutral-300 block mb-1">
                        Таблица подстановок в Redis PII Vault:
                      </span>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 font-mono text-[10.5px]">
                        {Object.entries(sanitizeResult.entity_map).map(([k, v]) => (
                          <div key={k} className="p-1.5 rounded bg-neutral-900/80 border border-neutral-800/80 flex justify-between">
                            <span className="text-amber-400">{k}:</span>
                            <span className="text-neutral-300 truncate max-w-[150px]">{v}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
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

      </div>
    </div>
  );
}
