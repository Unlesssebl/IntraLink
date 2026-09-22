import React, { useEffect, useState, useMemo, useRef } from 'react';

// ============================================================================
// Типы данных API аналитики Helpdesk v2
// ============================================================================

export interface ServiceShareMetric {
  key: string;
  name: string;
  count: number;
  share_pct: number;
}

export interface ExecutiveInsight {
  id: string;
  title: string;
  value: string;
  description: string;
  tone: 'positive' | 'neutral' | 'warning' | 'negative' | 'accent';
}

export interface MonthLoadMetric {
  year: number;
  month: number;
  month_name: string;
  period_label: string;
  start_date_str: string;
  end_date_str: string;
  created: number;
  created_line1: number;
  created_line2: number;
  closed: number;
  workdays: number;
  daily_workday: number;
  daily_closed_workday: number;
  is_current: boolean;
  projected_created: number;
  balance: number;
  clear_rate: number;
  mom_delta_pct?: number | null;
  overdue_reaction: number;
  overdue_resolution: number;
  sla_resolution_rate: number;
  service_breakdown: ServiceShareMetric[];
  created_1c: number;
  created_directum: number;
  created_ats: number;
  total_tracked: number;
  tp_share_pct: number;
}

export interface YearLoadReport {
  generated_at: string;
  period_label: string;
  scope: string;
  months: MonthLoadMetric[];
  total_created: number;
  total_line1: number;
  total_line2: number;
  total_closed: number;
  total_balance: number;
  overall_clear_rate: number;
  avg_monthly_created: number;
  avg_daily_workday: number;
  avg_daily_closed_workday: number;
  created_mom_delta_pct?: number | null;
  peak_month: string;
  peak_created: number;
  min_month: string;
  min_created: number;
  total_overdue_reaction: number;
  total_overdue_resolution: number;
  overall_sla_resolution_rate: number;
  insights: ExecutiveInsight[];
  total_service_breakdown: ServiceShareMetric[];
  note: string | null;
  from_cache?: boolean;
}

type ChartMode = 'volume' | 'clear_rate' | 'sla';
type ScopeType = 'all' | 'line1' | 'line2';
type SortKey = 'date' | 'created' | 'closed' | 'balance' | 'clear_rate' | 'sla' | 'daily_workday';

interface ReportsPageProps {
  onNavigateToQueue?: (searchQuery?: string) => void;
}

// Палитра сервисов для категорий Root-Cause
const SERVICE_COLORS: Record<string, { bg: string; fill: string; text: string }> = {
  '03': { bg: 'bg-sky-500', fill: '#0284c7', text: 'text-sky-600 dark:text-sky-400' }, // Принтеры
  '02': { bg: 'bg-indigo-500', fill: '#6366f1', text: 'text-indigo-600 dark:text-indigo-400' }, // ПО
  '09': { bg: 'bg-emerald-500', fill: '#10b981', text: 'text-emerald-600 dark:text-emerald-400' }, // ЭЦП
  '01': { bg: 'bg-amber-500', fill: '#f59e0b', text: 'text-amber-600 dark:text-amber-400' }, // Учетки
  '04': { bg: 'bg-purple-500', fill: '#a855f7', text: 'text-purple-600 dark:text-purple-400' }, // Сеть
  '05': { bg: 'bg-blue-600', fill: '#2563eb', text: 'text-blue-600 dark:text-blue-400' }, // Directum
  '06': { bg: 'bg-orange-500', fill: '#f97316', text: 'text-orange-600 dark:text-orange-400' }, // 1C
  '10': { bg: 'bg-teal-500', fill: '#14b8a6', text: 'text-teal-600 dark:text-teal-400' }, // АТС
};

export default function ReportsPage({ onNavigateToQueue }: ReportsPageProps) {
  const [monthsCount, setMonthsCount] = useState<number>(12);
  const [scope, setScope] = useState<ScopeType>('all');
  const [chartMode, setChartMode] = useState<ChartMode>('volume');
  const [report, setReport] = useState<YearLoadReport | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<boolean>(false);

  // Выбранный месяц для drill-down среза (null = весь период)
  const [selectedMonthIdx, setSelectedMonthIdx] = useState<number | null>(null);

  // Интерактивный тултип диаграммы
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);

  // Сортировка ведомости
  const [sortKey, setSortKey] = useState<SortKey>('date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  // Загрузка отчёта
  const fetchReport = async (months: number, targetScope: ScopeType, forceRefresh: boolean = false) => {
    if (forceRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);

    try {
      const url = `/api/v2/reports/load?months=${months}&scope=${targetScope}${forceRefresh ? '&force_refresh=true' : ''}`;
      const response = await fetch(url, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });

      if (!response.ok) {
        throw new Error(`Ошибка сервера (${response.status}): не удалось сформировать аналитику.`);
      }

      const data: YearLoadReport = await response.json();
      setReport(data);
      // Если выбранный индекс выходит за рамки нового набора, сбрасываем
      if (selectedMonthIdx !== null && selectedMonthIdx >= data.months.length) {
        setSelectedMonthIdx(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Произошла непредвиденная ошибка при загрузке данных.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchReport(monthsCount, scope);
  }, [monthsCount, scope]);

  // Экспорт CSV
  const handleExportCsv = async () => {
    setExporting(true);
    try {
      const url = `/api/v2/reports/export?format=csv&months=${monthsCount}&scope=${scope}`;
      const res = await fetch(url, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('Не удалось выгрузить CSV');
      const blob = await res.blob();
      const downloadUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.download = `support_report_${scope}_${monthsCount}m_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(downloadUrl);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Ошибка при экспорте отчёта');
    } finally {
      setExporting(false);
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!chartRef.current) return;
    const rect = chartRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    setTooltipPos({ x, y });
  };

  // Месяц в фокусе (если выбран)
  const activeFocusMonth: MonthLoadMetric | null = useMemo(() => {
    if (!report || selectedMonthIdx === null) return null;
    return report.months[selectedMonthIdx] || null;
  }, [report, selectedMonthIdx]);

  // Срез услуг для отображения (выбранного месяца или суммарный за период)
  const activeServiceBreakdown: ServiceShareMetric[] = useMemo(() => {
    if (!report) return [];
    if (activeFocusMonth) {
      return activeFocusMonth.service_breakdown || [];
    }
    return report.total_service_breakdown || [];
  }, [report, activeFocusMonth]);

  // Параметры шкалы диаграммы
  const chartConfig = useMemo(() => {
    if (!report || report.months.length === 0) return null;

    if (chartMode === 'volume') {
      const maxVal = Math.max(
        ...report.months.map((m) => Math.max(m.created, m.closed, m.projected_created || 0)),
        50
      );
      const yMax = Math.ceil((maxVal * 1.15) / 50) * 50;
      return { yMax, unit: '' };
    }

    if (chartMode === 'clear_rate') {
      const maxVal = Math.max(...report.months.map((m) => m.clear_rate), 120);
      const yMax = Math.ceil(maxVal / 10) * 10;
      return { yMax, unit: '%' };
    }

    if (chartMode === 'sla') {
      return { yMax: 100, unit: '%' };
    }

    return { yMax: 100, unit: '' };
  }, [report, chartMode]);

  // Сортировка таблицы ведомости
  const sortedMonths = useMemo(() => {
    if (!report) return [];
    const list = report.months.map((m, originalIdx) => ({ ...m, originalIdx }));
    list.sort((a, b) => {
      let valA = 0;
      let valB = 0;
      switch (sortKey) {
        case 'created':
          valA = a.created;
          valB = b.created;
          break;
        case 'closed':
          valA = a.closed;
          valB = b.closed;
          break;
        case 'balance':
          valA = a.balance;
          valB = b.balance;
          break;
        case 'clear_rate':
          valA = a.clear_rate;
          valB = b.clear_rate;
          break;
        case 'sla':
          valA = a.sla_resolution_rate;
          valB = b.sla_resolution_rate;
          break;
        case 'daily_workday':
          valA = a.daily_workday;
          valB = b.daily_workday;
          break;
        case 'date':
        default:
          valA = a.year * 100 + a.month;
          valB = b.year * 100 + b.month;
          break;
      }
      return sortDir === 'asc' ? valA - valB : valB - valA;
    });
    return list;
  }, [report, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  // Обработчик перехода в очередь по конкретной услуге или периоду
  const handleDrilldownClick = (filterTerm?: string) => {
    if (!onNavigateToQueue) return;
    if (filterTerm) {
      onNavigateToQueue(filterTerm);
    } else if (activeFocusMonth) {
      onNavigateToQueue(activeFocusMonth.period_label);
    } else {
      onNavigateToQueue();
    }
  };

  if (loading && !report) {
    return (
      <div className="h-full flex flex-col items-center justify-center p-8 bg-zinc-50 dark:bg-zinc-950 text-zinc-500 space-y-4">
        <svg className="w-8 h-8 animate-spin text-zinc-900 dark:text-zinc-100" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
          <path className="opacity-80" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
        <span className="text-xs font-mono font-medium tracking-wide uppercase text-zinc-600 dark:text-zinc-400">
          Сбор и агрегация аналитики…
        </span>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="h-full flex flex-col items-center justify-center p-8 bg-zinc-50 dark:bg-zinc-950 text-center">
        <div className="w-12 h-12 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-900/60 flex items-center justify-center text-rose-600 dark:text-rose-400 mb-4">
          <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        </div>
        <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100 mb-2">Ошибка формирования отчёта</h2>
        <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-md mb-6">{error || 'Не удалось получить данные.'}</p>
        <button
          onClick={() => fetchReport(monthsCount, scope, true)}
          className="px-4 py-2 bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 rounded-lg text-xs font-medium hover:bg-zinc-800 dark:hover:bg-zinc-200 transition-colors shadow-xs cursor-pointer"
        >
          Повторить запрос
        </button>
      </div>
    );
  }

  const latestMonth = report.months[report.months.length - 1];
  const tpLine1Pct = report.total_created > 0 ? Math.round((report.total_line1 / report.total_created) * 100) : 0;
  const tpLine2Pct = report.total_created > 0 ? Math.round((report.total_line2 / report.total_created) * 100) : 0;

  return (
    <div className="h-full overflow-y-auto bg-zinc-50/60 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100">
      <div className="max-w-[1560px] mx-auto p-4 sm:p-6 lg:p-8 space-y-6">

        {/* ============================================================== */}
        {/* 1. TOPBAR: Заголовок и фильтры-контролы (Linear style)         */}
        {/* ============================================================== */}
        <header className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-4 border-b border-zinc-200 dark:border-zinc-800">
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-lg sm:text-xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50">
                Аналитика нагрузки Helpdesk
              </h1>
              {report.from_cache && (
                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-mono font-medium bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-900/60">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Кэш Redis
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Поток заявок, пропускная способность, SLA и распределение по ключевым сервисам
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {/* Пресеты периода */}
            <div className="inline-flex items-center p-0.5 rounded-lg bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 text-xs">
              {[
                { label: '1 мес', count: 1 },
                { label: '3 мес', count: 3 },
                { label: '6 мес', count: 6 },
                { label: '12 мес', count: 12 },
              ].map((p) => (
                <button
                  key={p.count}
                  type="button"
                  onClick={() => {
                    setMonthsCount(p.count);
                    setSelectedMonthIdx(null);
                  }}
                  className={`px-2.5 py-1.5 rounded-md font-medium transition-all cursor-pointer ${
                    monthsCount === p.count
                      ? 'bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 shadow-2xs'
                      : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>

            {/* Контур техподдержки */}
            <div className="inline-flex items-center p-0.5 rounded-lg bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 text-xs">
              {[
                { label: 'Все линии', val: 'all' },
                { label: '1-я линия', val: 'line1' },
                { label: '2-я линия', val: 'line2' },
              ].map((s) => (
                <button
                  key={s.val}
                  type="button"
                  onClick={() => setScope(s.val as ScopeType)}
                  className={`px-2.5 py-1.5 rounded-md font-medium transition-all cursor-pointer ${
                    scope === s.val
                      ? 'bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 shadow-2xs'
                      : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>

            {/* Кнопка Обновить */}
            <button
              type="button"
              onClick={() => fetchReport(monthsCount, scope, true)}
              disabled={refreshing}
              title="Пересчитать актуальные данные"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 hover:bg-zinc-50 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300 text-xs font-medium transition-colors shadow-2xs disabled:opacity-50 cursor-pointer"
            >
              <svg className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin text-zinc-900 dark:text-zinc-100' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              <span>{refreshing ? 'Обновление…' : 'Обновить'}</span>
            </button>

            {/* Кнопка Экспорт CSV */}
            <button
              type="button"
              onClick={handleExportCsv}
              disabled={exporting}
              title="Выгрузить отчёт в формате CSV"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 hover:bg-zinc-800 dark:hover:bg-zinc-200 text-xs font-medium transition-colors shadow-2xs disabled:opacity-50 cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              <span>{exporting ? 'Экспорт…' : 'CSV'}</span>
            </button>
          </div>
        </header>

        {/* ============================================================== */}
        {/* 2. EXECUTIVE INSIGHTS RIBBON                                   */}
        {/* ============================================================== */}
        {report.insights && report.insights.length > 0 && (
          <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {report.insights.map((ins) => {
              const isPositive = ins.tone === 'positive';
              const isWarning = ins.tone === 'warning';
              const isNegative = ins.tone === 'negative';
              const isAccent = ins.tone === 'accent';

              return (
                <div
                  key={ins.id}
                  className="p-3.5 rounded-xl border border-zinc-200 dark:border-zinc-800/80 bg-white dark:bg-zinc-900/90 shadow-2xs hover:border-zinc-300 dark:hover:border-zinc-700 transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-semibold tracking-wide uppercase text-zinc-500 dark:text-zinc-400">
                      {ins.title}
                    </span>
                    <span
                      className={`w-2 h-2 rounded-full ${
                        isAccent
                          ? 'bg-blue-500 ring-2 ring-blue-500/20'
                          : isPositive
                          ? 'bg-emerald-500 ring-2 ring-emerald-500/20'
                          : isWarning
                          ? 'bg-amber-500 ring-2 ring-amber-500/20'
                          : isNegative
                          ? 'bg-rose-500 ring-2 ring-rose-500/20'
                          : 'bg-zinc-400'
                      }`}
                    />
                  </div>
                  <div className="mt-1.5 text-lg font-bold font-mono tracking-tight text-zinc-900 dark:text-zinc-100">
                    {ins.value}
                  </div>
                  <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400 leading-normal line-clamp-2">
                    {ins.description}
                  </p>
                </div>
              );
            })}
          </section>
        )}

        {/* ============================================================== */}
        {/* 3. КЛЮЧЕВЫЕ KPI С MoM ДИНАМИКОЙ (+X% ↑)                        */}
        {/* ============================================================== */}
        <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
          {/* 1. Поступило всего */}
          <div className="p-4 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shadow-2xs">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Поступило заявок</span>
              {report.created_mom_delta_pct !== null && report.created_mom_delta_pct !== undefined && (
                <span
                  className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[11px] font-mono font-medium ${
                    report.created_mom_delta_pct > 0
                      ? 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300'
                      : 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300'
                  }`}
                  title="Динамика последнего месяца к предыдущему (MoM)"
                >
                  <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    {report.created_mom_delta_pct > 0 ? (
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
                    ) : (
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    )}
                  </svg>
                  {report.created_mom_delta_pct > 0 ? `+${report.created_mom_delta_pct}%` : `${report.created_mom_delta_pct}%`} MoM
                </span>
              )}
            </div>
            <div className="mt-2 text-2xl font-bold font-mono tracking-tight text-zinc-900 dark:text-zinc-50">
              {report.total_created.toLocaleString('ru-RU')}
            </div>
            <div className="mt-2 text-[11px] text-zinc-500 dark:text-zinc-400 flex items-center gap-2">
              <span>Л1: <strong className="font-mono text-zinc-800 dark:text-zinc-200">{report.total_line1.toLocaleString('ru-RU')}</strong> ({tpLine1Pct}%)</span>
              <span>•</span>
              <span>Л2: <strong className="font-mono text-zinc-800 dark:text-zinc-200">{report.total_line2.toLocaleString('ru-RU')}</strong> ({tpLine2Pct}%)</span>
            </div>
          </div>

          {/* 2. Закрыто / Выполнено */}
          <div className="p-4 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shadow-2xs">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Закрыто / Выполнено</span>
              <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400">
                Clear Rate: {report.overall_clear_rate}%
              </span>
            </div>
            <div className="mt-2 text-2xl font-bold font-mono tracking-tight text-emerald-600 dark:text-emerald-400">
              {report.total_closed.toLocaleString('ru-RU')}
            </div>
            <div className="mt-2 text-[11px] text-zinc-500 dark:text-zinc-400">
              Баланс остатка: <strong className={`font-mono ${report.total_balance <= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}>
                {report.total_balance > 0 ? `+${report.total_balance}` : report.total_balance}
              </strong> ({report.total_balance <= 0 ? 'сокращение очереди' : 'рост очереди'})
            </div>
          </div>

          {/* 3. Соблюдение SLA */}
          <div className="p-4 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shadow-2xs">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">SLA Решения заявок</span>
              <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono font-medium ${
                report.overall_sla_resolution_rate >= 95
                  ? 'bg-blue-50 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300'
                  : 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300'
              }`}>
                {report.overall_sla_resolution_rate >= 95 ? 'Норматив 95%+' : 'Ниже нормы'}
              </span>
            </div>
            <div className="mt-2 text-2xl font-bold font-mono tracking-tight text-zinc-900 dark:text-zinc-50">
              {report.overall_sla_resolution_rate}%
            </div>
            <div className="mt-2 text-[11px] text-zinc-500 dark:text-zinc-400">
              Нарушений срока: <strong className="font-mono text-zinc-800 dark:text-zinc-200">{report.total_overdue_resolution}</strong> заявок
            </div>
          </div>

          {/* 4. Среднедневной темп */}
          <div className="p-4 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shadow-2xs">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">В рабочий день</span>
              <span className="text-[11px] font-mono text-zinc-500 dark:text-zinc-400">
                Закрытие: ~{report.avg_daily_closed_workday}/дн
              </span>
            </div>
            <div className="mt-2 text-2xl font-bold font-mono tracking-tight text-zinc-900 dark:text-zinc-50">
              ~{report.avg_daily_workday}
              <span className="text-sm font-normal text-zinc-400 dark:text-zinc-500 ml-1">поступл./дн</span>
            </div>
            <div className="mt-2 text-[11px] text-zinc-500 dark:text-zinc-400">
              В среднем <strong className="font-mono text-zinc-800 dark:text-zinc-200">~{report.avg_monthly_created}</strong> заявок в месяц
            </div>
          </div>
        </section>

        {/* ============================================================== */}
        {/* 4. ЦЕНТРАЛЬНЫЙ СПЛИТ: ГРАФИК (65%) + СРЕЗ СЕРВИСОВ (35%)       */}
        {/* ============================================================== */}
        <section className="grid grid-cols-1 lg:grid-cols-12 gap-5">

          {/* ЛЕВАЯ КОЛОНКА (65% = 8 колонок): АДАПТИВНЫЙ ИНТЕРАКТИВНЫЙ SVG ГРАФИК */}
          <div className="lg:col-span-8 p-5 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shadow-2xs flex flex-col justify-between">
            <div>
              {/* Верхняя плашка управления графиком */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-zinc-100 dark:border-zinc-800">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    Динамика показателей
                  </h3>
                  <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                    Нажмите на столбец для фиксации среза месяца
                  </p>
                </div>

                <div className="flex items-center gap-1 p-0.5 rounded-lg bg-zinc-100 dark:bg-zinc-800 text-xs">
                  <button
                    type="button"
                    onClick={() => setChartMode('volume')}
                    className={`px-2.5 py-1 rounded-md font-medium transition-all cursor-pointer ${
                      chartMode === 'volume'
                        ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs'
                        : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                    }`}
                  >
                    Объёмы
                  </button>
                  <button
                    type="button"
                    onClick={() => setChartMode('clear_rate')}
                    className={`px-2.5 py-1 rounded-md font-medium transition-all cursor-pointer ${
                      chartMode === 'clear_rate'
                        ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs'
                        : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                    }`}
                  >
                    % Clear Rate
                  </button>
                  <button
                    type="button"
                    onClick={() => setChartMode('sla')}
                    className={`px-2.5 py-1 rounded-md font-medium transition-all cursor-pointer ${
                      chartMode === 'sla'
                        ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs'
                        : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                    }`}
                  >
                    SLA %
                  </button>
                </div>
              </div>

              {/* Легенда диаграммы */}
              <div className="flex flex-wrap items-center gap-4 mt-3 text-xs text-zinc-500 dark:text-zinc-400">
                {chartMode === 'volume' && (
                  <>
                    <div className="flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-xs bg-blue-600" />
                      <span>1-я линия</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-xs bg-indigo-500" />
                      <span>2-я линия</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="w-2.5 h-0.5 bg-emerald-500 rounded-full" />
                      <span>Закрыто</span>
                    </div>
                    {latestMonth?.is_current && (
                      <div className="flex items-center gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-xs border border-dashed border-blue-400 bg-blue-50 dark:bg-blue-950/40" />
                        <span>Прогноз текущего мес.</span>
                      </div>
                    )}
                  </>
                )}
                {chartMode === 'clear_rate' && (
                  <>
                    <div className="flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-xs bg-emerald-600" />
                      <span>Выполнение %</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="w-3 h-0.5 border-t border-dashed border-emerald-500" />
                      <span>Целевой баланс 100%</span>
                    </div>
                  </>
                )}
                {chartMode === 'sla' && (
                  <>
                    <div className="flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-xs bg-blue-600" />
                      <span>SLA в срок %</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="w-3 h-0.5 border-t border-dashed border-blue-500" />
                      <span>Норматив 95%</span>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Холст SVG графика (без горизонтального скролла, viewBox 0 0 1000 240) */}
            <div
              ref={chartRef}
              onMouseMove={handleMouseMove}
              onMouseLeave={() => setHoveredIdx(null)}
              className="relative mt-4 w-full select-none"
            >
              {chartConfig && (
                <svg
                  viewBox="0 0 1000 240"
                  className="w-full h-auto max-h-[280px] overflow-visible"
                >
                  {/* Горизонтальные сетки и отметки Y */}
                  {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
                    const y = 200 - ratio * 170;
                    const val = Math.round(chartConfig.yMax * ratio);
                    return (
                      <g key={ratio}>
                        <line
                          x1="45"
                          y1={y}
                          x2="980"
                          y2={y}
                          stroke="currentColor"
                          strokeDasharray="4 4"
                          className="text-zinc-200 dark:text-zinc-800"
                        />
                        <text
                          x="38"
                          y={y + 3}
                          textAnchor="end"
                          className="text-[10px] font-mono fill-zinc-400 dark:fill-zinc-500"
                        >
                          {val}{chartConfig.unit}
                        </text>
                      </g>
                    );
                  })}

                  {/* Опорная линия 100% для Clear Rate */}
                  {chartMode === 'clear_rate' && (
                    <line
                      x1="45"
                      y1={200 - (100 / chartConfig.yMax) * 170}
                      x2="980"
                      y2={200 - (100 / chartConfig.yMax) * 170}
                      stroke="#10b981"
                      strokeDasharray="5 3"
                      strokeWidth="1.5"
                    />
                  )}

                  {/* Опорная линия 95% для SLA */}
                  {chartMode === 'sla' && (
                    <line
                      x1="45"
                      y1={200 - (95 / chartConfig.yMax) * 170}
                      x2="980"
                      y2={200 - (95 / chartConfig.yMax) * 170}
                      stroke="#2563eb"
                      strokeDasharray="5 3"
                      strokeWidth="1.5"
                    />
                  )}

                  {/* Столбцы месяцев */}
                  {(() => {
                    const count = report.months.length;
                    const chartWidth = 920;
                    const step = chartWidth / count;
                    const colWidth = Math.min(step * 0.55, 48);

                    return report.months.map((m, idx) => {
                      const centerX = 55 + idx * step + step / 2;
                      const x = centerX - colWidth / 2;
                      const isHovered = hoveredIdx === idx;
                      const isSelected = selectedMonthIdx === idx;

                      if (chartMode === 'volume') {
                        const h1 = (m.created_line1 / chartConfig.yMax) * 170;
                        const h2 = (m.created_line2 / chartConfig.yMax) * 170;
                        const y1 = 200 - h1;
                        const y2 = y1 - h2;

                        const yClosed = 200 - (m.closed / chartConfig.yMax) * 170;

                        return (
                          <g
                            key={idx}
                            className="cursor-pointer transition-opacity"
                            onClick={() => setSelectedMonthIdx(isSelected ? null : idx)}
                            onMouseEnter={() => setHoveredIdx(idx)}
                          >
                            {/* Фоновая подсветка выбранного или наведенного месяца */}
                            {(isSelected || isHovered) && (
                              <rect
                                x={centerX - step * 0.45}
                                y={20}
                                width={step * 0.9}
                                height={200}
                                rx="6"
                                className={isSelected ? 'fill-blue-500/10 stroke-blue-500/30' : 'fill-zinc-500/5'}
                              />
                            )}

                            {/* Столбец 1-й линии */}
                            <rect
                              x={x}
                              y={y1}
                              width={colWidth}
                              height={Math.max(h1, 0)}
                              rx="3"
                              className="fill-blue-600 hover:fill-blue-500 transition-colors"
                            />

                            {/* Стек: столбец 2-й линии */}
                            {h2 > 0 && (
                              <rect
                                x={x}
                                y={y2}
                                width={colWidth}
                                height={Math.max(h2, 0)}
                                rx="3"
                                className="fill-indigo-500 hover:fill-indigo-400 transition-colors"
                              />
                            )}

                            {/* Маркер закрытия (горизонтальная засечка) */}
                            <line
                              x1={x - 4}
                              y1={yClosed}
                              x2={x + colWidth + 4}
                              y2={yClosed}
                              stroke="#10b981"
                              strokeWidth="2.5"
                              strokeLinecap="round"
                            />

                            {/* Подпись месяца под осью X */}
                            <text
                              x={centerX}
                              y="222"
                              textAnchor="middle"
                              className={`text-[10px] font-mono ${
                                isSelected
                                  ? 'font-bold fill-blue-600 dark:fill-blue-400'
                                  : 'fill-zinc-500 dark:fill-zinc-400'
                              }`}
                            >
                              {m.month_name.slice(0, 3)}
                              {m.is_current ? '*' : ''}
                            </text>
                          </g>
                        );
                      }

                      if (chartMode === 'clear_rate') {
                        const h = (Math.min(m.clear_rate, chartConfig.yMax) / chartConfig.yMax) * 170;
                        const y = 200 - h;
                        const isGood = m.clear_rate >= 100;

                        return (
                          <g
                            key={idx}
                            className="cursor-pointer transition-opacity"
                            onClick={() => setSelectedMonthIdx(isSelected ? null : idx)}
                            onMouseEnter={() => setHoveredIdx(idx)}
                          >
                            {(isSelected || isHovered) && (
                              <rect
                                x={centerX - step * 0.45}
                                y={20}
                                width={step * 0.9}
                                height={200}
                                rx="6"
                                className={isSelected ? 'fill-blue-500/10 stroke-blue-500/30' : 'fill-zinc-500/5'}
                              />
                            )}

                            <rect
                              x={x}
                              y={y}
                              width={colWidth}
                              height={Math.max(h, 0)}
                              rx="3"
                              className={isGood ? 'fill-emerald-600 hover:fill-emerald-500' : 'fill-amber-500 hover:fill-amber-400'}
                            />

                            <text
                              x={centerX}
                              y="222"
                              textAnchor="middle"
                              className={`text-[10px] font-mono ${
                                isSelected
                                  ? 'font-bold fill-blue-600 dark:fill-blue-400'
                                  : 'fill-zinc-500 dark:fill-zinc-400'
                              }`}
                            >
                              {m.month_name.slice(0, 3)}
                            </text>
                          </g>
                        );
                      }

                      if (chartMode === 'sla') {
                        const h = (m.sla_resolution_rate / 100) * 170;
                        const y = 200 - h;
                        const isOk = m.sla_resolution_rate >= 95;

                        return (
                          <g
                            key={idx}
                            className="cursor-pointer transition-opacity"
                            onClick={() => setSelectedMonthIdx(isSelected ? null : idx)}
                            onMouseEnter={() => setHoveredIdx(idx)}
                          >
                            {(isSelected || isHovered) && (
                              <rect
                                x={centerX - step * 0.45}
                                y={20}
                                width={step * 0.9}
                                height={200}
                                rx="6"
                                className={isSelected ? 'fill-blue-500/10 stroke-blue-500/30' : 'fill-zinc-500/5'}
                              />
                            )}

                            <rect
                              x={x}
                              y={y}
                              width={colWidth}
                              height={Math.max(h, 0)}
                              rx="3"
                              className={isOk ? 'fill-blue-600 hover:fill-blue-500' : 'fill-rose-500 hover:fill-rose-400'}
                            />

                            <text
                              x={centerX}
                              y="222"
                              textAnchor="middle"
                              className={`text-[10px] font-mono ${
                                isSelected
                                  ? 'font-bold fill-blue-600 dark:fill-blue-400'
                                  : 'fill-zinc-500 dark:fill-zinc-400'
                              }`}
                            >
                              {m.month_name.slice(0, 3)}
                            </text>
                          </g>
                        );
                      }

                      return null;
                    });
                  })()}
                </svg>
              )}

              {/* Плавающий тултип при наведении */}
              {hoveredIdx !== null && tooltipPos && report.months[hoveredIdx] && (
                <div
                  style={{
                    left: `${Math.min(Math.max(tooltipPos.x, 120), (chartRef.current?.clientWidth || 700) - 130)}px`,
                    top: `${Math.max(tooltipPos.y - 120, 10)}px`,
                  }}
                  className="pointer-events-none absolute -translate-x-1/2 z-20 w-56 rounded-lg bg-zinc-900/95 dark:bg-zinc-800/95 p-3 text-white shadow-xl backdrop-blur-xs border border-zinc-700 text-xs"
                >
                  <div className="font-semibold text-zinc-100 flex items-center justify-between border-b border-zinc-700 pb-1.5">
                    <span>{report.months[hoveredIdx].period_label}</span>
                    {report.months[hoveredIdx].is_current && (
                      <span className="text-[10px] text-amber-400 font-mono">тек. срез</span>
                    )}
                  </div>
                  <div className="mt-2 space-y-1 font-mono text-[11px]">
                    <div className="flex justify-between text-zinc-300">
                      <span>Создано:</span>
                      <strong className="text-white">{report.months[hoveredIdx].created}</strong>
                    </div>
                    <div className="flex justify-between text-zinc-400 pl-2">
                      <span>1-я линия:</span>
                      <span>{report.months[hoveredIdx].created_line1}</span>
                    </div>
                    <div className="flex justify-between text-zinc-400 pl-2">
                      <span>2-я линия:</span>
                      <span>{report.months[hoveredIdx].created_line2}</span>
                    </div>
                    <div className="flex justify-between text-emerald-400">
                      <span>Закрыто:</span>
                      <strong>{report.months[hoveredIdx].closed}</strong>
                    </div>
                    <div className="flex justify-between text-zinc-300">
                      <span>Clear Rate:</span>
                      <strong className={report.months[hoveredIdx].clear_rate >= 100 ? 'text-emerald-400' : 'text-amber-400'}>
                        {report.months[hoveredIdx].clear_rate}%
                      </strong>
                    </div>
                    <div className="flex justify-between text-zinc-300">
                      <span>SLA решения:</span>
                      <strong className={report.months[hoveredIdx].sla_resolution_rate >= 95 ? 'text-blue-400' : 'text-rose-400'}>
                        {report.months[hoveredIdx].sla_resolution_rate}%
                      </strong>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Подвал графика: подсказка фокуса */}
            <div className="mt-3 pt-3 border-t border-zinc-100 dark:border-zinc-800 flex items-center justify-between text-[11px] text-zinc-500 dark:text-zinc-400">
              <div>
                {selectedMonthIdx !== null ? (
                  <span>
                    Выбран месяц: <strong className="text-zinc-900 dark:text-zinc-100">{report.months[selectedMonthIdx]?.period_label}</strong>
                  </span>
                ) : (
                  <span>Отображаются сводные данные за весь период ({monthsCount} мес)</span>
                )}
              </div>
              {selectedMonthIdx !== null && (
                <button
                  type="button"
                  onClick={() => setSelectedMonthIdx(null)}
                  className="text-blue-600 dark:text-blue-400 hover:underline font-medium cursor-pointer"
                >
                  Сбросить выбор (показать весь период)
                </button>
              )}
            </div>
          </div>

          {/* ПРАВАЯ КОЛОНКА (35% = 4 колонки): СРЕЗ СЕРВИСОВ (ROOT-CAUSE BREAKDOWN) */}
          <div className="lg:col-span-4 p-5 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shadow-2xs flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-3 border-b border-zinc-100 dark:border-zinc-800">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    {activeFocusMonth ? `Срез: ${activeFocusMonth.period_label}` : `Структура за ${monthsCount} мес`}
                  </h3>
                  <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                    {activeFocusMonth ? 'Категории выбранного месяца' : 'Корневые направления потока обращений'}
                  </p>
                </div>
                {activeFocusMonth && (
                  <button
                    type="button"
                    onClick={() => setSelectedMonthIdx(null)}
                    className="p-1 rounded text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 text-xs"
                    title="Сбросить к общему периоду"
                  >
                    ✕
                  </button>
                )}
              </div>

              {/* Список сервисов с прогресс-барами */}
              <div className="mt-4 space-y-3">
                {activeServiceBreakdown.length > 0 ? (
                  activeServiceBreakdown.map((item) => {
                    const color = SERVICE_COLORS[item.key] || { bg: 'bg-zinc-500', fill: '#71717a', text: 'text-zinc-500' };

                    return (
                      <div
                        key={item.key}
                        onClick={() => handleDrilldownClick(item.name.split(' ')[0])}
                        title={`Нажмите, чтобы открыть заявки категории «${item.name}» в очереди`}
                        className="group p-2 -mx-2 rounded-lg hover:bg-zinc-50 dark:hover:bg-zinc-800/60 transition-colors cursor-pointer"
                      >
                        <div className="flex items-center justify-between text-xs mb-1.5">
                          <span className="font-medium text-zinc-700 dark:text-zinc-300 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors flex items-center gap-1.5">
                            <span className={`w-2 h-2 rounded-full ${color.bg}`} />
                            {item.name}
                          </span>
                          <div className="flex items-center gap-2 font-mono text-[11px]">
                            <span className="text-zinc-900 dark:text-zinc-100 font-semibold">
                              {item.count.toLocaleString('ru-RU')}
                            </span>
                            <span className="text-zinc-400 dark:text-zinc-500">
                              ({item.share_pct}%)
                            </span>
                          </div>
                        </div>

                        {/* Горизонтальный индикатор доли */}
                        <div className="w-full h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${color.bg} transition-all duration-500`}
                            style={{ width: `${Math.min(item.share_pct, 100)}%` }}
                          />
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="py-8 text-center text-xs text-zinc-400">
                    Данные о сервисах отсутствуют
                  </div>
                )}
              </div>
            </div>

            {/* Быстрый Drill-down переход в очередь заявок */}
            <div className="mt-6 pt-4 border-t border-zinc-100 dark:border-zinc-800">
              <button
                type="button"
                onClick={() => handleDrilldownClick()}
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 hover:bg-zinc-800 dark:hover:bg-zinc-200 text-xs font-semibold transition-all shadow-xs cursor-pointer group"
              >
                <svg className="w-4 h-4 text-zinc-400 group-hover:text-zinc-200 dark:group-hover:text-zinc-700 transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <span>
                  {activeFocusMonth
                    ? `Показать заявки за ${activeFocusMonth.period_label} в очереди`
                    : 'Показать все заявки периода в очереди'}
                </span>
                <svg className="w-3.5 h-3.5 transform group-hover:translate-x-0.5 transition-transform" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
              </button>
            </div>
          </div>

        </section>

        {/* ============================================================== */}
        {/* 5. ПОЛНАЯ ВЕДОМОСТЬ ПО МЕСЯЦАМ (СОРТИРУЕМАЯ ТАБЛИЦА)            */}
        {/* ============================================================== */}
        <section className="p-5 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shadow-2xs">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-4 border-b border-zinc-100 dark:border-zinc-800">
            <div>
              <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Ежемесячная ведомость обращений
              </h3>
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Детальный баланс закрытия, SLA и распределение по линиям ТП. Нажмите на строку для фокусировки.
              </p>
            </div>
            <span className="text-xs font-mono text-zinc-400 dark:text-zinc-500">
              Строк: {report.months.length}
            </span>
          </div>

          <div className="overflow-x-auto mt-2">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="border-b border-zinc-200 dark:border-zinc-800 text-[11px] font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider select-none">
                  <th
                    className="py-3 px-3 cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors"
                    onClick={() => toggleSort('date')}
                  >
                    Период {sortKey === 'date' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th className="py-3 px-3 text-right">Раб. дней</th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors"
                    onClick={() => toggleSort('created')}
                  >
                    Создано {sortKey === 'created' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th className="py-3 px-3 text-right">1-я линия</th>
                  <th className="py-3 px-3 text-right">2-я линия</th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors"
                    onClick={() => toggleSort('closed')}
                  >
                    Закрыто {sortKey === 'closed' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors"
                    onClick={() => toggleSort('balance')}
                  >
                    Баланс {sortKey === 'balance' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors"
                    onClick={() => toggleSort('clear_rate')}
                  >
                    Clear Rate {sortKey === 'clear_rate' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors"
                    onClick={() => toggleSort('sla')}
                  >
                    SLA % {sortKey === 'sla' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors"
                    onClick={() => toggleSort('daily_workday')}
                  >
                    В день {sortKey === 'daily_workday' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th className="py-3 px-3 text-center">Очередь</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800/60 font-mono text-[11px]">
                {sortedMonths.map((m) => {
                  const isSelected = selectedMonthIdx === m.originalIdx;
                  const isPeak = m.period_label === report.peak_month;
                  const line1Pct = m.created > 0 ? Math.round((m.created_line1 / m.created) * 100) : 0;
                  const line2Pct = m.created > 0 ? Math.round((m.created_line2 / m.created) * 100) : 0;

                  return (
                    <tr
                      key={`${m.year}-${m.month}`}
                      onClick={() => setSelectedMonthIdx(isSelected ? null : m.originalIdx)}
                      className={`cursor-pointer transition-colors ${
                        isSelected
                          ? 'bg-blue-50/80 dark:bg-blue-950/30 text-blue-950 dark:text-blue-100'
                          : 'hover:bg-zinc-50 dark:hover:bg-zinc-800/40 text-zinc-700 dark:text-zinc-300'
                      }`}
                    >
                      {/* Период */}
                      <td className="py-2.5 px-3 font-medium">
                        <div className="flex items-center gap-1.5 font-sans">
                          <span>{m.period_label}</span>
                          {m.is_current && (
                            <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-blue-100 dark:bg-blue-950 text-blue-700 dark:text-blue-300">
                              тек.
                            </span>
                          )}
                          {isPeak && (
                            <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-300 font-bold">
                              ПИК
                            </span>
                          )}
                        </div>
                      </td>

                      {/* Раб. дней */}
                      <td className="py-2.5 px-3 text-right text-zinc-500 dark:text-zinc-400">
                        {m.workdays}
                      </td>

                      {/* Создано */}
                      <td className="py-2.5 px-3 text-right font-semibold text-zinc-900 dark:text-zinc-100">
                        <div className="flex items-center justify-end gap-1.5">
                          <span>{m.created.toLocaleString('ru-RU')}</span>
                          {m.mom_delta_pct !== null && m.mom_delta_pct !== undefined && (
                            <span
                              className={`text-[10px] ${
                                m.mom_delta_pct > 0 ? 'text-amber-500' : 'text-emerald-500'
                              }`}
                              title="Отклонение от предшествующего месяца"
                            >
                              {m.mom_delta_pct > 0 ? `+${m.mom_delta_pct}%` : `${m.mom_delta_pct}%`}
                            </span>
                          )}
                        </div>
                      </td>

                      {/* 1-я линия */}
                      <td className="py-2.5 px-3 text-right text-zinc-600 dark:text-zinc-400">
                        {m.created_line1.toLocaleString('ru-RU')} <span className="text-zinc-400 text-[10px]">({line1Pct}%)</span>
                      </td>

                      {/* 2-я линия */}
                      <td className="py-2.5 px-3 text-right text-zinc-600 dark:text-zinc-400">
                        {m.created_line2.toLocaleString('ru-RU')} <span className="text-zinc-400 text-[10px]">({line2Pct}%)</span>
                      </td>

                      {/* Закрыто */}
                      <td className="py-2.5 px-3 text-right font-semibold text-emerald-600 dark:text-emerald-400">
                        {m.closed.toLocaleString('ru-RU')}
                      </td>

                      {/* Баланс */}
                      <td className="py-2.5 px-3 text-right">
                        <span
                          className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            m.balance <= 0
                              ? 'bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-400'
                              : 'bg-rose-50 dark:bg-rose-950/50 text-rose-700 dark:text-rose-400'
                          }`}
                        >
                          {m.balance > 0 ? `+${m.balance}` : m.balance}
                        </span>
                      </td>

                      {/* Clear Rate */}
                      <td className="py-2.5 px-3 text-right">
                        <span
                          className={`font-semibold ${
                            m.clear_rate >= 100
                              ? 'text-emerald-600 dark:text-emerald-400'
                              : 'text-amber-600 dark:text-amber-400'
                          }`}
                        >
                          {m.clear_rate}%
                        </span>
                      </td>

                      {/* SLA % */}
                      <td className="py-2.5 px-3 text-right">
                        <span
                          className={`font-semibold ${
                            m.sla_resolution_rate >= 95
                              ? 'text-zinc-800 dark:text-zinc-200'
                              : 'text-rose-600 dark:text-rose-400'
                          }`}
                        >
                          {m.sla_resolution_rate}%
                        </span>
                        {m.overdue_resolution > 0 && (
                          <span className="text-rose-500 text-[10px] ml-1">
                            ({m.overdue_resolution})
                          </span>
                        )}
                      </td>

                      {/* В день */}
                      <td className="py-2.5 px-3 text-right text-zinc-600 dark:text-zinc-400">
                        ~{m.daily_workday}
                      </td>

                      {/* Кнопка перехода в очередь */}
                      <td className="py-2.5 px-3 text-center">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDrilldownClick(m.period_label);
                          }}
                          title={`Открыть заявки ${m.period_label} в очереди`}
                          className="p-1 rounded text-zinc-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                        >
                          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

      </div>
    </div>
  );
}
