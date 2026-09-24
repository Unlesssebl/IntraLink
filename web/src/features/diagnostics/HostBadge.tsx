import React, { useState } from "react";
import { Monitor, RefreshCw, CheckCircle2, XCircle } from "lucide-react";
import { Badge } from "@/shared/ui";
import { diagnosticsApi, HostDiagnostic } from "@/shared/api";

export interface HostBadgeProps {
  host?: string | null;
}

export const HostBadge: React.FC<HostBadgeProps> = ({ host }) => {
  const [data, setData] = useState<HostDiagnostic | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [popoverOpen, setPopoverOpen] = useState(false);

  // If no host is provided, render nothing (after all hooks are initialized)
  if (!host) {
    return null;
  }

  const runCheck = async (e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    try {
      setLoading(true);
      setError(null);
      const res = await diagnosticsApi.diagnose(host);
      setData(res);
      setPopoverOpen(true);
    } catch (err: any) {
      setError(err?.message || "Ошибка диагностики");
      setPopoverOpen(true);
    } finally {
      setLoading(false);
    }
  };

  const getVariant = () => {
    if (!data) return "neutral";
    return data.is_online ? "success" : "danger";
  };

  return (
    <div className="relative inline-block font-mono">
      <div
        onClick={runCheck}
        className="inline-flex items-center gap-1.5 cursor-pointer hover:opacity-85 transition-opacity"
        title="Нажмите для проверки сети (Ping, SMB, WinRM)"
      >
        <Badge variant={getVariant()} dot={!!data} pulse={loading}>
          <Monitor className="w-3 h-3 text-neutral-400" />
          <span>{host}</span>
          {loading && <RefreshCw className="w-2.5 h-2.5 animate-spin ml-0.5" />}
          {data && (
            <span className="text-[10px] opacity-80">
              {data.is_online ? `${data.round_trip_ms ?? 0}ms` : "down"}
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
          <div className="absolute left-0 mt-1 z-50 w-56 bg-[#121316] border border-neutral-800 rounded shadow-2xl p-3 text-xs space-y-2 animate-in fade-in zoom-in-95 duration-100">
            <div className="flex items-center justify-between border-b border-neutral-800/80 pb-1.5 font-semibold text-neutral-200">
              <span className="truncate">{host}</span>
              <button
                onClick={runCheck}
                disabled={loading}
                className="text-neutral-400 hover:text-neutral-200 p-0.5 rounded hover:bg-neutral-800/50"
              >
                <RefreshCw
                  className={`w-3 h-3 ${loading ? "animate-spin" : ""}`}
                />
              </button>
            </div>

            {error ? (
              <p className="text-[11px] text-rose-400">{error}</p>
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
                {data.ip_address && (
                  <div className="flex justify-between">
                    <span className="text-neutral-400">IP:</span>
                    <span className="font-mono">{data.ip_address}</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-neutral-400">SMB (порт 445):</span>
                  <span
                    className={
                      data.smb_port_open ? "text-emerald-400" : "text-neutral-500"
                    }
                  >
                    {data.smb_port_open ? "Открыт" : "Закрыт"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-neutral-400">WinRM (5985):</span>
                  <span
                    className={
                      data.winrm_port_open ? "text-emerald-400" : "text-neutral-500"
                    }
                  >
                    {data.winrm_port_open ? "Открыт" : "Закрыт"}
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
