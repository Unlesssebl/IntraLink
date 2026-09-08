import { useState, useEffect, useCallback } from 'react';
import {
  fetchSystemStatus,
  fetchDomainAuth,
  fetchTelegramUsers,
  toggleTelegramUser,
  deleteTelegramUser,
  restartWorkerService,
} from '../lib/tasks';
import { IconSun, IconMoon } from '../components/Icons';
import { apiFetch } from '../lib/api';
import {
  fetchAutopilotSetting,
  saveAutopilotScenario,
  updateAutopilotSetting,
  fetchShadowMetrics,
  rollbackAutopilotScenarios,
  type AutopilotSetting,
  type AutopilotScenario,
  type AutopilotScenarioKey,
  type ShadowMetricsResponse,
} from '../lib/ticketRuns';
import { useAuth } from '../lib/auth';

const SCENARIO_TITLES: Record<string, string> = {
  create_user: 'Создание УЗ',
  user_creation: 'Создание УЗ (legacy)',
  install_printer: 'Установка принтера',
  printer_installation: 'Установка принтера (legacy)',
  grant_wlan: 'Доступ к корпоративному Wi-Fi',
  redirect: 'Перенаправление сервиса',
  offline_host: 'Диагностика недоступного ПК',
  file_lock: 'Блокировка файла',
  physical_device: 'Физическое устройство',
  rag_consultation: 'RAG-консультация',
  consultation: 'Консультация',
};

interface Props {
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

export default function SettingsPage({ theme, onToggleTheme, onToast }: Props) {
  const { user } = useAuth();
  const canManageAutopilot = Boolean(
    user?.permissions?.includes('autopilot:manage') || user?.permissions?.includes('*'),
  );
  // System status state
  const [systemStatus, setSystemStatus] = useState<any>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [restartingWorker, setRestartingWorker] = useState(false);
  const [autopilot, setAutopilot] = useState<AutopilotSetting | null>(null);
  const [savingAutopilot, setSavingAutopilot] = useState(false);
  const [scenarioServiceId, setScenarioServiceId] = useState('');
  const [scenarioKey, setScenarioKey] = useState<AutopilotScenarioKey>('install_printer');
  const [savingScenario, setSavingScenario] = useState(false);

  // Shadow metrics state
  const [shadowMetrics, setShadowMetrics] = useState<ShadowMetricsResponse | null>(null);
  const [loadingShadowMetrics, setLoadingShadowMetrics] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);

  // Domain auth state
  const [domainAuth, setDomainAuth] = useState<{ is_configured: boolean; username: string | null }>({
    is_configured: false,
    username: null,
  });

  // Telegram users state
  const [tgUsers, setTgUsers] = useState<Array<{ tg_user_id: number; username?: string; full_name?: string; is_active: boolean }>>([]);
  const [loadingTgUsers, setLoadingTgUsers] = useState(false);
  const [telegramLinkCode, setTelegramLinkCode] = useState<string | null>(null);
  const [creatingLinkCode, setCreatingLinkCode] = useState(false);

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
    setLoadingShadowMetrics(true);
    try {
      const [sys, dom, usersRes, autopilotRes, shadowRes] = await Promise.allSettled([
        fetchSystemStatus(),
        fetchDomainAuth(),
        fetchTelegramUsers(),
        fetchAutopilotSetting(),
        fetchShadowMetrics(7),
      ]);

      if (sys.status === 'fulfilled') setSystemStatus(sys.value);
      if (dom.status === 'fulfilled') {
        setDomainAuth(dom.value);
      }
      if (usersRes.status === 'fulfilled' && usersRes.value.users) {
        setTgUsers(usersRes.value.users);
      }
      if (autopilotRes.status === 'fulfilled') setAutopilot(autopilotRes.value);
      if (shadowRes.status === 'fulfilled') setShadowMetrics(shadowRes.value);
    } catch (err: any) {
      console.error('Ошибка загрузки настроек:', err);
    } finally {
      setLoadingStatus(false);
      setLoadingTgUsers(false);
      setLoadingShadowMetrics(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

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

  const handleCreateTelegramLinkCode = async () => {
    setCreatingLinkCode(true);
    try {
      const data = await apiFetch<{ code: string }>('/api/v2/identities/telegram/link-code', { method: 'POST' });
      setTelegramLinkCode(data.code);
      await navigator.clipboard?.writeText(data.code).catch(() => undefined);
      onToast({ type: 'success', message: 'Код создан и скопирован. Он действует 10 минут.' });
    } catch (err: any) {
      onToast({ type: 'error', message: `Не удалось создать код: ${err.message || err}` });
    } finally {
      setCreatingLinkCode(false);
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

  const handleToggleAutopilot = async () => {
    if (!autopilot || savingAutopilot) return;
    setSavingAutopilot(true);
    try {
      const enabled = !autopilot.enabled;
      const updated = await updateAutopilotSetting(
        enabled,
        autopilot.version,
        enabled ? 'Включено администратором из Web UI' : 'Выключено администратором из Web UI',
      );
      setAutopilot(updated);
      onToast({
        type: enabled ? 'success' : 'warning',
        message: enabled ? 'Автопилот включён' : 'Автопилот выключен; автоматические циклы приостановлены',
      });
    } catch (err: any) {
      onToast({ type: 'error', message: `Не удалось изменить автопилот: ${err.message || err}` });
      await loadAll();
    } finally {
      setSavingAutopilot(false);
    }
  };

  const handleAddScenario = async () => {
    if (!autopilot || !scenarioServiceId.trim()) return;
    setSavingScenario(true);
    try {
      await saveAutopilotScenario({
        service_id: Number(scenarioServiceId),
        scenario_key: scenarioKey,
        enabled: true,
        rollout_mode: 'shadow',
        canary_percent: 10,
        config: {},
      });
      setScenarioServiceId('');
      await loadAll();
      onToast({ type: 'success', message: 'Сервис добавлен в область автопилота (режим Shadow)' });
    } catch (err: any) {
      onToast({ type: 'error', message: `Не удалось сохранить сценарий: ${err.message || err}` });
    } finally {
      setSavingScenario(false);
    }
  };

  const handleToggleScenario = async (
    serviceId: number,
    scenarioKeyVal: AutopilotScenarioKey,
    enabled: boolean,
    version: number,
    config: Record<string, unknown>,
    rolloutMode: 'legacy' | 'shadow' | 'canary' | 'active',
    canaryPercent?: number,
  ) => {
    setSavingScenario(true);
    try {
      await saveAutopilotScenario({
        service_id: serviceId,
        scenario_key: scenarioKeyVal,
        enabled,
        rollout_mode: rolloutMode,
        canary_percent: canaryPercent,
        config,
        expected_version: version,
      });
      await loadAll();
    } catch (err: any) {
      onToast({ type: 'error', message: `Не удалось изменить сценарий: ${err.message || err}` });
    } finally {
      setSavingScenario(false);
    }
  };

  const handleRollbackAllToLegacy = async () => {
    if (!confirm('Вы уверены, что хотите аварийно перевести ВСЕ сценарии в режим Legacy? Новый сценарный контур будет отключен.')) return;
    setRollingBack(true);
    try {
      const res = await rollbackAutopilotScenarios({
        target_mode: 'legacy',
        reason: 'Аварийный откат на Legacy из Web UI',
      });
      onToast({
        type: 'warning',
        message: `Откат выполнен: переведено сценариев — ${res.rolled_back_count}. Режим: legacy.`,
      });
      await loadAll();
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка отката: ${err.message || err}` });
    } finally {
      setRollingBack(false);
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
            Управление интеграциями, автопилотом, Shadow/Canary раскаткой сценариев и безопасностью
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canManageAutopilot && (
            <button
              onClick={handleRollbackAllToLegacy}
              disabled={rollingBack}
              title="Экстренный откат всех сценариев в режим Legacy"
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-rose-50 text-rose-700 hover:bg-rose-100 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60 border border-rose-200 dark:border-rose-900 rounded-md transition-colors cursor-pointer disabled:opacity-50"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                <path d="M3 3v5h5" />
              </svg>
              <span>{rollingBack ? 'Откат…' : 'Rollback в Legacy'}</span>
            </button>
          )}
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
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Autopilot and Scenarios */}
        <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-5 space-y-4">
          <div className="flex items-start justify-between gap-4 border-b border-neutral-100 pb-3 dark:border-neutral-800">
            <div>
              <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">Автопилот заявок</h2>
              <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
                Безопасный переход: Legacy → Shadow (теневое сравнение) → Canary (доля %) → Active.
              </p>
            </div>
            <span className={`rounded px-2 py-1 text-[11px] font-semibold ${autopilot?.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300'}`}>
              {autopilot?.enabled ? 'Включён' : 'Выключен'}
            </span>
          </div>
          <div className="flex items-center justify-between gap-4">
            <div className="text-[11px] text-neutral-500 dark:text-neutral-400">
              {autopilot ? `Версия ${autopilot.version} · ${autopilot.updated_by}` : 'Настройка недоступна'}
              {autopilot && !autopilot.templates_ready && (
                <div className="mt-1 text-amber-600 dark:text-amber-300">
                  Не настроены шаблоны: {autopilot.missing_templates.join(', ')}
                </div>
              )}
              {autopilot && !autopilot.service_identity_ready && (
                <div className="mt-1 text-amber-600 dark:text-amber-300">Сервисная учётная запись не проверена</div>
              )}
              {autopilot?.service_user_id && (
                <div className="mt-1 font-mono">IntraService user ID: {autopilot.service_user_id}</div>
              )}
            </div>
            <button
              type="button"
              disabled={!canManageAutopilot || !autopilot || savingAutopilot || (!autopilot.enabled && (!autopilot.templates_ready || !autopilot.service_identity_ready || autopilot.scenarios.filter(item => item.enabled).length === 0))}
              onClick={handleToggleAutopilot}
              title={canManageAutopilot ? undefined : 'Требуется право autopilot:manage'}
              className={`rounded px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 ${autopilot?.enabled ? 'bg-rose-600 hover:bg-rose-500' : 'bg-blue-600 hover:bg-blue-500'}`}
            >
              {savingAutopilot ? 'Сохранение…' : autopilot?.enabled ? 'Выключить' : 'Включить'}
            </button>
          </div>
          <div className="space-y-2 border-t border-neutral-100 pt-3 dark:border-neutral-800">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs font-medium text-neutral-800 dark:text-neutral-200">Поддерживаемые сервисы</div>
                <div className="text-[11px] text-neutral-500">Режимы раскатки и доля Canary-трафика</div>
              </div>
              <span className="text-[11px] text-neutral-500">{autopilot?.scenarios.filter(item => item.enabled).length || 0} включено</span>
            </div>
            <div className="flex gap-2">
              <select
                value={scenarioKey}
                onChange={event => setScenarioKey(event.target.value as AutopilotScenarioKey)}
                className="rounded border border-neutral-200 bg-white px-2.5 py-1.5 text-xs text-neutral-900 outline-none focus:border-blue-500 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"
              >
                <option value="install_printer">Установка принтера</option>
                <option value="create_user">Создание учётной записи</option>
                <option value="grant_wlan">Доступ к Wi-Fi (WLAN)</option>
                <option value="offline_host">Недоступный ПК</option>
                <option value="redirect">Перенаправление</option>
                <option value="file_lock">Блокировка файла</option>
                <option value="physical_device">Физическое устройство</option>
                <option value="rag_consultation">RAG-консультация</option>
              </select>
              <input
                type="number"
                min={1}
                value={scenarioServiceId}
                onChange={event => setScenarioServiceId(event.target.value)}
                placeholder="ID сервиса IntraService"
                className="min-w-0 flex-1 rounded border border-neutral-200 bg-white px-2.5 py-1.5 text-xs text-neutral-900 outline-none focus:border-blue-500 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"
              />
              <button type="button" onClick={handleAddScenario} disabled={!canManageAutopilot || savingScenario || !scenarioServiceId.trim()} className="rounded bg-neutral-900 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900">Добавить</button>
            </div>
            {autopilot?.scenarios.map(item => (
              <div key={item.id} className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 rounded border border-neutral-200/80 px-2.5 py-2 text-xs dark:border-neutral-800">
                <div>
                  <span className="font-mono font-semibold">#{item.service_id}</span>
                  <span className="ml-2 text-neutral-700 dark:text-neutral-300 font-medium">
                    {SCENARIO_TITLES[item.scenario_key] || item.scenario_key}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <select
                    value={item.rollout_mode}
                    disabled={!canManageAutopilot || savingScenario}
                    onChange={event => handleToggleScenario(
                      item.service_id,
                      item.scenario_key,
                      item.enabled,
                      item.version,
                      item.config,
                      event.target.value as 'legacy' | 'shadow' | 'canary' | 'active',
                      item.canary_percent ?? 10,
                    )}
                    className="rounded border border-neutral-200 bg-white px-2 py-1 text-[11px] font-medium dark:border-neutral-700 dark:bg-neutral-950"
                  >
                    <option value="legacy">Legacy</option>
                    <option value="shadow">Shadow (Теневой)</option>
                    <option value="canary">Canary (%)</option>
                    <option value="active">Active (100%)</option>
                  </select>

                  {item.rollout_mode === 'canary' && (
                    <div className="flex items-center gap-1 text-[11px] text-neutral-500" title="Доля трафика на новый контур (1-100%)">
                      <input
                        type="number"
                        min={1}
                        max={100}
                        defaultValue={item.canary_percent ?? 10}
                        disabled={!canManageAutopilot || savingScenario}
                        onBlur={event => {
                          const val = Math.min(100, Math.max(1, Number(event.target.value) || 10));
                          handleToggleScenario(
                            item.service_id,
                            item.scenario_key,
                            item.enabled,
                            item.version,
                            item.config,
                            'canary',
                            val,
                          );
                        }}
                        className="w-12 rounded border border-neutral-200 bg-white px-1 py-0.5 text-center font-mono text-[11px] dark:border-neutral-700 dark:bg-neutral-950 text-neutral-900 dark:text-neutral-100"
                      />
                      <span>%</span>
                    </div>
                  )}

                  <button
                    type="button"
                    disabled={!canManageAutopilot || savingScenario}
                    onClick={() => handleToggleScenario(
                      item.service_id,
                      item.scenario_key,
                      !item.enabled,
                      item.version,
                      item.config,
                      item.rollout_mode,
                      item.canary_percent,
                    )}
                    className={`rounded px-2 py-1 text-[11px] font-semibold transition-colors cursor-pointer ${item.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300'}`}
                  >
                    {item.enabled ? 'Включён' : 'Выключен'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

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
      </div>

      {/* Shadow Metrics Dashboard Section */}
      <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-5 space-y-4">
        <div className="flex items-center justify-between border-b border-neutral-100 dark:border-neutral-800 pb-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                Валидация теневого режима (Shadow Mode Metrics)
              </h2>
              {shadowMetrics && (
                <span className={`px-2 py-0.5 rounded text-[11px] font-semibold ${shadowMetrics.overall_divergence_rate_percent === 0 ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300'}`}>
                  {shadowMetrics.overall_divergence_rate_percent === 0 ? 'Совпадение 100%' : `Расхождения: ${shadowMetrics.overall_divergence_rate_percent.toFixed(1)}%`}
                </span>
              )}
            </div>
            <p className="text-[11px] text-neutral-500 dark:text-neutral-400 mt-0.5">
              Автоматическое сопоставление решений legacy-движка и TicketRunOrchestrator без побочных эффектов
            </p>
          </div>
          <button
            onClick={loadAll}
            disabled={loadingShadowMetrics}
            className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
          >
            {loadingShadowMetrics ? 'Обновление…' : 'Обновить метрики'}
          </button>
        </div>

        {/* Top KPI Cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div className="p-3 bg-neutral-50 dark:bg-neutral-850 rounded-md border border-neutral-200 dark:border-neutral-750">
            <div className="text-[11px] text-neutral-500">Теневых прогонов</div>
            <div className="text-lg font-bold font-mono text-neutral-900 dark:text-neutral-100 mt-0.5">
              {shadowMetrics?.total_shadow_evaluations ?? 0}
            </div>
            <div className="text-[10px] text-neutral-400 mt-0.5">за последние {shadowMetrics?.period_days ?? 7} дней</div>
          </div>
          <div className="p-3 bg-neutral-50 dark:bg-neutral-850 rounded-md border border-neutral-200 dark:border-neutral-750">
            <div className="text-[11px] text-neutral-500">Расхождений (Diverged)</div>
            <div className={`text-lg font-bold font-mono mt-0.5 ${(shadowMetrics?.total_diverged ?? 0) > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
              {shadowMetrics?.total_diverged ?? 0}
            </div>
            <div className="text-[10px] text-neutral-400 mt-0.5">несоответствий логики</div>
          </div>
          <div className="p-3 bg-neutral-50 dark:bg-neutral-850 rounded-md border border-neutral-200 dark:border-neutral-750">
            <div className="text-[11px] text-neutral-500">Divergence Rate</div>
            <div className="text-lg font-bold font-mono text-neutral-900 dark:text-neutral-100 mt-0.5">
              {(shadowMetrics?.overall_divergence_rate_percent ?? 0).toFixed(1)}%
            </div>
            <div className="text-[10px] text-neutral-400 mt-0.5">порог безопасности &lt; 5%</div>
          </div>
          <div className="p-3 bg-neutral-50 dark:bg-neutral-850 rounded-md border border-neutral-200 dark:border-neutral-750">
            <div className="text-[11px] text-neutral-500">Готовность к раскатке</div>
            <div className="text-xs font-semibold mt-1 text-emerald-600 dark:text-emerald-400">
              {(shadowMetrics?.overall_divergence_rate_percent ?? 0) === 0 ? 'Готов к Canary / Active' : 'Требует ревизии'}
            </div>
            <div className="text-[10px] text-neutral-400 mt-0.5">контроль безопасности</div>
          </div>
        </div>

        {/* By-Scenario Stats */}
        {shadowMetrics && Object.keys(shadowMetrics.by_scenario).length > 0 && (
          <div className="overflow-x-auto border-t border-neutral-100 dark:border-neutral-800 pt-3">
            <div className="text-xs font-medium text-neutral-800 dark:text-neutral-200 mb-2">Статистика по сценариям</div>
            <table className="w-full text-xs text-left">
              <thead className="text-neutral-400 font-medium border-b border-neutral-200 dark:border-neutral-800">
                <tr>
                  <th className="py-1.5 px-2">Сценарий</th>
                  <th className="py-1.5 px-2 text-right">Прогонов</th>
                  <th className="py-1.5 px-2 text-right">Расхождений</th>
                  <th className="py-1.5 px-2 text-right">Divergence Rate</th>
                  <th className="py-1.5 px-2 text-right">Avg Confidence</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-850 font-mono">
                {Object.entries(shadowMetrics.by_scenario).map(([key, stat]) => (
                  <tr key={key} className="hover:bg-neutral-50/50 dark:hover:bg-neutral-800/40">
                    <td className="py-1.5 px-2 font-sans text-neutral-800 dark:text-neutral-200">
                      {SCENARIO_TITLES[key] || key}
                    </td>
                    <td className="py-1.5 px-2 text-right">{stat.total}</td>
                    <td className={`py-1.5 px-2 text-right ${stat.diverged > 0 ? 'text-amber-600 dark:text-amber-400 font-bold' : 'text-neutral-500'}`}>
                      {stat.diverged}
                    </td>
                    <td className="py-1.5 px-2 text-right">{stat.divergence_rate_percent.toFixed(1)}%</td>
                    <td className="py-1.5 px-2 text-right text-neutral-500">
                      {stat.avg_confidence ? stat.avg_confidence.toFixed(2) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Recent Divergences List */}
        {shadowMetrics && shadowMetrics.recent_divergences.length > 0 && (
          <div className="border-t border-neutral-100 dark:border-neutral-800 pt-3 space-y-2">
            <div className="text-xs font-medium text-amber-700 dark:text-amber-300">
              Недавние расхождения ({shadowMetrics.recent_divergences.length})
            </div>
            <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
              {shadowMetrics.recent_divergences.map((item, idx) => (
                <div key={idx} className="p-2 rounded bg-amber-50/50 dark:bg-amber-950/20 border border-amber-200/60 dark:border-amber-900/40 text-xs">
                  <div className="flex items-center justify-between text-[11px] text-neutral-500">
                    <span className="font-mono text-neutral-700 dark:text-neutral-300">ID: {item.ticket_run_id}</span>
                    <span>{item.created_at ? new Date(item.created_at).toLocaleString('ru-RU') : ''}</span>
                  </div>
                  <div className="mt-1 text-amber-900 dark:text-amber-200">
                    {item.divergence_reasons.join(', ')}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Domain Auth and Telegram Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Card 2: Domain Auth Status (Active Directory / WinRM) */}
        <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-5 space-y-4 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-neutral-100 dark:border-neutral-800 pb-3">
              <div>
                <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">Домен Active Directory & WinRM</h2>
                <p className="text-[11px] text-neutral-400">Централизованный SSOT Vault в PostgreSQL</p>
              </div>
              <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${domainAuth.is_configured ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300'}`}>
                {domainAuth.is_configured ? 'Сконфигурирована' : 'Не настроена'}
              </span>
            </div>

            <div className="space-y-2.5 text-xs mt-3.5">
              <div className="flex items-center justify-between py-1.5 border-b border-neutral-100 dark:border-neutral-850">
                <span className="text-neutral-500 dark:text-neutral-400">Доменный пользователь:</span>
                <span className="font-mono text-neutral-800 dark:text-neutral-200 font-medium">
                  {domainAuth.username || '—'}
                </span>
              </div>

              <div className="flex items-center justify-between py-1.5 border-b border-neutral-100 dark:border-neutral-850">
                <span className="text-neutral-500 dark:text-neutral-400">Шифрование секретов:</span>
                <span className="text-emerald-600 dark:text-emerald-400 font-medium">Fernet AES-128-CBC (SSOT)</span>
              </div>

              <div className="flex items-center justify-between py-1.5">
                <span className="text-neutral-500 dark:text-neutral-400">Назначение учетной записи:</span>
                <span className="text-neutral-700 dark:text-neutral-300">Установка принтеров, WMI, WinRM</span>
              </div>
            </div>

            <p className="text-[11.5px] text-neutral-500 dark:text-neutral-400 mt-3 leading-relaxed">
              Управление доменным доступом, контроллерами домена и LDAPS портами централизовано в защищенной консоли администратора.
            </p>
          </div>

          <div className="pt-3 border-t border-neutral-100 dark:border-neutral-800">
            <a
              href="/admin"
              className="w-full py-2 px-3 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-750 text-neutral-800 dark:text-neutral-200 font-medium rounded text-xs transition-colors flex items-center justify-center gap-1.5 cursor-pointer text-center"
            >
              <span>Управление в консоли Vault SSOT</span>
              <span aria-hidden="true">→</span>
            </a>
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

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-md border border-blue-200 dark:border-blue-900 bg-blue-50/70 dark:bg-blue-950/20 p-3">
            <div>
              <div className="text-xs font-medium text-neutral-900 dark:text-neutral-100">Привязать мой Telegram</div>
              <div className="text-[11px] text-neutral-500 dark:text-neutral-400 mt-0.5">
                Создайте код и отправьте его боту после команды /login. Код одноразовый и действует 10 минут.
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {telegramLinkCode && (
                <code className="px-3 py-1.5 rounded bg-white dark:bg-neutral-900 border border-blue-200 dark:border-blue-800 text-sm font-semibold tracking-wide text-blue-700 dark:text-blue-300">
                  {telegramLinkCode}
                </code>
              )}
              <button
                type="button"
                onClick={handleCreateTelegramLinkCode}
                disabled={creatingLinkCode}
                className="py-1.5 px-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white font-medium rounded text-xs transition-colors cursor-pointer"
              >
                {creatingLinkCode ? 'Создание...' : 'Создать код'}
              </button>
            </div>
          </div>

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
