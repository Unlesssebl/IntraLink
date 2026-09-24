import React, { useState } from "react";
import { Monitor, RefreshCw, CheckCircle2, XCircle, AlertCircle } from "lucide-react";
import { Badge, Button } from "@/shared/ui";
import { diagnosticsApi, HostDiagnostic } from "@/shared/api";

export interface HostBadgeProps {
  host?: string | null;
}

export const HostBadge: React.FC<HostBadgeProps> = ({ host }) => {
  const [data, setData] = useState<HostDiagnostic | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [popoverOpen, setPopoverOpen] = useState(false);

  // If no host is provided, render nothing
  if (!host || !host.trim()) {
    return null;
  }

  // Edge case (docs/architecture/domain-model-and-contracts.md):
  // Clean host token if applicant specified composite names like "PC-01, PC-02"
  const primaryHost = host.split(/[,;\s/]/)[0].trim();
  if (!primaryHost) return null;

  const runCheck = async (e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    try {
      setLoading(true);
      setError(null);
      const res = await diagnosticsApi.diagnose(primaryHost);
      setData(res);
      setPopoverOpen(true);
    } catch (err: any) {
      setError(err?.message || "Ошибка опроса рабочей станции");
      setPopoverOpen(true);
    } finally {
      setLoading(false);
    }
  };

  const getVariant = () => {
    if (!data) return "neutral";
    return data.is_online ? "success" : "danger";
  };

  const smbOpen = Boolean(data?.ports?.["smb_445"]);
  const winrmOpen = Boolean(data?.ports?.["winrm_5985"]);

  return (
    <div className="relative inline-block font-mono">
      <div
        onClick={runCheck}
        className="inline-flex items-center gap-1.5 cursor-pointer hover:opacity-85 transition-opacity"
        title="Нажмите для проверки сети (Ping, SMB, WinRM)"
      >
        <Badge variant={getVariant()} dot={!!data} pulse={loading}>
          <Monitor className="w-3 h-3 text-neutral-400" />
          <span>{primaryHost}</span>
          {loading && <RefreshCw className="w-2.5 h-2.5 animate-spin ml-0.5" />}
          {data && (
            <span className="text-[10px] opacity-80">
              {data.is_online ? (data.avg_rtt || "online") : "down"}
            </span>
          )}
        </Badge>
      </div>

      {popoverOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setPopoverOpen(false)}
          />
          <div className="absolute left-0 mt-1 z-50 w-64 bg-[#121316] border border-neutral-800 rounded shadow-2xl p-3 text-xs space-y-2 animate-in fade-in zoom-in-95 duration-100">
            <div className="flex items-center justify-between border-b border-neutral-800/80 pb-1.5 font-semibold text-neutral-200">
              <span className="truncate">{primaryHost}</span>
              <button
                onClick={runCheck}
                disabled={loading}
                className="text-neutral-400 hover:text-neutral-200 p-0.5 rounded hover:bg-neutral-800/50"
                title="Обновить диагностику"
              >
                <RefreshCw
                  className={`w-3 h-3 ${loading ? "animate-spin" : ""}`}
                />
              </button>
            </div>

            {error ? (
              <div className="space-y-2 py-1">
                <div className="flex items-start gap-1.5 text-[11px] text-rose-400">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>{error}</span>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  className="w-full text-[10px] py-1"
                  onClick={runCheck}
                >
                  Попробовать снова
                </Button>
              </div>
            ) : data ? (
              <div className="space-y-1 text-[11px] text-neutral-300">
                <div className="flex justify-between items-center">
                  <span className="text-neutral-400">Статус сети:</span>
                  <span className="flex items-center gap-1 font-medium">
                    {data.is_online ? (
                      <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                    ) : (
                      <XCircle className="w-3 h-3 text-rose-400" />
                    )}
                    {data.is_online ? "Онлайн" : "Офлайн"}
                  </span>
                </div>
                {data.is_online && data.avg_rtt && (
                  <div className="flex justify-between">
                    <span className="text-neutral-400">Время отклика (RTT):</span>
                    <span className="font-mono text-neutral-200">{data.avg_rtt}</span>
                  </div>
                )}
                {data.ip_address && (
                  <div className="flex justify-between">
                    <span className="text-neutral-400">IP адрес:</span>
                    <span className="font-mono text-neutral-200">{data.ip_address}</span>
                  </div>
                )}
                <div className="flex justify-between pt-1 border-t border-neutral-800/60">
                  <span className="text-neutral-400">SMB (порт 445):</span>
                  <span
                    className={
                      smbOpen ? "text-emerald-400 font-medium" : "text-neutral-500"
                    }
                  >
                    {smbOpen ? "Открыт" : "Закрыт"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-neutral-400">WinRM (5985):</span>
                  <span
                    className={
                      winrmOpen ? "text-emerald-400 font-medium" : "text-neutral-500"
                    }
                  >
                    {winrmOpen ? "Открыт" : "Закрыт"}
                  </span>
                </div>
              </div>
            ) : (
              <p className="text-[11px] text-neutral-400">Загрузка данных...</p>
            )}
          </div>
        </>
      )}
    </div>
  );
};
