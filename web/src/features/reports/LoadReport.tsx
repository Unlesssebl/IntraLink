import React, { useState, useEffect } from "react";
import { BarChart3, Download, RefreshCw, Users, Layers, Clock } from "lucide-react";
import { Button, Card, Badge } from "@/shared/ui";
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

  const handleExport = async () => {
    try {
      setExporting(true);
      const res = await reportsApi.exportLoad(year, month, "csv");
      window.open(res.download_url, "_blank");
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

  return (
    <div className="space-y-4 max-w-5xl mx-auto">
      {/* Header with Filters */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-100 flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-indigo-400" />
            Аналитика нагрузки Helpdesk
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Сводная статистика по инженерам и категориям каталога
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Month selector */}
          <select
            value={month}
            onChange={(e) => setMonth(Number(e.target.value))}
            className="bg-[#151922] border border-[#252b3b] rounded-md px-2.5 py-1 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
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
            className="bg-[#151922] border border-[#252b3b] rounded-md px-2.5 py-1 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
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
        <div className="p-3 bg-red-950/40 border border-red-800/60 rounded-lg text-xs text-red-300">
          {error}
        </div>
      )}

      {/* KPI Cards */}
      {data && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Card className="bg-[#141822]">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-[11px] text-slate-400 font-medium">
                  Всего поступило
                </span>
                <div className="text-xl font-bold font-mono text-slate-100 mt-1">
                  {data.total_tickets}
                </div>
              </div>
              <Layers className="w-6 h-6 text-indigo-400/80" />
            </div>
          </Card>

          <Card className="bg-[#141822]">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-[11px] text-slate-400 font-medium">
                  Успешно решено
                </span>
                <div className="text-xl font-bold font-mono text-emerald-400 mt-1">
                  {data.closed_tickets}
                </div>
              </div>
              <Users className="w-6 h-6 text-emerald-400/80" />
            </div>
          </Card>

          <Card className="bg-[#141822]">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-[11px] text-slate-400 font-medium">
                  Среднее время решения
                </span>
                <div className="text-xl font-bold font-mono text-amber-400 mt-1">
                  {data.avg_resolution_hours} ч
                </div>
              </div>
              <Clock className="w-6 h-6 text-amber-400/80" />
            </div>
          </Card>
        </div>
      )}

      {/* Detailed breakdowns */}
      {data && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Engineers Load */}
          <Card
            title={
              <div className="flex items-center gap-2">
                <Users className="w-4 h-4 text-indigo-400" />
                <span>Нагрузка по инженерам</span>
              </div>
            }
          >
            <div className="space-y-2">
              {Object.entries(data.engineer_load).map(([name, count]) => {
                const percent =
                  data.closed_tickets > 0
                    ? Math.round((count / data.closed_tickets) * 100)
                    : 0;
                return (
                  <div key={name} className="space-y-1 text-xs">
                    <div className="flex justify-between items-center">
                      <span className="text-slate-300 font-medium">{name}</span>
                      <span className="font-mono text-slate-400">
                        {count} ({percent}%)
                      </span>
                    </div>
                    <div className="h-1.5 w-full bg-[#1e2330] rounded-full overflow-hidden">
                      <div
                        className="h-full bg-indigo-500 rounded-full"
                        style={{ width: `${percent}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Service Categories Load */}
          <Card
            title={
              <div className="flex items-center gap-2">
                <Layers className="w-4 h-4 text-emerald-400" />
                <span>Нагрузка по категориям сервисов</span>
              </div>
            }
          >
            <div className="space-y-2">
              {Object.entries(data.service_load).map(([service, count]) => {
                const percent =
                  data.total_tickets > 0
                    ? Math.round((count / data.total_tickets) * 100)
                    : 0;
                return (
                  <div key={service} className="space-y-1 text-xs">
                    <div className="flex justify-between items-center">
                      <span className="text-slate-300 truncate pr-2">{service}</span>
                      <span className="font-mono text-slate-400 shrink-0">
                        {count} ({percent}%)
                      </span>
                    </div>
                    <div className="h-1.5 w-full bg-[#1e2330] rounded-full overflow-hidden">
                      <div
                        className="h-full bg-emerald-500 rounded-full"
                        style={{ width: `${percent}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
};
