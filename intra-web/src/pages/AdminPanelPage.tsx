import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  loginAdmin,
  checkCurrentAdminSession,
  fetchAdminSettings,
  testLdapsSettings,
  saveHelpdeskSettings,
  fetchKbStats,
  fetchKbExamples,
  blacklistKbExample,
  purgeKnowledgeBase,
  triggerKbSync,
  triggerStratifiedKbSync,
  fetchKbSyncStatus,
  fetchVaultStatus,
  saveVaultServiceAccount,
  saveVaultDomain,
  saveVaultLocalAdmin,
  testVaultWinrm,
  type HelpdeskConfigDTO,
  type ConnectionTestResult,
  type KBExampleItem,
  type KBStatsResponse,
  type KBSyncProgressResponse,
  fetchAvailableStatuses,
  type KBStatusItem,
  type VaultStatusResponse,
  triggerNightlyAudit,
  fetchNightlyAuditStatus,
  type KBNightlyAuditProgress,
} from '../lib/adminApi';
import SkillsHub from '../components/SkillsHub';
import { IconShield } from '../components/Icons';
import { fetchAIHealth, fetchSanitizePreview, purgeTriageCache } from '../lib/tasks';
import type { AIHealthData, SanitizePreviewResult } from '../lib/types';

interface AdminPanelPageProps {
  theme?: 'light' | 'dark';
}

function getPaginationPages(currentPage: number, totalPages: number): (number | string)[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  if (currentPage <= 4) {
    return [1, 2, 3, 4, 5, '...', totalPages];
  }
  if (currentPage >= totalPages - 3) {
    return [1, '...', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
  }
  return [1, '...', currentPage - 1, currentPage, currentPage + 1, '...', totalPages];
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
  const [purgingTriageCache, setPurgingTriageCache] = useState(false);

  const handlePurgeTriageCache = async () => {
    setPurgingTriageCache(true);
    setStatusMessage(null);
    try {
      const res = await purgeTriageCache();
      setStatusMessage({
        type: 'success',
        text: `${res.message || 'Кэш вердиктов успешно очищен'} (удалено ключей: ${res.deleted_verdicts})`,
      });
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка сброса кэша вердиктов' });
    } finally {
      setPurgingTriageCache(false);
    }
  };

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

  // Knowledge Base State
  const [kbStats, setKbStats] = useState<KBStatsResponse | null>(null);
  const [kbExamples, setKbExamples] = useState<KBExampleItem[]>([]);
  const [kbTotal, setKbTotal] = useState<number>(0);
  const [kbPage, setKbPage] = useState<number>(1);
  const [kbLimit, setKbLimit] = useState<number>(10);
  const [kbSearch, setKbSearch] = useState<string>('');
  const [kbSearchInput, setKbSearchInput] = useState<string>('');
  const [kbSelectedRootFilter, setKbSelectedRootFilter] = useState<string | null>(null);
  const [expandedTasks, setExpandedTasks] = useState<Record<number, boolean>>({});
  const [copiedTaskId, setCopiedTaskId] = useState<number | null>(null);
  const kbTableRef = useRef<HTMLDivElement>(null);
  const [kbLoading, setKbLoading] = useState<boolean>(false);
  const [kbSyncLoading, setKbSyncLoading] = useState<boolean>(false);
  const [kbSyncDays, setKbSyncDays] = useState<number>(60);
  const [kbSyncQuota, setKbSyncQuota] = useState<number>(30);
  const [kbSyncRootId, setKbSyncRootId] = useState<string>('');
  const [availableStatuses, setAvailableStatuses] = useState<KBStatusItem[]>([]);
  const [selectedStatusIds, setSelectedStatusIds] = useState<number[]>([28, 29, 43, 30]);
  const [aiQualityEval, setAiQualityEval] = useState<boolean>(true);
  const [nightlyAuditLoading, setNightlyAuditLoading] = useState<boolean>(false);
  const [nightlyAuditProgress, setNightlyAuditProgress] = useState<KBNightlyAuditProgress | null>(null);
  const [kbSyncProgress, setKbSyncProgress] = useState<KBSyncProgressResponse | null>(null);
  const [showSyncConsole, setShowSyncConsole] = useState<boolean>(true);
  const syncConsoleEndRef = useRef<HTMLDivElement | null>(null);
  const [blacklistingTaskId, setBlacklistingTaskId] = useState<number | null>(null);
  const [isPurgeModalOpen, setIsPurgeModalOpen] = useState<boolean>(false);
  const [purgeConfirmed, setPurgeConfirmed] = useState<boolean>(false);
  const [purgingKb, setPurgingKb] = useState<boolean>(false);

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

  // KB Handlers
  const loadKbData = useCallback(
    async (authToken: string, page = 1, search = '', rootId: string | null = null, limit = 10) => {
      setKbLoading(true);
      try {
        const [stats, examplesData] = await Promise.all([
          fetchKbStats(authToken).catch(() => null),
          fetchKbExamples(authToken, page, limit, undefined, search, rootId),
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
    []
  );

  // Мгновенный дебаунс-поиск 300мс
  useEffect(() => {
    const handler = setTimeout(() => {
      setKbSearch(kbSearchInput.trim());
      setKbPage(1);
    }, 300);
    return () => clearTimeout(handler);
  }, [kbSearchInput]);

  useEffect(() => {
    if (token && activeTab === 'kb') {
      loadKbData(token, kbPage, kbSearch, kbSelectedRootFilter, kbLimit);
    }
  }, [token, activeTab, kbPage, kbSearch, kbSelectedRootFilter, kbLimit, loadKbData]);

  const handleCopySolution = async (taskId: number, solution: string) => {
    try {
      await navigator.clipboard.writeText(solution);
      setCopiedTaskId(taskId);
      setTimeout(() => setCopiedTaskId(null), 2000);
    } catch {
      // ignore
    }
  };

  const handleToggleExpand = (taskId: number) => {
    setExpandedTasks(prev => ({ ...prev, [taskId]: !prev[taskId] }));
  };

  const handleResetFilters = () => {
    setKbSearchInput('');
    setKbSearch('');
    setKbSelectedRootFilter(null);
    setKbPage(1);
  };

  const handlePageChange = (newPage: number) => {
    setKbPage(newPage);
    kbTableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleBlacklistExample = async (taskId: number) => {
    if (!token) return;
    if (
      !window.confirm(
        `Вы уверены, что хотите скрыть прецедент #${taskId} из базы знаний? Это решение перестанет предлагаться AI-ассистентом.`
      )
    ) {
      return;
    }
    setBlacklistingTaskId(taskId);
    try {
      await blacklistKbExample(token, taskId);
      setStatusMessage({ type: 'success', text: `Прецедент #${taskId} успешно скрыт из базы знаний RAG.` });
      await loadKbData(token, kbPage, kbSearch);
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка скрытия из базы знаний' });
    } finally {
      setBlacklistingTaskId(null);
    }
  };

  const handlePurgeKnowledgeBase = async () => {
    if (!token || !purgeConfirmed) return;
    setPurgingKb(true);
    setStatusMessage(null);
    try {
      const res = await purgeKnowledgeBase(token);
      setStatusMessage({ type: 'success', text: res.message || 'База знаний RAG успешно очищена' });
      setIsPurgeModalOpen(false);
      setPurgeConfirmed(false);
      await loadKbData(token, 1, '');
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка очистки базы знаний' });
    } finally {
      setPurgingKb(false);
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

  const handleTriggerStratifiedSync = async () => {
    if (!token) return;
    setKbSyncLoading(true);
    setStatusMessage(null);
    try {
      const res = await triggerStratifiedKbSync(token, {
        quota_per_service: kbSyncQuota,
        days: kbSyncDays,
        root_id: kbSyncRootId || null,
        status_ids: selectedStatusIds,
        ai_eval: aiQualityEval,
      });
      setStatusMessage({ type: 'success', text: res.message || 'Умная синхронизация запущена в фоне' });
      const statusData = await fetchKbSyncStatus(token);
      setKbSyncProgress(statusData);
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка запуска умной синхронизации' });
      setKbSyncLoading(false);
    }
  };

  const handleTriggerNightlyAudit = async () => {
    if (!token) return;
    setNightlyAuditLoading(true);
    setStatusMessage(null);
    try {
      const res = await triggerNightlyAudit(token);
      setStatusMessage({ type: 'success', text: res.message || 'Глубокий ночной аудит запущен' });
      const statusData = await fetchNightlyAuditStatus(token);
      setNightlyAuditProgress(statusData);
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: err.message || 'Ошибка запуска ночного аудита' });
      setNightlyAuditLoading(false);
    }
  };

  // Загрузка доступных статусов IntraService при открытии вкладки базы знаний
  useEffect(() => {
    if (token && activeTab === 'kb') {
      fetchAvailableStatuses(token).then(st => {
        if (st && st.length > 0) {
          setAvailableStatuses(st);
        }
      });
    }
  }, [token, activeTab]);

  // Фоновый опрос прогресса умной синхронизации (Redis polling)
  useEffect(() => {
    let timer: any = null;
    let isMounted = true;
    if (token && activeTab === 'kb') {
      const checkProgress = async () => {
        try {
          const prog = await fetchKbSyncStatus(token);
          if (!isMounted) return;
          setKbSyncProgress(prog);
          if (prog.is_running) {
            setKbSyncLoading(true);
            timer = setTimeout(checkProgress, 2000);
          } else {
            setKbSyncLoading(false);
          }
        } catch {
          // мягкий fallback
        }
      };
      checkProgress();
    }
    return () => {
      isMounted = false;
      if (timer) clearTimeout(timer);
    };
  }, [token, activeTab]);

  // Auto-scroll sync console to bottom when new logs arrive
  useEffect(() => {
    if (showSyncConsole && kbSyncProgress?.logs?.length) {
      syncConsoleEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [kbSyncProgress?.logs?.length, showSyncConsole]);

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
    window.history.pushState({}, '', '/operator-panel');
    window.dispatchEvent(new PopStateEvent('popstate'));
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
                  <span className="inline-flex items-center gap-1.5 font-mono">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${vaultStatus?.service_account.redis_synced ? 'bg-emerald-400' : 'bg-amber-400'}`} />
                    <span className={vaultStatus?.service_account.redis_synced ? 'text-emerald-400' : 'text-amber-400'}>
                      {vaultStatus?.service_account.redis_synced ? 'Прогрет' : 'Ожидает'}
                    </span>
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
                  <span className="inline-flex items-center gap-1.5 font-mono">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${vaultStatus?.domain.redis_synced ? 'bg-emerald-400' : 'bg-amber-400'}`} />
                    <span className={vaultStatus?.domain.redis_synced ? 'text-emerald-400' : 'text-amber-400'}>
                      {vaultStatus?.domain.redis_synced ? 'Прогрет' : 'Ожидает'}
                    </span>
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
                  <div className="text-sm font-semibold truncate flex items-center gap-1.5">
                    <span className={`w-2 h-2 rounded-full shrink-0 ${vaultStatus?.execution_worker.online ? 'bg-emerald-400 animate-pulse' : 'bg-neutral-500'}`} />
                    <span>{vaultStatus?.execution_worker.online ? 'Онлайн' : 'Ожидание воркера'}</span>
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

              {/* Form 2: Domain Credentials (AD & Wi-Fi) */}
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
                      <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
                    </svg>
                    <span>Доменная служба Active Directory и доступ к Wi-Fi</span>
                  </h3>
                  <span className="text-[11px] font-mono text-neutral-400">worker:domain_auth</span>
                </div>

                <form onSubmit={handleSaveVaultDomain} className="space-y-4">
                  <div className="text-xs font-semibold text-neutral-400 border-b border-neutral-800 pb-1">
                    Сервисная учетная запись (Active Directory / WinRM)
                  </div>
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

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Домен</label>
                      <input
                        type="text"
                        value={vaultDomainName}
                        onChange={e => setVaultDomainName(e.target.value)}
                        placeholder="corporate.loc"
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Контроллер домена (DC)</label>
                      <input
                        type="text"
                        value={vaultDomainDcHost}
                        onChange={e => setVaultDomainDcHost(e.target.value)}
                        placeholder="dc01.corporate.loc"
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                      />
                    </div>
                  </div>

                  <div className="text-xs font-semibold text-neutral-400 border-b border-neutral-800 pb-1 pt-2">
                    Параметры каталога LDAPS и группа Wi-Fi
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div className="sm:col-span-1">
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Порт LDAPS</label>
                      <input
                        type="number"
                        value={vaultDomainPort}
                        onChange={e => setVaultDomainPort(Number(e.target.value))}
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                      />
                    </div>

                    <div className="sm:col-span-2">
                      <label className="block text-xs font-medium text-neutral-400 mb-1">Базовый DN каталога (Base DN)</label>
                      <input
                        type="text"
                        value={vaultDomainBaseDn}
                        onChange={e => setVaultDomainBaseDn(e.target.value)}
                        placeholder="DC=corporate,DC=loc"
                        className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono text-xs"
                      />
                    </div>
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

            {/* Fallback Local Admin Row */}
            <div className="grid grid-cols-1 gap-6">

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
                  {showSecurityInfo ? 'Свернуть' : 'Развернуть'}
                </button>
              </div>

              {showSecurityInfo && (
                <div className="pt-3 border-t border-neutral-800/80 space-y-3 text-xs text-neutral-300 leading-relaxed">
                  <div className="p-3.5 rounded-xl bg-neutral-950 border border-neutral-800 space-y-1.5">
                    <div className="flex items-center gap-2 text-emerald-400 font-semibold">
                      <span>Шифрование чувствительных данных (Fernet SSOT)</span>
                    </div>
                    <p className="text-neutral-400">
                      Все учетные данные Active Directory, сохраняемые через веб-интерфейс, шифруются симметричным ключом Fernet перед записью в PostgreSQL (<code className="text-emerald-300">system_settings</code>). Пароли никогда не передаются в браузер в открытом виде, а прогреваются в Redis для демонов с ограничением по ключам.
                    </p>
                  </div>

                  <div className="p-3.5 rounded-xl bg-neutral-950 border border-neutral-800 space-y-1.5">
                    <div className="flex items-center gap-2 text-blue-400 font-semibold">
                      <span>Принцип действия Fallback One-Liner (Self-Service)</span>
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
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-lg bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-rose-400 font-bold text-xs">
                      <span className="w-2.5 h-2.5 rounded-full bg-rose-500 animate-pulse" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-neutral-100">Локальный инференс Ollama</h3>
                      <span className="text-[11px] text-neutral-400">Закрытый контур On-Prem (RED)</span>
                    </div>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[11px] font-medium border inline-flex items-center gap-1.5 ${
                      aiHealth?.ollama_available
                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                        : 'bg-rose-500/10 text-rose-400 border-rose-500/30'
                    }`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${aiHealth?.ollama_available ? 'bg-emerald-400' : 'bg-rose-400'}`} />
                    <span>{aiHealth?.ollama_available ? 'Доступен' : 'Недоступен'}</span>
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
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400 font-bold text-xs">
                      <span className="w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-neutral-100">Облачный шлюз LiteLLM</h3>
                      <span className="text-[11px] text-neutral-400">Контуры YELLOW (Sanitized) & GREEN</span>
                    </div>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[11px] font-medium border inline-flex items-center gap-1.5 ${
                      aiHealth?.litellm_available
                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                        : 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                    }`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${aiHealth?.litellm_available ? 'bg-emerald-400' : 'bg-amber-400'}`} />
                    <span>{aiHealth?.litellm_available ? 'Прокси активен' : 'Standby / Автономно'}</span>
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
                  <IconShield size={16} className="text-neutral-400 shrink-0" />
                  <span>Интерактивная проверка Zero Trust DLP (Sanitize Playground)</span>
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

            {/* Карточка глобального сброса кэша вердиктов (marks.md #7) */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-3 mt-6">
              <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-neutral-100 flex items-center gap-2">
                    <span>Сброс кэша вердиктов AI и Rule Engine</span>
                  </h3>
                  <p className="text-xs text-neutral-400 mt-1 max-w-2xl">
                    Очищает все сохраненные вердикты и AI-резолюции в Redis (<code className="text-amber-400 font-mono">ai:resolution:*</code>) и кэш каталога услуг. Используйте после изменения логики правил, шаблонов или кода движка для принудительного пересчета очереди.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handlePurgeTriageCache}
                  disabled={purgingTriageCache}
                  className="px-4 py-2 bg-amber-600/20 hover:bg-amber-600/30 text-amber-400 border border-amber-500/40 rounded-lg text-xs font-semibold cursor-pointer transition-colors flex items-center gap-2 disabled:opacity-50"
                >
                  <svg className={`w-3.5 h-3.5 ${purgingTriageCache ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
                  </svg>
                  <span>{purgingTriageCache ? 'Очистка кэша...' : 'Сбросить кэш вердиктов AI/Rules'}</span>
                </button>
              </div>
            </div>
          </div>
        )}


        {/* Tab 2: Helpdesk Parameters */}
        {activeTab === 'helpdesk' && (
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-6">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <h2 className="text-base font-semibold">Параметры Helpdesk и Очереди</h2>
                <p className="text-xs text-neutral-400 mt-0.5">
                  Привязка исполнителей по умолчанию для списания трудозатрат и фильтра первой линии IntraService.
                </p>
              </div>

              <button
                type="button"
                onClick={() => setHelpdeskConfig({
                  primary_executor_id: 8664,
                  default_executor_ids: '8664,10502',
                  primary_filter_id: 984,
                  timezone: 'Europe/Moscow',
                })}
                className="px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded-lg text-xs font-medium border border-neutral-700 transition-colors cursor-pointer"
              >
                Восстановить рекомендуемые параметры (Беликов Ален / 984)
              </button>
            </div>

            <div className="p-3.5 rounded-xl bg-blue-950/20 border border-blue-800/40 text-xs text-neutral-300 space-y-1.5">
              <div className="font-semibold text-blue-300 flex items-center gap-1.5">
                <span>Текущие системные привязки</span>
              </div>
              <p className="text-neutral-400 leading-relaxed text-[11.5px]">
                Значения берутся из переменных окружения сервиса (<code className="text-blue-400 font-mono">INTRASERVICE_FILTER_ID</code>, <code className="text-blue-400 font-mono">DEFAULT_EXECUTOR_IDS</code>). При необходимости вы можете переопределить их здесь.
              </p>
            </div>

            <form onSubmit={handleSaveHelpdesk} className="space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-neutral-300 mb-1 flex items-center justify-between">
                    <span>ID основного инженера</span>
                    <span className="text-[10.5px] font-normal text-neutral-400">Беликов Ален: 8664</span>
                  </label>
                  <input
                    type="number"
                    value={helpdeskConfig.primary_executor_id}
                    onChange={e => setHelpdeskConfig({ ...helpdeskConfig, primary_executor_id: Number(e.target.value) })}
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono"
                  />
                  <span className="text-[11px] text-neutral-500 mt-1 block">Исполнитель, на которого по умолчанию списываются трудозатраты 1-й линии</span>
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-300 mb-1 flex items-center justify-between">
                    <span>Список исполнителей очереди</span>
                    <span className="text-[10.5px] font-normal text-neutral-400">8664, 10502</span>
                  </label>
                  <input
                    type="text"
                    value={helpdeskConfig.default_executor_ids}
                    onChange={e => setHelpdeskConfig({ ...helpdeskConfig, default_executor_ids: e.target.value })}
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono"
                  />
                  <span className="text-[11px] text-neutral-500 mt-1 block">ID инженеров через запятую: основной инженер и ассистенты</span>
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-300 mb-1 flex items-center justify-between">
                    <span>ID фильтра очереди IntraService</span>
                    <span className="text-[10.5px] font-normal text-neutral-400">Очередь 1-й линии: 984</span>
                  </label>
                  <input
                    type="number"
                    value={helpdeskConfig.primary_filter_id}
                    onChange={e => setHelpdeskConfig({ ...helpdeskConfig, primary_filter_id: Number(e.target.value) })}
                    required
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 font-mono"
                  />
                  <span className="text-[11px] text-neutral-500 mt-1 block">ID фильтра входящих заявок первой линии в веб-версии IntraService</span>
                </div>

                <div>
                  <label className="block text-xs font-medium text-neutral-300 mb-1">Часовой пояс системы (Timezone)</label>
                  <select
                    value={helpdeskConfig.timezone}
                    onChange={e => setHelpdeskConfig({ ...helpdeskConfig, timezone: e.target.value })}
                    className="w-full px-3.5 py-2 bg-neutral-950 border border-neutral-700 rounded-xl text-sm focus:outline-none focus:border-blue-500"
                  >
                    <option value="Europe/Moscow">Europe/Moscow (МСК, UTC+3)</option>
                    <option value="Asia/Yekaterinburg">Asia/Yekaterinburg (ЕКБ, UTC+5)</option>
                    <option value="Asia/Novosibirsk">Asia/Novosibirsk (НСК, UTC+7)</option>
                    <option value="UTC">UTC (Всемирное координированное время)</option>
                  </select>
                  <span className="text-[11px] text-neutral-500 mt-1 block">Часовой пояс для сопоставления дат обновлений и истории заявок</span>
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

            {/* Warning banner if IntraService credentials are not ready */}
            {kbStats?.sync_readiness && !kbStats.sync_readiness.ready && (
              <div className="p-4 rounded-2xl bg-amber-950/20 border border-amber-800/40 text-xs text-amber-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs">
                <div className="flex items-start gap-3">
                  <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-mono text-[10px] font-bold uppercase tracking-wider shrink-0 mt-0.5 border border-amber-500/30">
                    ТРЕБУЕТСЯ АВТОРИЗАЦИЯ
                  </span>
                  <div>
                    <p className="font-semibold text-amber-100">
                      Синхронизация с IntraService недоступна
                    </p>
                    <p className="text-amber-300/80 text-[11.5px] mt-0.5 leading-relaxed">
                      {kbStats.sync_readiness.message}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setActiveTab('vault')}
                  className="px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 border border-amber-500/40 rounded-xl text-xs font-medium cursor-pointer transition-colors shrink-0 text-center"
                >
                  Настроить в Хранилище &rarr;
                </button>
              </div>
            )}

            {/* Warning banner if Embedding service is not ready */}
            {kbStats?.embedding_readiness && !kbStats.embedding_readiness.ready && (
              <div className="p-4 rounded-2xl bg-rose-950/20 border border-rose-800/40 text-xs text-rose-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs">
                <div className="flex items-start gap-3">
                  <span className="px-2 py-0.5 rounded bg-rose-500/20 text-rose-300 font-mono text-[10px] font-bold uppercase tracking-wider shrink-0 mt-0.5 border border-rose-500/30">
                    СБОЙ AI HUB
                  </span>
                  <div>
                    <p className="font-semibold text-rose-100">
                      Служба генерации эмбеддингов недоступна
                    </p>
                    <p className="text-rose-300/80 text-[11.5px] mt-0.5 leading-relaxed font-mono">
                      {kbStats.embedding_readiness.message}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => token && loadKbData(token, 1, '')}
                  className="px-3 py-1.5 bg-rose-500/20 hover:bg-rose-500/30 text-rose-200 border border-rose-500/40 rounded-xl text-xs font-medium cursor-pointer transition-colors shrink-0 text-center"
                >
                  Проверить связь
                </button>
              </div>
            )}

            {/* Sync & Search Control Panel */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-sm space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-base font-semibold">Синхронизация и Модерация</h2>
                    {kbStats?.sync_readiness?.ready && (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px] font-mono">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                        <span>{kbStats.sync_readiness.auth_source === 'operator_session' ? `Сессия: ${kbStats.sync_readiness.account_name}` : 'Сервисный аккаунт'}</span>
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    Управление векторными знаниями pgvector: обучение по закрытым заявкам и удаление ошибок.
                  </p>
                </div>

                {/* Мультистатусный отбор и AI-валидация качества */}
                <div className="p-3 bg-neutral-900/60 border border-neutral-800 rounded-xl space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-medium text-neutral-300 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-blue-400"></span>
                      Статусы для выборки прецедентов:
                    </span>
                    <label className="flex items-center gap-2 cursor-pointer text-xs text-neutral-300 select-none hover:text-white transition-colors">
                      <input
                        type="checkbox"
                        checked={aiQualityEval}
                        onChange={e => setAiQualityEval(e.target.checked)}
                        className="rounded border-neutral-700 text-blue-600 focus:ring-blue-500 focus:ring-offset-0 bg-neutral-950 w-3.5 h-3.5 cursor-pointer"
                      />
                      <span className="flex items-center gap-1">
                        <span>🤖 AI-валидация качества решений</span>
                        <span className="text-[10px] px-1.5 py-0.2 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded font-mono">Qwen 2.5</span>
                      </span>
                    </label>
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    {(availableStatuses.length > 0 ? availableStatuses : [
                      { id: 28, name: 'Закрыта', is_recommended: true },
                      { id: 29, name: 'Выполнена', is_recommended: true },
                      { id: 43, name: 'Обработано 1-й линией', is_recommended: true },
                      { id: 30, name: 'Отменена', is_recommended: true },
                      { id: 31, name: 'Открыта', is_recommended: false },
                      { id: 27, name: 'В работе', is_recommended: false },
                      { id: 35, name: 'Требует уточнения', is_recommended: false },
                    ]).map(s => {
                      const isSelected = selectedStatusIds.includes(s.id);
                      return (
                        <button
                          key={s.id}
                          type="button"
                          onClick={() => {
                            setSelectedStatusIds(prev =>
                              isSelected
                                ? (prev.length > 1 ? prev.filter(id => id !== s.id) : prev)
                                : [...prev, s.id]
                            );
                          }}
                          className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-all flex items-center gap-1.5 cursor-pointer ${
                            isSelected
                              ? 'bg-blue-600/20 text-blue-300 border-blue-500/60 shadow-sm'
                              : 'bg-neutral-950 text-neutral-400 border-neutral-800 hover:border-neutral-700 hover:text-neutral-300'
                          }`}
                        >
                          <span className={`w-1.5 h-1.5 rounded-full ${isSelected ? 'bg-blue-400' : 'bg-neutral-600'}`}></span>
                          <span>{s.name}</span>
                          <span className="text-[10px] opacity-60 font-mono">#{s.id}</span>
                          {s.is_recommended && (
                            <span className="text-[9px] px-1 py-0.2 bg-blue-500/10 text-blue-400 rounded">rec</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-neutral-400">Раздел:</span>
                    <select
                      value={kbSyncRootId}
                      onChange={e => setKbSyncRootId(e.target.value)}
                      className="px-2.5 py-1.5 bg-neutral-950 border border-neutral-700 rounded-lg text-xs text-neutral-200 focus:outline-none focus:border-blue-500 cursor-pointer max-w-[200px] truncate"
                    >
                      <option value="">Все разделы (01–17)</option>
                      {kbStats?.root_services?.map(r => (
                        <option key={r.root_id} value={r.root_id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="flex items-center gap-2">
                    <span className="text-xs text-neutral-400">Квота:</span>
                    <input
                      type="number"
                      min={5}
                      max={100}
                      value={kbSyncQuota}
                      onChange={e => setKbSyncQuota(Math.max(5, Math.min(100, Number(e.target.value) || 30)))}
                      className="w-14 px-2 py-1.5 bg-neutral-950 border border-neutral-700 rounded-lg text-xs text-neutral-200 text-center focus:outline-none focus:border-blue-500"
                      title="Количество качественных прецедентов на каждый раздел"
                    />
                  </div>

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
                    onClick={handleTriggerStratifiedSync}
                    disabled={
                      kbSyncLoading ||
                      (kbStats?.sync_readiness ? !kbStats.sync_readiness.ready : false) ||
                      (kbStats?.embedding_readiness ? !kbStats.embedding_readiness.ready : false)
                    }
                    title={
                      kbStats?.embedding_readiness && !kbStats.embedding_readiness.ready
                        ? `Сбой AI Hub: ${kbStats.embedding_readiness.message}`
                        : kbStats?.sync_readiness && !kbStats.sync_readiness.ready
                        ? kbStats.sync_readiness.message
                        : 'Запустить умное квотирование по разделам каталога с дедупликацией'
                    }
                    className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg text-xs font-medium shadow-sm transition-colors flex items-center gap-1.5 cursor-pointer"
                  >
                    {kbSyncLoading ? (
                      <>
                        <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
                        <span>Синхронизация...</span>
                      </>
                    ) : (
                      <>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path>
                        </svg>
                        <span>Умное наполнение</span>
                      </>
                    )}
                  </button>

                  <button
                    type="button"
                    onClick={handleTriggerNightlyAudit}
                    disabled={
                      nightlyAuditLoading ||
                      nightlyAuditProgress?.is_running ||
                      (kbStats?.sync_readiness ? !kbStats.sync_readiness.ready : false)
                    }
                    title="Запустить глубокий аудит базы знаний через локальную модель Qwen 2.5 без эвристических срезок (расписание: 19:00)"
                    className="px-3 py-1.5 bg-neutral-900 hover:bg-neutral-800 border border-neutral-700 hover:border-neutral-600 disabled:opacity-40 disabled:cursor-not-allowed text-neutral-200 hover:text-white rounded-lg text-xs font-medium shadow-sm transition-colors flex items-center gap-1.5 cursor-pointer"
                  >
                    {nightlyAuditLoading || nightlyAuditProgress?.is_running ? (
                      <>
                        <span className="w-3.5 h-3.5 border-2 border-indigo-400/30 border-t-indigo-400 rounded-full animate-spin"></span>
                        <span>Аудит...</span>
                      </>
                    ) : (
                      <>
                        <span>🌙</span>
                        <span>Глубокий аудит (19:00)</span>
                      </>
                    )}
                  </button>
                </div>
              </div>

              {/* Live Nightly Deep Audit Progress Card */}
              {nightlyAuditProgress && (nightlyAuditProgress.is_running || (nightlyAuditProgress.total_audited > 0 && nightlyAuditProgress.percent < 100) || (nightlyAuditProgress.logs && nightlyAuditProgress.logs.length > 0)) && (
                <div className={`p-4 rounded-xl bg-neutral-950 border space-y-2.5 shadow-inner ${
                  nightlyAuditProgress.error ? 'border-rose-800/60' : 'border-indigo-900/50'
                }`}>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${nightlyAuditProgress.is_running ? 'bg-indigo-500 animate-ping' : 'bg-emerald-500'}`}></span>
                      <span className="font-semibold text-indigo-300 flex items-center gap-1.5">
                        <span>🌙</span>
                        <span>{nightlyAuditProgress.is_running ? 'Выполняется глубокий ночной аудит (Qwen 2.5)...' : 'Глубокий ночной аудит завершен'}</span>
                      </span>
                      <span className="text-[10px] px-1.5 py-0.2 bg-neutral-900 text-neutral-400 rounded border border-neutral-800">
                        Ежедневно в 19:00
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-neutral-400 font-mono text-[11px]">
                      <span>Проверено: <b className="text-neutral-200">{nightlyAuditProgress.total_audited}/{nightlyAuditProgress.total_records}</b></span>
                      <span>Подтверждено: <b className="text-emerald-400">+{nightlyAuditProgress.high_quality_count}</b></span>
                      <span>В Blacklist: <b className="text-rose-400">+{nightlyAuditProgress.blacklisted_count}</b></span>
                      <span className="font-bold text-indigo-400">{nightlyAuditProgress.percent}%</span>
                    </div>
                  </div>

                  <div className="w-full bg-neutral-900 rounded-full h-1.5 overflow-hidden">
                    <div
                      className="h-1.5 bg-gradient-to-r from-indigo-600 via-purple-500 to-indigo-400 transition-all duration-300 rounded-full"
                      style={{ width: `${Math.max(2, nightlyAuditProgress.percent)}%` }}
                    ></div>
                  </div>

                  {nightlyAuditProgress.logs && nightlyAuditProgress.logs.length > 0 && (
                    <div className="max-h-24 overflow-y-auto font-mono text-[11px] p-2 bg-black/50 border border-neutral-900 rounded-lg space-y-1">
                      {nightlyAuditProgress.logs.slice(-6).map((l, i) => (
                        <div key={i} className="flex items-start gap-2">
                          <span className="text-neutral-500 shrink-0">{l.time}</span>
                          <span className={
                            l.level === 'warn' ? 'text-amber-400' :
                            l.level === 'error' ? 'text-rose-400' :
                            l.level === 'success' ? 'text-emerald-400' :
                            'text-neutral-300'
                          }>
                            {l.message}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Live Background Sync Progress Card */}
              {kbSyncProgress && (kbSyncProgress.is_running || (kbSyncProgress.percent > 0 && kbSyncProgress.percent < 100) || kbSyncProgress.error || (kbSyncProgress.logs && kbSyncProgress.logs.length > 0)) && (
                <div className={`p-4 rounded-xl bg-neutral-950 border space-y-2.5 shadow-inner ${
                  kbSyncProgress.error ? 'border-rose-800/60' : 'border-blue-900/50'
                }`}>
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      {kbSyncProgress.error ? (
                        <>
                          <span className="w-2 h-2 rounded-full bg-rose-500"></span>
                          <span className="font-semibold text-rose-300">
                            Синхронизация остановлена (сбой)
                          </span>
                        </>
                      ) : (
                        <>
                          <span className="w-2 h-2 rounded-full bg-blue-500 animate-ping"></span>
                          <span className="font-semibold text-blue-300">
                            {kbSyncProgress.current_service_name
                              ? `Обработка: ${kbSyncProgress.current_service_name}`
                              : 'Подготовка разделов каталога...'}
                          </span>
                        </>
                      )}
                    </div>
                    <span className="font-mono text-neutral-400">
                      {kbSyncProgress.percent}% ({kbSyncProgress.processed_roots}/{kbSyncProgress.total_roots} разделов)
                    </span>
                  </div>

                  {/* Progress bar */}
                  <div className="w-full bg-neutral-800 rounded-full h-1.5 overflow-hidden">
                    <div
                      className={`h-1.5 rounded-full transition-all duration-300 ease-out ${
                        kbSyncProgress.error ? 'bg-rose-500' : 'bg-blue-500'
                      }`}
                      style={{ width: `${kbSyncProgress.percent}%` }}
                    ></div>
                  </div>

                  {/* Prominent Circuit Breaker / Failure Alert Box */}
                  {kbSyncProgress.error && (
                    <div className="p-3 rounded-lg bg-rose-950/40 border border-rose-800/50 text-[11.5px] font-mono text-rose-200 space-y-1">
                      <div className="font-semibold flex items-center gap-1.5 text-rose-300">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <circle cx="12" cy="12" r="10"></circle>
                          <line x1="12" y1="8" x2="12" y2="12"></line>
                          <line x1="12" y1="16" x2="12.01" y2="16"></line>
                        </svg>
                        <span>Причина остановки процесса:</span>
                      </div>
                      <p className="leading-relaxed break-words">{kbSyncProgress.error}</p>
                    </div>
                  )}

                  <div className="flex flex-wrap items-center justify-between text-[11px] text-neutral-400 pt-0.5">
                    <div className="flex items-center gap-3">
                      <span>
                        Добавлено: <strong className="text-emerald-400 font-mono">+{kbSyncProgress.total_indexed}</strong>
                      </span>
                      <span>
                        Пропущено (отписки): <strong className="text-neutral-300 font-mono">{kbSyncProgress.total_skipped}</strong>
                      </span>
                      <span>
                        Отсеяно дублей: <strong className="text-amber-400 font-mono">{kbSyncProgress.total_duplicates}</strong>
                      </span>
                      {(kbSyncProgress.total_ai_errors ?? 0) > 0 && (
                        <span>
                          Сбоев AI Hub: <strong className="text-rose-400 font-mono">{kbSyncProgress.total_ai_errors}</strong>
                        </span>
                      )}
                    </div>
                    <span className="text-neutral-500 font-mono text-[10px]">
                      Лимит: {kbSyncQuota} на раздел
                    </span>
                  </div>

                  {/* Live Console Terminal */}
                  {kbSyncProgress.logs && kbSyncProgress.logs.length > 0 && (
                    <div className="mt-2.5 border-t border-neutral-800/80 pt-2.5">
                      <div className="flex items-center justify-between mb-1.5">
                        <button
                          type="button"
                          onClick={() => setShowSyncConsole(!showSyncConsole)}
                          className="flex items-center gap-1.5 text-neutral-400 hover:text-neutral-200 text-xs font-mono transition-colors cursor-pointer"
                        >
                          <span className="text-[10px] text-neutral-500">&gt;_</span>
                          <span className="font-semibold text-[11px]">Терминал выполнения RAG</span>
                          <span className="px-1.5 py-0.2 rounded bg-neutral-800 text-[9.5px] text-neutral-300 font-mono">
                            {kbSyncProgress.logs.length}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => setShowSyncConsole(!showSyncConsole)}
                          className="text-[10px] text-neutral-500 hover:text-neutral-400 font-mono cursor-pointer"
                        >
                          {showSyncConsole ? 'свернуть ▲' : 'развернуть ▼'}
                        </button>
                      </div>

                      {showSyncConsole && (
                        <div className="p-3 rounded-xl bg-black/95 border border-neutral-800/90 font-mono text-[11px] leading-relaxed max-h-48 overflow-y-auto scrollbar-thin scrollbar-thumb-neutral-800 space-y-1 shadow-inner">
                          {kbSyncProgress.logs.map((l, lIdx) => (
                            <div key={lIdx} className="flex items-start gap-2">
                              <span className="text-neutral-600 shrink-0 select-none text-[10px]">[{l.time}]</span>
                              <span className={
                                l.level === 'error' ? 'text-rose-400 font-semibold' :
                                l.level === 'warn' ? 'text-amber-400' :
                                l.level === 'success' ? 'text-emerald-400 font-medium' :
                                'text-neutral-300'
                              }>
                                {l.message}
                              </span>
                            </div>
                          ))}
                          <div ref={syncConsoleEndRef} />
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Service Category Chips Filter (01..17) */}
              <div className="space-y-2 pt-1">
                <div className="flex items-center gap-1.5 overflow-x-auto pb-1.5 scrollbar-thin scrollbar-thumb-neutral-800">
                  <button
                    type="button"
                    onClick={() => {
                      setKbSelectedRootFilter(null);
                      setKbPage(1);
                    }}
                    className={`px-3 py-1.5 rounded-xl text-xs font-medium shrink-0 transition-all cursor-pointer flex items-center gap-1.5 ${
                      kbSelectedRootFilter === null
                        ? 'bg-blue-600 text-white shadow-sm'
                        : 'bg-neutral-950 hover:bg-neutral-800 text-neutral-400 hover:text-neutral-200 border border-neutral-800'
                    }`}
                  >
                    <span>Все разделы</span>
                    <span className="px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-black/30 text-neutral-300">
                      {kbStats?.total_active_examples ?? 0}
                    </span>
                  </button>
                  {kbStats?.root_services?.map(r => {
                    const cnt = kbStats.root_counts?.[r.root_id] ?? 0;
                    const isSelected = kbSelectedRootFilter === r.root_id;
                    return (
                      <button
                        key={r.root_id}
                        type="button"
                        onClick={() => {
                          setKbSelectedRootFilter(isSelected ? null : r.root_id);
                          setKbPage(1);
                        }}
                        className={`px-3 py-1.5 rounded-xl text-xs font-medium shrink-0 transition-all cursor-pointer flex items-center gap-1.5 ${
                          isSelected
                            ? 'bg-blue-600 text-white shadow-sm'
                            : 'bg-neutral-950 hover:bg-neutral-800 text-neutral-400 hover:text-neutral-200 border border-neutral-800'
                        }`}
                      >
                        <span>{r.name}</span>
                        <span
                          className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${
                            isSelected
                              ? 'bg-black/30 text-white'
                              : cnt > 0
                              ? 'bg-neutral-800 text-neutral-300'
                              : 'bg-neutral-900 text-neutral-600'
                          }`}
                        >
                          {cnt}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {/* Active Filters Bar */}
                {(kbSelectedRootFilter || kbSearch) && (
                  <div className="flex flex-wrap items-center gap-2 pt-0.5">
                    <span className="text-[11px] text-neutral-500 font-medium">Активные фильтры:</span>
                    {kbSelectedRootFilter && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-lg bg-blue-500/10 text-blue-300 border border-blue-500/30 text-xs">
                        <span>
                          Раздел: {kbStats?.root_services?.find(r => r.root_id === kbSelectedRootFilter)?.name || kbSelectedRootFilter}
                        </span>
                        <button
                          type="button"
                          onClick={() => {
                            setKbSelectedRootFilter(null);
                            setKbPage(1);
                          }}
                          className="hover:text-white cursor-pointer ml-0.5 text-sm"
                          title="Снять фильтр раздела"
                        >
                          &times;
                        </button>
                      </span>
                    )}
                    {kbSearch && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-lg bg-neutral-800 text-neutral-300 border border-neutral-700 text-xs">
                        <span>Поиск: "{kbSearch}"</span>
                        <button
                          type="button"
                          onClick={() => {
                            setKbSearchInput('');
                            setKbSearch('');
                            setKbPage(1);
                          }}
                          className="hover:text-white cursor-pointer ml-0.5 text-sm"
                          title="Очистить поиск"
                        >
                          &times;
                        </button>
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={handleResetFilters}
                      className="text-[11px] text-neutral-400 hover:text-rose-400 underline cursor-pointer ml-1 transition-colors"
                    >
                      Сбросить все
                    </button>
                  </div>
                )}
              </div>

              {/* Instant Search input */}
              <div className="relative">
                <input
                  type="text"
                  value={kbSearchInput}
                  onChange={e => setKbSearchInput(e.target.value)}
                  placeholder="Мгновенный поиск по номеру заявки, теме, проблеме или решению..."
                  className="w-full pl-9 pr-9 py-2.5 bg-neutral-950 border border-neutral-700 rounded-xl text-xs text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-blue-500 transition-colors"
                />
                <svg
                  className="absolute left-3 top-3 text-neutral-500"
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
                {kbSearchInput && (
                  <button
                    type="button"
                    onClick={() => setKbSearchInput('')}
                    className="absolute right-3 top-2.5 text-neutral-400 hover:text-neutral-200 cursor-pointer p-0.5"
                    title="Очистить строку"
                  >
                    &times;
                  </button>
                )}
              </div>
            </div>

            {/* Knowledge Base Examples Table */}
            <div ref={kbTableRef} className="bg-neutral-900 border border-neutral-800 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-neutral-800 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold">Проиндексированные прецеденты</h3>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-neutral-800 text-neutral-300">
                    {kbTotal} {kbTotal === 1 ? 'запись' : kbTotal < 5 ? 'записи' : 'записей'}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => {
                      setPurgeConfirmed(false);
                      setIsPurgeModalOpen(true);
                    }}
                    disabled={kbTotal === 0}
                    className="px-2.5 py-1 text-xs font-semibold text-rose-400 hover:text-rose-300 hover:bg-rose-950/40 rounded-lg border border-rose-900/40 transition-colors flex items-center gap-1.5 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    title="Полная очистка базы знаний RAG"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                    </svg>
                    <span>Очистить базу знаний</span>
                  </button>
                  {kbLoading && (
                    <span className="text-xs text-neutral-400 flex items-center gap-1.5">
                      <span className="w-3 h-3 border-2 border-neutral-400/30 border-t-neutral-400 rounded-full animate-spin"></span>
                      Загрузка...
                    </span>
                  )}
                </div>
              </div>

              {kbExamples.length === 0 && !kbLoading ? (
                <div className="p-12 text-center text-neutral-500 text-xs">
                  {kbSearch || kbSelectedRootFilter
                    ? 'По выбранным фильтрам прецедентов не найдено.'
                    : 'База знаний пока пуста. Запустите синхронизацию выше.'}
                </div>
              ) : (
                <div className="divide-y divide-neutral-800/60">
                  {kbExamples.map(item => {
                    const isExpanded = !!expandedTasks[item.task_id];
                    const isLongText = (item.problem?.length || 0) > 180 || (item.solution?.length || 0) > 180;
                    return (
                      <div key={item.task_id} className="p-5 hover:bg-neutral-800/30 transition-colors space-y-3">
                        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="px-2 py-0.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-400 text-xs font-mono font-semibold">
                              #{item.task_id}
                            </span>
                            <span className="text-xs font-semibold text-neutral-200">
                              {item.original_name || 'Без названия'}
                            </span>
                            <span className="px-2 py-0.5 rounded text-[10px] bg-neutral-800 text-neutral-400">
                              {item.service_name}
                            </span>
                            {/* Resolution Outcome Badge */}
                            <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border flex items-center gap-1 ${
                              item.resolution_type === 'rejected' ? 'bg-rose-500/10 border-rose-500/30 text-rose-300' :
                              item.resolution_type === 'cancelled' ? 'bg-amber-500/10 border-amber-500/30 text-amber-300' :
                              item.resolution_type === 'redirected' ? 'bg-sky-500/10 border-sky-500/30 text-sky-300' :
                              item.resolution_type === 'consultation' ? 'bg-indigo-500/10 border-indigo-500/30 text-indigo-300' :
                              item.resolution_type === 'duplicate' ? 'bg-amber-500/10 border-amber-500/30 text-amber-300' :
                              'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                            }`}>
                              <span className="w-1.5 h-1.5 rounded-full bg-current opacity-80"></span>
                              <span>{item.status_name ? `${item.status_name}: ` : ''}{item.resolution_label || 'Выполнено'}</span>
                            </span>
                            {item.root_cause && (
                              <span className="px-2 py-0.5 rounded text-[10px] bg-neutral-800/80 text-neutral-400 border border-neutral-700/50">
                                Причина: {item.root_cause}
                              </span>
                            )}
                            {typeof item.quality_score === 'number' && (
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold border flex items-center gap-1 ${
                                  item.quality_score >= 0.8
                                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                                    : item.quality_score >= 0.5
                                    ? 'bg-yellow-500/10 border-yellow-500/30 text-yellow-400'
                                    : 'bg-rose-500/10 border-rose-500/30 text-rose-400'
                                }`}
                                title={`Скоринг ценности решения для Helpdesk: ${(item.quality_score * 100).toFixed(0)}%`}
                              >
                                <span>{item.quality_score >= 0.8 ? '⭐ ' : ''}Ценность: {(item.quality_score * 100).toFixed(0)}%</span>
                              </span>
                            )}
                          </div>

                          <div className="flex items-center gap-2 shrink-0">
                            {/* Copy Solution button */}
                            <button
                              type="button"
                              onClick={() => handleCopySolution(item.task_id, item.solution || '')}
                              className="px-2.5 py-1 text-[11px] font-medium text-neutral-300 hover:text-white bg-neutral-800 hover:bg-neutral-700 rounded-lg border border-neutral-700 transition-colors flex items-center gap-1 cursor-pointer"
                              title="Скопировать решение в буфер обмена"
                            >
                              {copiedTaskId === item.task_id ? (
                                <span className="text-emerald-400 font-semibold">Скопировано!</span>
                              ) : (
                                <>
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                                  </svg>
                                  <span>Решение</span>
                                </>
                              )}
                            </button>

                            {/* IntraService direct link */}
                            <a
                              href={`/api/v1/tasks/${item.task_id}/open`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="px-2.5 py-1 text-[11px] font-medium text-blue-400 hover:text-blue-300 hover:bg-blue-950/40 rounded-lg border border-blue-900/40 transition-colors flex items-center gap-1 cursor-pointer"
                              title="Открыть карточку заявки в IntraService"
                            >
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                                <polyline points="15 3 21 3 21 9"></polyline>
                                <line x1="10" y1="14" x2="21" y2="3"></line>
                              </svg>
                              <span>В тикет</span>
                            </a>

                            {/* Blacklist button */}
                            <button
                              onClick={() => handleBlacklistExample(item.task_id)}
                              disabled={blacklistingTaskId === item.task_id}
                              title="Скрыть прецедент из базы знаний RAG"
                              className="px-2.5 py-1 text-[11px] font-medium text-amber-400 hover:text-amber-300 hover:bg-amber-950/40 rounded-lg border border-amber-900/40 transition-colors flex items-center gap-1 cursor-pointer"
                            >
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
                                <line x1="1" y1="1" x2="23" y2="23"></line>
                              </svg>
                              <span>{blacklistingTaskId === item.task_id ? 'Скрытие...' : 'Скрыть'}</span>
                            </button>
                          </div>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                          <div className="p-3 bg-neutral-950/60 rounded-xl border border-neutral-800/80">
                            <p className="text-[10px] uppercase font-semibold text-neutral-500 tracking-wider mb-1">
                              Суть проблемы / Запрос
                            </p>
                            <p className={`text-neutral-300 leading-relaxed whitespace-pre-wrap ${!isExpanded ? 'line-clamp-3' : ''}`}>
                              {item.problem || '—'}
                            </p>
                          </div>

                          <div className={`p-3 rounded-xl border ${
                            item.resolution_type === 'rejected' ? 'bg-rose-950/20 border-rose-900/30' :
                            item.resolution_type === 'cancelled' ? 'bg-amber-950/20 border-amber-900/30' :
                            item.resolution_type === 'redirected' ? 'bg-sky-950/20 border-sky-900/30' :
                            item.resolution_type === 'consultation' ? 'bg-indigo-950/20 border-indigo-900/30' :
                            'bg-emerald-950/20 border-emerald-900/30'
                          }`}>
                            <div className="flex items-center justify-between mb-1">
                              <p className={`text-[10px] uppercase font-semibold tracking-wider ${
                                item.resolution_type === 'rejected' ? 'text-rose-400' :
                                item.resolution_type === 'cancelled' ? 'text-amber-400' :
                                item.resolution_type === 'redirected' ? 'text-sky-400' :
                                item.resolution_type === 'consultation' ? 'text-indigo-400' :
                                'text-emerald-400'
                              }`}>
                                {item.resolution_type === 'rejected' ? 'Причина отказа / Резолюция' :
                                 item.resolution_type === 'cancelled' ? 'Причина отмены' :
                                 item.resolution_type === 'redirected' ? 'Маршрут перенаправления' :
                                 item.resolution_type === 'duplicate' ? 'Дубликат заявки' :
                                 'Решение / Ответ инженера'}
                              </p>
                              <span className="text-[9.5px] font-mono text-neutral-400">
                                {item.status_name}
                              </span>
                            </div>
                            <p className={`text-neutral-300 leading-relaxed whitespace-pre-wrap ${!isExpanded ? 'line-clamp-3' : ''}`}>
                              {item.solution || '—'}
                            </p>
                          </div>
                        </div>

                        {isLongText && (
                          <div className="flex justify-end">
                            <button
                              type="button"
                              onClick={() => handleToggleExpand(item.task_id)}
                              className="text-[11px] text-blue-400 hover:text-blue-300 font-medium cursor-pointer transition-colors flex items-center gap-1"
                            >
                              <span>{isExpanded ? 'Свернуть текст' : 'Развернуть полностью'}</span>
                              <span>{isExpanded ? '↑' : '↓'}</span>
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Enhanced Pagination Bar */}
              {kbTotal > 0 && (
                <div className="px-6 py-3.5 border-t border-neutral-800 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-neutral-400">
                  <div className="flex items-center gap-4">
                    <span>
                      Показано <strong className="text-neutral-200 font-mono">{(kbPage - 1) * kbLimit + 1}</strong>–
                      <strong className="text-neutral-200 font-mono">{Math.min(kbPage * kbLimit, kbTotal)}</strong> из{' '}
                      <strong className="text-neutral-200 font-mono">{kbTotal}</strong>
                    </span>

                    <div className="flex items-center gap-1.5">
                      <span className="text-neutral-500">На странице:</span>
                      <select
                        value={kbLimit}
                        onChange={e => {
                          setKbLimit(Number(e.target.value));
                          setKbPage(1);
                        }}
                        className="px-2 py-1 bg-neutral-950 border border-neutral-700 rounded-lg text-xs text-neutral-200 focus:outline-none focus:border-blue-500 cursor-pointer"
                      >
                        <option value={10}>10</option>
                        <option value={25}>25</option>
                        <option value={50}>50</option>
                      </select>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => handlePageChange(Math.max(kbPage - 1, 1))}
                      disabled={kbPage <= 1 || kbLoading}
                      className="px-2.5 py-1 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40 text-neutral-300 rounded-lg border border-neutral-700 transition-colors cursor-pointer"
                    >
                      ← Назад
                    </button>

                    {/* Numeric page buttons */}
                    {getPaginationPages(kbPage, Math.ceil(kbTotal / kbLimit)).map((p, idx) =>
                      typeof p === 'number' ? (
                        <button
                          key={idx}
                          onClick={() => handlePageChange(p)}
                          className={`w-7 h-7 rounded-lg text-xs font-mono transition-colors cursor-pointer ${
                            kbPage === p
                              ? 'bg-blue-600 text-white font-semibold shadow-xs'
                              : 'bg-neutral-800 hover:bg-neutral-700 text-neutral-300 border border-neutral-700'
                          }`}
                        >
                          {p}
                        </button>
                      ) : (
                        <span key={idx} className="px-1 text-neutral-600 font-mono">
                          ...
                        </span>
                      )
                    )}

                    <button
                      onClick={() => handlePageChange(kbPage * kbLimit < kbTotal ? kbPage + 1 : kbPage)}
                      disabled={kbPage * kbLimit >= kbTotal || kbLoading}
                      className="px-2.5 py-1 bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40 text-neutral-300 rounded-lg border border-neutral-700 transition-colors cursor-pointer"
                    >
                      Вперед →
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

      {/* Safety Confirmation Modal for KB Purge */}
      {isPurgeModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center gap-3 text-rose-400">
              <div className="w-10 h-10 rounded-xl bg-rose-500/10 border border-rose-500/20 flex items-center justify-center shrink-0">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                  <line x1="12" y1="9" x2="12" y2="13"/>
                  <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-neutral-100">Очистка базы знаний RAG</h3>
                <p className="text-xs text-neutral-400">Удаление всех векторных прецедентов</p>
              </div>
            </div>

            <p className="text-xs text-neutral-300 leading-relaxed">
              Вы собираетесь полностью удалить все проиндексированные решения ({kbTotal} записей) из базы PostgreSQL и сбросить кэш эмбеддингов в Redis. Это действие необратимо.
            </p>

            <label className="flex items-start gap-2.5 p-3 rounded-xl bg-neutral-950/80 border border-neutral-800 text-xs text-neutral-200 cursor-pointer">
              <input
                type="checkbox"
                checked={purgeConfirmed}
                onChange={e => setPurgeConfirmed(e.target.checked)}
                className="mt-0.5 rounded border-neutral-700 text-rose-600 focus:ring-rose-500 bg-neutral-900"
              />
              <span>Я подтверждаю удаление всех векторов и сброс базы знаний</span>
            </label>

            <div className="flex items-center justify-end gap-2.5 pt-2">
              <button
                type="button"
                onClick={() => {
                  setIsPurgeModalOpen(false);
                  setPurgeConfirmed(false);
                }}
                className="px-4 py-2 rounded-xl text-xs font-medium text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 transition-colors cursor-pointer"
              >
                Отмена
              </button>
              <button
                type="button"
                disabled={!purgeConfirmed || purgingKb}
                onClick={handlePurgeKnowledgeBase}
                className="px-4 py-2 rounded-xl text-xs font-semibold bg-rose-600 hover:bg-rose-500 disabled:opacity-40 disabled:cursor-not-allowed text-white shadow-lg shadow-rose-900/30 transition-all cursor-pointer flex items-center gap-1.5"
              >
                {purgingKb ? 'Очистка...' : 'Очистить всё'}
              </button>
            </div>
          </div>
        </div>
      )}

      </div>
    </div>
  );
}
