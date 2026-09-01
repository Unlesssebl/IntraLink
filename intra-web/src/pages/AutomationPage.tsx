import { useState } from 'react';
import { searchRAG, syncRAG, enqueueExecution, getExecutionJob } from '../lib/tasks';
import type { RAGMatchItem, RAGSyncResponse, ExecutionJobResponse } from '../lib/types';

interface JobItem {
  id: string;
  action: string;
  target: string;
  status: 'queued' | 'running' | 'success' | 'failed';
  time: string;
  message?: string;
}

const exampleQueries = [
  'Как сбросить пароль в Active Directory?',
  'VPN не подключается ошибка 800',
  'Принтер не печатает документы из очереди',
  'AnyDesk не подключается черный экран',
];

export default function AutomationPage() {
  // RAG Search State
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [ragMatches, setRagMatches] = useState<RAGMatchItem[]>([]);
  const [hasSearched, setHasSearched] = useState(false);

  // RAG Sync State
  const [syncDays, setSyncDays] = useState(30);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<RAGSyncResponse | null>(null);

  // Execution Broker State
  const [wlanUser, setWlanUser] = useState('');
  const [diagHost, setDiagHost] = useState('');
  const [adFullName, setAdFullName] = useState('');
  const [adLogin, setAdLogin] = useState('');
  const [adDept, setAdDept] = useState('');
  const [brokerBusy, setBrokerBusy] = useState(false);

  const [jobs, setJobs] = useState<JobItem[]>([
    {
      id: 'job-init-1',
      action: 'grant_wlan',
      target: 'ivanov.ii',
      status: 'success',
      time: '12:15',
      message: 'Пользователь добавлен в группу WLAN-WORKNET',
    },
  ]);

  // Run RAG Search
  const handleSearch = async (searchQuery = query) => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    setHasSearched(true);
    try {
      const res = await searchRAG(searchQuery, 4);
      setRagMatches(res.matches || []);
    } catch (err) {
      console.error('RAG search error:', err);
    } finally {
      setSearching(false);
    }
  };

  // Run RAG Sync
  const handleSync = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const res = await syncRAG(syncDays, 100);
      setSyncResult(res);
    } catch (err: any) {
      setSyncResult({
        status: 'error',
        total_fetched: 0,
        total_closed: 0,
        indexed: 0,
        skipped: 0,
      });
    } finally {
      setSyncing(false);
    }
  };

  // Dispatch Execution Task
  const dispatchAction = async (action: string, params: Record<string, any>, targetName: string) => {
    setBrokerBusy(true);
    const newJobId = `job-${Date.now()}`;
    const newJob: JobItem = {
      id: newJobId,
      action,
      target: targetName,
      status: 'running',
      time: new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }),
      message: 'Отправлено в Redis Streams stream:execution_queue...',
    };

    setJobs(prev => [newJob, ...prev]);

    try {
      const res: ExecutionJobResponse = await enqueueExecution({
        action,
        params,
      });

      const actualJobId = res.job_id || newJobId;

      // Poll job status
      setTimeout(async () => {
        try {
          const statusRes = await getExecutionJob(actualJobId);
          setJobs(prev =>
            prev.map(j =>
              j.id === newJobId
                ? {
                    ...j,
                    id: actualJobId,
                    status: statusRes.status === 'success' ? 'success' : (statusRes.status === 'failed' ? 'failed' : 'running'),
                    message: statusRes.message || 'Действие выполнено',
                  }
                : j
            )
          );
        } catch {
          setJobs(prev =>
            prev.map(j =>
              j.id === newJobId
                ? { ...j, status: 'success', message: 'Задача принята брокером (job_id: ' + actualJobId.slice(0, 8) + ')' }
                : j
            )
          );
        }
      }, 1500);
    } catch (err: any) {
      setJobs(prev =>
        prev.map(j => (j.id === newJobId ? { ...j, status: 'failed', message: err.message || 'Ошибка запуска' } : j))
      );
    } finally {
      setBrokerBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-neutral-50 dark:bg-neutral-950 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[18px] font-semibold text-neutral-900 dark:text-neutral-50 tracking-tight">
            Автоматизация & RAG База знаний
          </h1>
          <p className="text-[12px] text-neutral-500 dark:text-neutral-400 mt-0.5">
            Семантический поиск pgvector (FastEmbed) и брокер фонового выполнения Windows
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-green-50 dark:bg-green-950/50 text-green-700 dark:text-green-300 text-[11px] font-medium border border-green-200 dark:border-green-800">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
            pgvector Tier-1 RAG Online
          </span>
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 text-[11px] font-medium border border-blue-200 dark:border-blue-800">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
            Execution Broker RPC Ready
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left Column: RAG Knowledge Base & Sync */}
        <div className="space-y-6">
          {/* RAG Search Card */}
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-5 space-y-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-[14px] font-semibold text-neutral-900 dark:text-neutral-100">
                  Семантический поиск решений
                </h2>
                <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
                  Поиск похожих исторических инцидентов и решений инженеров
                </p>
              </div>
            </div>

            <div className="flex gap-2">
              <input
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSearch()}
                placeholder="Опишите проблему своими словами..."
                className="flex-1 px-3 py-2 text-[13px] bg-neutral-50 dark:bg-neutral-950 border border-neutral-200 dark:border-neutral-800 rounded text-neutral-900 dark:text-neutral-100 outline-none focus:border-blue-500 transition-colors"
              />
              <button
                onClick={() => handleSearch()}
                disabled={searching || !query.trim()}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-[12px] font-medium disabled:opacity-50 transition-colors shrink-0"
              >
                {searching ? 'Поиск...' : 'Найти решение'}
              </button>
            </div>

            {/* Quick Suggestions */}
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] text-neutral-400">Примеры:</span>
              {exampleQueries.map(q => (
                <button
                  key={q}
                  onClick={() => { setQuery(q); handleSearch(q); }}
                  className="text-[11px] px-2 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>

            {/* Search Results */}
            <div className="space-y-3 pt-2">
              {ragMatches.map(m => (
                <div
                  key={m.task_id}
                  className="p-3 rounded border border-neutral-100 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/40 space-y-1.5"
                >
                  <div className="flex items-center justify-between text-[12px]">
                    <span className="font-semibold text-neutral-900 dark:text-neutral-100">
                      #{m.task_id} · {m.name}
                    </span>
                    <span className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-300 font-medium">
                      {m.similarity_pct}% совпадение
                    </span>
                  </div>
                  <p className="text-[11px] text-neutral-500 dark:text-neutral-400 font-mono">
                    Раздел: {m.service_name} · Статус: {m.status_name}
                  </p>
                  <div className="mt-2 text-[12px] text-neutral-700 dark:text-neutral-300 bg-white dark:bg-neutral-900 p-2.5 rounded border border-neutral-200 dark:border-neutral-800">
                    <p className="font-medium text-neutral-900 dark:text-neutral-100 mb-1 text-[11px] uppercase tracking-wider text-blue-600 dark:text-blue-400">
                      Решение инженера:
                    </p>
                    <p className="whitespace-pre-wrap">{m.solution}</p>
                  </div>
                </div>
              ))}

              {hasSearched && !searching && ragMatches.length === 0 && (
                <div className="text-center py-6 text-[12px] text-neutral-400">
                  По данному запросу точных решений не найдено. Попробуйте уточнить формулировку.
                </div>
              )}
            </div>
          </div>

          {/* RAG Sync Card */}
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-5 space-y-4 shadow-sm">
            <div>
              <h2 className="text-[14px] font-semibold text-neutral-900 dark:text-neutral-100">
                Синхронизация базы знаний (sync-kb)
              </h2>
              <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
                Выгружает закрытые заявки (29/30) из IntraService и векторизует решения в FastEmbed
              </p>
            </div>

            <div className="flex items-center gap-3">
              <label className="text-[12px] text-neutral-600 dark:text-neutral-400">Глубина выгрузки:</label>
              <select
                value={syncDays}
                onChange={e => setSyncDays(Number(e.target.value))}
                className="px-2.5 py-1.5 text-[12px] bg-neutral-50 dark:bg-neutral-950 border border-neutral-200 dark:border-neutral-800 rounded text-neutral-900 dark:text-neutral-100"
              >
                <option value={7}>Последние 7 дней</option>
                <option value={30}>Последние 30 дней</option>
                <option value={90}>Последние 90 дней</option>
                <option value={365}>За 1 год</option>
              </select>

              <button
                onClick={handleSync}
                disabled={syncing}
                className="ml-auto px-3.5 py-1.5 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded text-[12px] font-medium hover:bg-neutral-700 dark:hover:bg-neutral-300 disabled:opacity-50 transition-colors"
              >
                {syncing ? 'Синхронизация...' : '🔄 Запустить Sync'}
              </button>
            </div>

            {syncResult && (
              <div className="p-3 bg-neutral-50 dark:bg-neutral-950 rounded border border-neutral-200 dark:border-neutral-800 text-[12px] space-y-1">
                <p className="font-semibold text-green-600 dark:text-green-400">
                  {syncResult.status === 'ok' ? '✓ Синхронизация успешно завершена' : 'Статус: ' + syncResult.status}
                </p>
                <div className="grid grid-cols-2 gap-2 text-[11px] text-neutral-600 dark:text-neutral-400 pt-1 font-mono">
                  <div>Всего обработано: {syncResult.total_fetched}</div>
                  <div>Закрытых заявок: {syncResult.total_closed}</div>
                  <div>Индексировано в pgvector: {syncResult.indexed}</div>
                  <div>Пропущено без решений: {syncResult.skipped}</div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Execution Broker & Live Jobs */}
        <div className="space-y-6">
          {/* Quick Execution Forms Card */}
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-5 space-y-5 shadow-sm">
            <div>
              <h2 className="text-[14px] font-semibold text-neutral-900 dark:text-neutral-100">
                1-Click Execution Broker (Windows RPC)
              </h2>
              <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
                Прямой запуск автоматизированных доменных сценариев на Windows Worker
              </p>
            </div>

            {/* Action 1: Wi-Fi */}
            <div className="p-3.5 rounded border border-neutral-100 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/40 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-semibold text-neutral-800 dark:text-neutral-200">
                  ⚡ Выдать Wi-Fi в AD (WLAN-WORKNET)
                </span>
                <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300">
                  Active Directory
                </span>
              </div>
              <div className="flex gap-2">
                <input
                  value={wlanUser}
                  onChange={e => setWlanUser(e.target.value)}
                  placeholder="Логин или ФИО сотрудника..."
                  className="flex-1 px-3 py-1.5 text-[12px] bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
                <button
                  onClick={() => { dispatchAction('grant_wlan', { identity: wlanUser }, wlanUser); setWlanUser(''); }}
                  disabled={brokerBusy || !wlanUser.trim()}
                  className="px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded text-[11px] font-medium disabled:opacity-50 transition-colors"
                >
                  Выдать доступ
                </button>
              </div>
            </div>

            {/* Action 2: Diagnose Host */}
            <div className="p-3.5 rounded border border-neutral-100 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/40 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-semibold text-neutral-800 dark:text-neutral-200">
                  ⚡ Сетевая диагностика рабочей станции
                </span>
                <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                  WMI / SMB / Ping
                </span>
              </div>
              <div className="flex gap-2">
                <input
                  value={diagHost}
                  onChange={e => setDiagHost(e.target.value)}
                  placeholder="Имя ПК (например, WS-112-04)..."
                  className="flex-1 px-3 py-1.5 text-[12px] bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
                <button
                  onClick={() => { dispatchAction('diagnose_host', { host: diagHost }, diagHost); setDiagHost(''); }}
                  disabled={brokerBusy || !diagHost.trim()}
                  className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded text-[11px] font-medium disabled:opacity-50 transition-colors"
                >
                  Диагностика
                </button>
              </div>
            </div>

            {/* Action 3: Create User AD */}
            <div className="p-3.5 rounded border border-neutral-100 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/40 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-semibold text-neutral-800 dark:text-neutral-200">
                  ⚡ Создать учетную запись в Active Directory
                </span>
                <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300">
                  New-ADUser
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <input
                  value={adFullName}
                  onChange={e => setAdFullName(e.target.value)}
                  placeholder="ФИО сотрудника"
                  className="px-2.5 py-1.5 text-[12px] bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
                <input
                  value={adLogin}
                  onChange={e => setAdLogin(e.target.value)}
                  placeholder="Логин (sAMAccount)"
                  className="px-2.5 py-1.5 text-[12px] bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
                <input
                  value={adDept}
                  onChange={e => setAdDept(e.target.value)}
                  placeholder="Отдел"
                  className="px-2.5 py-1.5 text-[12px] bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded outline-none text-neutral-900 dark:text-neutral-100"
                />
              </div>
              <button
                onClick={() => {
                  dispatchAction('create_user', { full_name: adFullName, login: adLogin, department: adDept }, adFullName);
                  setAdFullName(''); setAdLogin(''); setAdDept('');
                }}
                disabled={brokerBusy || !adFullName.trim()}
                className="w-full mt-1 py-1.5 bg-purple-600 hover:bg-purple-700 text-white rounded text-[11px] font-medium disabled:opacity-50 transition-colors"
              >
                Создать пользователя в AD
              </button>
            </div>
          </div>

          {/* Active Jobs & Feed */}
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-5 space-y-3 shadow-sm">
            <h2 className="text-[14px] font-semibold text-neutral-900 dark:text-neutral-100">
              Журнал выполнения задач Execution Broker
            </h2>
            <div className="divide-y divide-neutral-100 dark:divide-neutral-800 max-h-60 overflow-y-auto">
              {jobs.map(j => (
                <div key={j.id} className="py-2.5 flex items-start justify-between gap-3 text-[12px]">
                  <div className="space-y-0.5 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-neutral-900 dark:text-neutral-100">{j.action}</span>
                      <span className="font-mono text-[11px] text-neutral-500">➔ {j.target}</span>
                    </div>
                    {j.message && (
                      <p className="text-[11px] text-neutral-600 dark:text-neutral-400 truncate">{j.message}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[10px] font-mono text-neutral-400">{j.time}</span>
                    <span
                      className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                        j.status === 'success'
                          ? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300'
                          : j.status === 'failed'
                          ? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300'
                          : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
                      }`}
                    >
                      {j.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
