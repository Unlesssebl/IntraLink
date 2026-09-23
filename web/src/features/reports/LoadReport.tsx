import React, { useState, useEffect } from "react";
import { BarChart3, Download, RefreshCw, Users, Layers, Clock } from "lucide-react";
import { Button, Card } from "@/shared/ui";
import { reportsApi, LoadReport as ILoadReport } from "@/shared/api";

export const LoadReport: React.FC = () => {
  const currentDate = new Date();
  const [year, setYear] = useState(currentDate.getFullYear());
  const [month, setMonth] = useState(currentDate.getMonth() + 1);

  const [data, setData] = useState<ILoadReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchReport = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await reportsApi.getLoad(year, month);
      setData(res);
    } catch (err: any) {
      setError(err?.message || "Ошибка загрузки отчета нагрузки");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReport();
  }, [year, month]);

  const handleExport = () => {
    try {
      setExporting(true);
      const url = reportsApi.getExportUrl(year, month);
      window.open(url, "_blank");
    } catch (err: any) {
      alert(`Ошибка экспорта: ${err?.message}`);
    } finally {
      setExporting(false);
    }
  };

  const months = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
  ];

  const engineerEntries =
    data?.engineer_load && Object.keys(data.engineer_load).length > 0
      ? Object.entries(data.engineer_load)
      : (data?.engineers || []).map((e) => [e.user_name, e.total_closed] as [string, number]);

  const serviceEntries = Object.entries(data?.service_load || {});
  const totalTickets =
    data?.total_tickets ??
    (data?.engineers || []).reduce((acc, e) => acc + e.total_assigned, 0);
  const closedTickets =
    data?.closed_tickets ??
    (data?.engineers || []).reduce((acc, e) => acc + e.total_closed, 0);
  const avgResolutionHours = data?.avg_resolution_hours ?? 0;

  return (
    <div className="space-y-4 max-w-5xl mx-auto">
      {/* Header with Filters */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-neutral-100 flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-neutral-400" />
            Аналитика нагрузки Helpdesk
          </h2>
          <p className="text-xs text-neutral-400 mt-0.5">
            Сводная статистика по инженерам и категориям каталога
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Month selector */}
          <select
            value={month}
            onChange={(e) => setMonth(Number(e.target.value))}
            className="bg-[#121316] border border-neutral-800 rounded px-2.5 py-1 text-xs text-neutral-100 focus:outline-none focus:ring-1 focus:ring-neutral-400 focus:border-neutral-500"
          >
            {months.map((name, idx) => (
              <option key={idx + 1} value={idx + 1}>
                {name}
              </option>
            ))}
          </select>

          {/* Year selector */}
          <select
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            className="bg-[#121316] border border-neutral-800 rounded px-2.5 py-1 text-xs text-neutral-100 focus:outline-none focus:ring-1 focus:ring-neutral-400 focus:border-neutral-500 font-mono"
          >
            <option value={2026}>2026</option>
            <option value={2025}>2025</option>
          </select>

          <Button
            size="sm"
            variant="secondary"
            loading={loading}
            icon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={fetchReport}
          >
            Обновить
          </Button>

          <Button
            size="sm"
            variant="primary"
            loading={exporting}
            icon={<Download className="w-3.5 h-3.5" />}
            onClick={handleExport}
          >
            Экспорт CSV
          </Button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-rose-950/40 border border-rose-800/60 rounded text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* KPI Cards */}
      {data && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Card className="bg-[#121316] border border-neutral-800/80">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-[11px] text-neutral-400 font-medium">
                  Всего поступило
                </span>
                <div className="text-xl font-bold font-mono text-neutral-100 mt-1">
                  {totalTickets}
                </div>
              </div>
              <Layers className="w-6 h-6 text-neutral-500" />
            </div>
          </Card>

          <Card className="bg-[#121316] border border-neutral-800/80">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-[11px] text-neutral-400 font-medium">
                  Успешно решено
                </span>
                <div className="text-xl font-bold font-mono text-emerald-400 mt-1">
                  {closedTickets}
                </div>
              </div>
              <Users className="w-6 h-6 text-emerald-500/80" />
            </div>
          </Card>

          <Card className="bg-[#121316] border border-neutral-800/80">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-[11px] text-neutral-400 font-medium">
                  Среднее время решения
                </span>
                <div className="text-xl font-bold font-mono text-amber-400 mt-1">
                  {avgResolutionHours} ч
                </div>
              </div>
              <Clock className="w-6 h-6 text-amber-500/80" />
            </div>
          </Card>
        </div>
      )}

      {/* Detailed breakdowns */}
      {data && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Engineers Load */}
          <Card
            className="bg-[#121316] border border-neutral-800/80"
            title={
              <div className="flex items-center gap-2">
                <Users className="w-4 h-4 text-neutral-400" />
                <span>Нагрузка по инженерам</span>
              </div>
            }
          >
            <div className="space-y-2.5">
              {engineerEntries.length === 0 ? (
                <div className="text-xs text-neutral-500 py-3 text-center">
                  Нет данных по инженерам
                </div>
              ) : (
                engineerEntries.map(([name, count]) => {
                  const percent =
                    closedTickets > 0
                      ? Math.round((count / closedTickets) * 100)
                      : 0;
                  return (
                    <div key={name} className="space-y-1 text-xs">
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-200 font-medium">{name}</span>
                        <span className="font-mono text-neutral-400">
                          {count} ({percent}%)
                        </span>
                      </div>
                      <div className="h-1.5 w-full bg-neutral-800 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-neutral-200 rounded-full"
                          style={{ width: `${percent}%` }}
                        />
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </Card>

          {/* Service Categories Load */}
          <Card
            className="bg-[#121316] border border-neutral-800/80"
            title={
              <div className="flex items-center gap-2">
                <Layers className="w-4 h-4 text-neutral-400" />
                <span>Нагрузка по категориям сервисов</span>
              </div>
            }
          >
            <div className="space-y-2.5">
              {serviceEntries.length === 0 ? (
                <div className="text-xs text-neutral-500 py-3 text-center">
                  Нет данных по категориям
                </div>
              ) : (
                serviceEntries.map(([service, count]) => {
                  const percent =
                    totalTickets > 0
                      ? Math.round((count / totalTickets) * 100)
                      : 0;
                  return (
                    <div key={service} className="space-y-1 text-xs">
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-200 truncate pr-2">{service}</span>
                        <span className="font-mono text-neutral-400 shrink-0">
                          {count} ({percent}%)
                        </span>
                      </div>
                      <div className="h-1.5 w-full bg-neutral-800 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-neutral-400 rounded-full"
                          style={{ width: `${percent}%` }}
                        />
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </Card>
        </div>
      )}

      {/* Detailed Engineer Table if engineers metrics available */}
      {data?.engineers && data.engineers.length > 0 && (
        <Card
          className="bg-[#121316] border border-neutral-800/80"
          title={
            <div className="flex items-center gap-2">
              <Users className="w-4 h-4 text-neutral-400" />
              <span>Показатели производительности инженеров</span>
            </div>
          }
        >
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-left">
              <thead>
                <tr className="border-b border-neutral-800 text-neutral-400">
                  <th className="pb-2 font-medium">Инженер</th>
                  <th className="pb-2 font-medium text-right">Назначено</th>
                  <th className="pb-2 font-medium text-right">Закрыто</th>
                  <th className="pb-2 font-medium text-right">Ср. время (ч)</th>
                  <th className="pb-2 font-medium text-right">Возвраты</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-800/60">
                {data.engineers.map((e) => (
                  <tr key={e.user_id} className="hover:bg-white/[0.04]">
                    <td className="py-2 text-neutral-200 font-medium">{e.user_name}</td>
                    <td className="py-2 text-right font-mono text-neutral-400">{e.total_assigned}</td>
                    <td className="py-2 text-right font-mono text-emerald-400">{e.total_closed}</td>
                    <td className="py-2 text-right font-mono text-amber-400">{e.avg_resolution_hours}</td>
                    <td className="py-2 text-right font-mono text-rose-400">{e.reopened_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
};
