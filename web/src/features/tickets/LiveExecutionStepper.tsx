import React from "react";
import { CheckCircle2, XCircle, Loader2, Server, Terminal, CheckCheck, RefreshCw } from "lucide-react";
import { useCommandStatus } from "./queries";
import { Button } from "@/shared/ui";

export interface LiveExecutionStepperProps {
  commandId: string;
  onDismiss: () => void;
  onSuccess?: () => void;
}

export const LiveExecutionStepper: React.FC<LiveExecutionStepperProps> = ({
  commandId,
  onDismiss,
  onSuccess,
}) => {
  const { data: command, isLoading, error, refetch } = useCommandStatus(commandId);

  const status = command?.status || (isLoading ? "running" : "pending");

  React.useEffect(() => {
    if (status === "succeeded" && onSuccess) {
      onSuccess();
    }
  }, [status, onSuccess]);

  return (
    <div className="p-3.5 bg-[#0e1014] border border-neutral-800 rounded-lg shadow-lg space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {status === "running" || status === "pending" ? (
            <Loader2 className="w-4 h-4 text-amber-400 animate-spin" />
          ) : status === "succeeded" ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          ) : (
            <XCircle className="w-4 h-4 text-rose-400" />
          )}
          <span className="text-xs font-semibold text-neutral-100">
            {status === "running" || status === "pending"
              ? "Автономное исполнение команды..."
              : status === "succeeded"
              ? "Команда успешно выполнена"
              : "Ошибка выполнения сценария"}
          </span>
          <span className="text-[10px] font-mono text-neutral-500">
            ID: {commandId.slice(0, 8)}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => refetch()}
            icon={<RefreshCw className="w-3 h-3 text-neutral-400" />}
            title="Обновить статус"
          />
          {(status === "succeeded" || status === "failed") && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onDismiss}
              className="text-[11px] text-neutral-400 hover:text-white"
            >
              Закрыть
            </Button>
          )}
        </div>
      </div>

      {/* 3-Step Execution Pipeline */}
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        {/* Step 1: Probe */}
        <div
          className={`p-2 rounded border transition-colors flex flex-col gap-1 ${
            status === "pending" || status === "running" || status === "succeeded" || status === "failed"
              ? "bg-[#14161b] border-neutral-700/80 text-neutral-200"
              : "bg-[#101114] border-neutral-800/60 text-neutral-500"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold flex items-center gap-1">
              <Server className="w-3 h-3 text-blue-400" />
              1. Сеть & Хост
            </span>
            <span className="text-[10px] text-emerald-400 font-mono">OK</span>
          </div>
          <span className="text-[10px] text-neutral-400 truncate">
            {command?.target_json?.pc_name || "Проверка связи"}
          </span>
        </div>

        {/* Step 2: Taskiq Execution */}
        <div
          className={`p-2 rounded border transition-colors flex flex-col gap-1 ${
            status === "succeeded"
              ? "bg-[#14161b] border-emerald-900/60 text-emerald-300"
              : status === "running" || status === "pending"
              ? "bg-amber-950/20 border-amber-800/60 text-amber-200"
              : status === "failed"
              ? "bg-rose-950/20 border-rose-800/60 text-rose-300"
              : "bg-[#101114] border-neutral-800/60 text-neutral-500"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold flex items-center gap-1">
              <Terminal className="w-3 h-3 text-purple-400" />
              2. Сценарий
            </span>
            {status === "running" || status === "pending" ? (
              <Loader2 className="w-2.5 h-2.5 animate-spin text-amber-400" />
            ) : status === "succeeded" ? (
              <span className="text-[10px] text-emerald-400 font-mono">OK</span>
            ) : (
              <span className="text-[10px] text-rose-400 font-mono">ERR</span>
            )}
          </div>
          <span className="text-[10px] text-neutral-400 truncate">
            {command?.action || "Исполнение воркером"}
          </span>
        </div>

        {/* Step 3: IntraService Closure */}
        <div
          className={`p-2 rounded border transition-colors flex flex-col gap-1 ${
            status === "succeeded"
              ? "bg-emerald-950/20 border-emerald-800/60 text-emerald-200"
              : "bg-[#101114] border-neutral-800/60 text-neutral-500"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold flex items-center gap-1">
              <CheckCheck className="w-3 h-3 text-emerald-400" />
              3. IntraService
            </span>
            {status === "succeeded" && (
              <span className="text-[10px] text-emerald-400 font-mono">OK</span>
            )}
          </div>
          <span className="text-[10px] text-neutral-400 truncate">
            {status === "succeeded" ? "Выполнена (Статус 3)" : "Ожидание завершения"}
          </span>
        </div>
      </div>

      {/* Result or Error Message */}
      {command?.error_message && (
        <div className="p-2.5 bg-rose-950/40 border border-rose-800/60 rounded text-rose-300 text-xs font-mono whitespace-pre-wrap">
          {command.error_message}
        </div>
      )}

      {command?.result_json && (
        <div className="p-2 bg-[#121418] border border-neutral-800 rounded text-[11px] text-neutral-300 space-y-1">
          <div className="font-semibold text-neutral-400 text-[10px] uppercase tracking-wider">
            Ответ сценария
          </div>
          <div className="text-emerald-300">
            {command.result_json.message || JSON.stringify(command.result_json)}
          </div>
        </div>
      )}
    </div>
  );
};
