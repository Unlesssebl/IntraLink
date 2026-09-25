import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Zap,
  ShieldAlert,
  Clock,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  Play,
  Layers,
  Settings2,
  Activity,
  Terminal,
  Download,
  FileJson,
} from "lucide-react";
import { Badge, Button, useToast } from "@/shared/ui";
import { autopilotApi } from "./api";
import { AutopilotCorrection, AutopilotMode, AutopilotPolicy } from "./types";

const SCENARIO_NAMES: Record<string, string> = {
  install_printer: "Установка и настройка принтеров",
  ad_password_reset: "Сброс пароля Active Directory",
  grant_wlan: "Доступ к Wi-Fi WLAN-WORKNET",
  service_redirect: "Регламентное перенаправление (Шлюз #1)",
  offline_host: "Диагностика недоступности ПК",
  rag_consultation: "База знаний RAG (Авто-ответы)",
};

export function AutopilotConsole() {
  const queryClient = useQueryClient();
  const toast = useToast();

  // 1. Fetch policies
  const { data: policiesData, isLoading: isLoadingPolicies } = useQuery({
    queryKey: ["autopilot", "policies"],
    queryFn: () => autopilotApi.getPolicies(),
    refetchInterval: 5000,
  });

  // 2. Fetch aggregate telemetry & stats
  const { data: statsData } = useQuery({
    queryKey: ["autopilot", "stats"],
    queryFn: () => autopilotApi.getStats(),
    refetchInterval: 3000,
  });

  // 3. Fetch live command execution feed (short-polling 1.5s)
  const { data: commands = [], isLoading: isLoadingCommands } = useQuery({
    queryKey: ["autopilot", "commands"],
    queryFn: () => autopilotApi.getCommands(30),
    refetchInterval: 1500,
  });

  // 4. Fetch human supervisor corrections (Harness Ground Truth)
  const { data: correctionsData } = useQuery({
    queryKey: ["autopilot", "corrections"],
    queryFn: () => autopilotApi.getCorrections(20),
    refetchInterval: 10000,
  });

  // Policy update mutation (optimistic UI)
  const updatePolicyMutation = useMutation({
    mutationFn: ({
      scenarioKey,
      mode,
      minConfidence,
    }: {
      scenarioKey: string;
      mode: AutopilotMode;
      minConfidence?: number;
    }) => autopilotApi.updatePolicy(scenarioKey, { mode, min_confidence: minConfidence }),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        ["autopilot", "policies"],
        (old: { policies: AutopilotPolicy[]; total: number } | undefined) => {
          if (!old) return old;
          return {
            ...old,
            policies: old.policies.map((p) =>
              p.scenario_key === updated.scenario_key ? updated : p
            ),
          };
        }
      );
      toast.success(
        `Режим для «${SCENARIO_NAMES[updated.scenario_key] || updated.scenario_key}» изменен на ${updated.mode}`
      );
    },
    onError: (err: any) => {
      toast.error(`Ошибка обновления политики: ${err.message}`);
    },
  });

  // Reset circuit breaker mutation
  const resetMutation = useMutation({
    mutationFn: (scenarioKey: string) => autopilotApi.resetCircuitBreaker(scenarioKey),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ["autopilot"] });
      toast.success(
        `Предохранитель для «${SCENARIO_NAMES[updated.scenario_key] || updated.scenario_key}» успешно сброшен`
      );
    },
    onError: (err: any) => {
      toast.error(`Не удалось сбросить предохранитель: ${err.message}`);
    },
  });

  const policies = policiesData?.policies || [];

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100 flex items-center gap-2.5">
            <Bot className="w-5 h-5 text-indigo-400" />
            Операторская консоль управления Автопилотом
          </h1>
          <p className="text-xs text-neutral-400 mt-1">
            Мониторинг Core-6 сценариев, оперативное переключение матриц автономии и защита от сбоев (Circuit Breaker)
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Badge variant="neutral" dot pulse className="font-mono text-[11px]">
            Live: 1.5s
          </Badge>
        </div>
      </div>

      {/* Telemetry Impact Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
        <div className="bg-[#121418] border border-neutral-800 rounded-lg p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-neutral-400 text-xs">
            <span>Сэкономлено времени</span>
            <Clock className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-1.5">
            <span className="text-2xl font-bold font-mono text-emerald-400">
              {statsData?.hours_saved ?? 0}
            </span>
            <span className="text-xs text-neutral-400">часов Helpdesk</span>
          </div>
        </div>

        <div className="bg-[#121418] border border-neutral-800 rounded-lg p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-neutral-400 text-xs">
            <span>Автономных решений</span>
            <Zap className="w-4 h-4 text-amber-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-1.5">
            <span className="text-2xl font-bold font-mono text-neutral-100">
              {statsData?.total_automated_actions ?? 0}
            </span>
            <span className="text-xs text-neutral-400">успешных действий</span>
          </div>
        </div>

        <div className="bg-[#121418] border border-neutral-800 rounded-lg p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-neutral-400 text-xs">
            <span>Активных сценариев</span>
            <Layers className="w-4 h-4 text-indigo-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-1.5">
            <span className="text-2xl font-bold font-mono text-neutral-100">
              {statsData?.active_scenarios_count ?? 6}
            </span>
            <span className="text-xs text-neutral-400">из 6 модулей</span>
          </div>
        </div>

        <div className="bg-[#121418] border border-neutral-800 rounded-lg p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-neutral-400 text-xs">
            <span>Состояние конвейера</span>
            {statsData?.tripped_circuit_breakers ? (
              <ShieldAlert className="w-4 h-4 text-rose-400" />
            ) : (
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            )}
          </div>
          <div className="mt-2 flex items-baseline gap-1.5">
            <span
              className={`text-sm font-semibold ${
                statsData?.tripped_circuit_breakers
                  ? "text-rose-400"
                  : "text-emerald-400"
              }`}
            >
              {statsData?.tripped_circuit_breakers
                ? `${statsData.tripped_circuit_breakers} сработал откат`
                : "Все системы в норме"}
            </span>
          </div>
        </div>
      </div>

      {/* Scenarios Governance Table */}
      <div className="bg-[#121418] border border-neutral-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-neutral-800 flex items-center justify-between bg-[#15181d]">
          <div className="flex items-center gap-2">
            <Settings2 className="w-4 h-4 text-neutral-400" />
            <h2 className="text-xs font-semibold text-neutral-200 uppercase tracking-wider">
              Матрица автономии Core-6 (0ms Redis Sync)
            </h2>
          </div>
          <span className="text-[11px] text-neutral-500">
            Иерархия: Redis ➔ PostgreSQL ➔ Baseline YAML
          </span>
        </div>

        {isLoadingPolicies ? (
          <div className="p-8 text-center text-xs text-neutral-500">
            Загрузка политик автопилота...
          </div>
        ) : (
          <div className="divide-y divide-neutral-800/80">
            {policies.map((p) => {
              const displayName = SCENARIO_NAMES[p.scenario_key] || p.scenario_key;
              return (
                <div
                  key={p.scenario_key}
                  className="p-4 flex flex-col lg:flex-row lg:items-center justify-between gap-4 hover:bg-neutral-800/30 transition-colors"
                >
                  <div className="space-y-1 max-w-xl">
                    <div className="flex items-center gap-2.5">
                      <span className="text-sm font-medium text-neutral-200">
                        {displayName}
                      </span>
                      <code className="text-[10px] text-neutral-500 bg-neutral-900 border border-neutral-800 px-1.5 py-0.5 rounded font-mono">
                        {p.scenario_key}
                      </code>
                      {p.is_circuit_broken && (
                        <Badge variant="danger" dot pulse>
                          Circuit Broken (3 ошибки)
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-neutral-400">
                      {p.description || "Автоматизированный сценарий Helpdesk"}
                    </p>
                    <div className="flex items-center gap-3 text-[11px] text-neutral-500 pt-0.5">
                      <span>Порог уверенности: {(p.min_confidence * 100).toFixed(0)}%</span>
                      <span>•</span>
                      <span>Ошибок подряд: {p.consecutive_failures}</span>
                      {p.last_failure_at && (
                        <>
                          <span>•</span>
                          <span>
                            Последний сбой:{" "}
                            {new Date(p.last_failure_at).toLocaleTimeString("ru-RU", {
                              hour: "2-digit",
                              minute: "2-digit",
                              second: "2-digit",
                            })}
                          </span>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Governance Controls */}
                  <div className="flex items-center gap-3 self-end lg:self-center shrink-0">
                    {p.is_circuit_broken && (
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={<RotateCcw className="w-3 h-3 text-amber-400" />}
                        loading={resetMutation.isPending}
                        onClick={() => resetMutation.mutate(p.scenario_key)}
                        title="Сбросить счетчик ошибок и вернуть в штатный режим"
                      >
                        Сбросить предохранитель
                      </Button>
                    )}

                    {/* Mode Toggle Pills */}
                    <div className="flex items-center bg-[#0a0b0d] border border-neutral-800 rounded-md p-0.5">
                      <button
                        onClick={() =>
                          updatePolicyMutation.mutate({
                            scenarioKey: p.scenario_key,
                            mode: "FULL_AUTO",
                          })
                        }
                        className={`px-2.5 py-1 text-xs font-medium rounded transition-all ${
                          p.mode === "FULL_AUTO"
                            ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 shadow-xs"
                            : "text-neutral-400 hover:text-neutral-200"
                        }`}
                        title="Полная автономия: самостоятельное закрытие и публикация решения"
                      >
                        FULL_AUTO
                      </button>

                      <button
                        onClick={() =>
                          updatePolicyMutation.mutate({
                            scenarioKey: p.scenario_key,
                            mode: "ASSISTED",
                          })
                        }
                        className={`px-2.5 py-1 text-xs font-medium rounded transition-all ${
                          p.mode === "ASSISTED"
                            ? "bg-amber-500/20 text-amber-300 border border-amber-500/40 shadow-xs"
                            : "text-neutral-400 hover:text-neutral-200"
                        }`}
                        title="Ко-пилот: подготовка команды в ActionDock для подтверждения оператором"
                      >
                        ASSISTED
                      </button>

                      <button
                        onClick={() =>
                          updatePolicyMutation.mutate({
                            scenarioKey: p.scenario_key,
                            mode: "DISABLED",
                          })
                        }
                        className={`px-2.5 py-1 text-xs font-medium rounded transition-all ${
                          p.mode === "DISABLED"
                            ? "bg-rose-500/20 text-rose-300 border border-rose-500/40 shadow-xs"
                            : "text-neutral-400 hover:text-neutral-200"
                        }`}
                        title="Сценарий отключен: заявки передаются на ручную обработку"
                      >
                        DISABLED
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Live Execution Feed */}
      <div className="bg-[#121418] border border-neutral-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-neutral-800 flex items-center justify-between bg-[#15181d]">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-neutral-400" />
            <h2 className="text-xs font-semibold text-neutral-200 uppercase tracking-wider">
              Live Execution Feed (CommandRecord)
            </h2>
          </div>
          <span className="text-[11px] text-neutral-500">
            Очереди: default / rag_compute / windows_exec
          </span>
        </div>

        {isLoadingCommands && commands.length === 0 ? (
          <div className="p-8 text-center text-xs text-neutral-500">
            Ожидание команд выполнения...
          </div>
        ) : commands.length === 0 ? (
          <div className="p-8 text-center text-xs text-neutral-500">
            Журнал команд пуст. Выполненные сценарии появятся здесь автоматически.
          </div>
        ) : (
          <div className="divide-y divide-neutral-800/60 max-h-[360px] overflow-y-auto">
            {commands.map((cmd) => (
              <div
                key={cmd.id}
                className="px-4 py-2.5 flex items-center justify-between text-xs hover:bg-neutral-800/20 transition-colors"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <Badge
                    variant={
                      cmd.status === "succeeded"
                        ? "success"
                        : cmd.status === "failed"
                        ? "danger"
                        : cmd.status === "running"
                        ? "warning"
                        : "neutral"
                    }
                    dot={cmd.status === "running"}
                    pulse={cmd.status === "running"}
                  >
                    {cmd.status}
                  </Badge>

                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-medium text-neutral-200 font-mono">
                      {cmd.action}
                    </span>
                    {cmd.task_id && (
                      <span className="text-[11px] text-neutral-400 bg-neutral-900 border border-neutral-800 px-1.5 py-0.5 rounded font-mono">
                        #{cmd.task_id}
                      </span>
                    )}
                    {cmd.error_message && (
                      <span className="text-[11px] text-rose-400 truncate max-w-sm" title={cmd.error_message}>
                        {cmd.error_message}
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-4 text-neutral-500 text-[11px] shrink-0 font-mono">
                  <span>{cmd.initiator}</span>
                  <span>
                    {new Date(cmd.created_at).toLocaleTimeString("ru-RU", {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    })}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 4. Harness Ground Truth Feedback Dataset */}
      <div className="bg-[#121418] border border-neutral-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-neutral-800/80 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileJson className="w-4 h-4 text-amber-400" />
            <h2 className="text-sm font-semibold text-neutral-100">
              Датасет корректировок супервизора (Harness Ground Truth)
            </h2>
            <Badge variant="warning">
              {correctionsData?.total ?? 0} правок
            </Badge>
          </div>

          <a
            href={autopilotApi.getExportCorrectionsUrl(500)}
            download="autopilot_corrections.jsonl"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-neutral-950 font-semibold text-xs transition-colors shadow-xs"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Экспорт JSONL для Pytest / AI Coder</span>
          </a>
        </div>

        {correctionsData?.corrections?.length === 0 ? (
          <div className="p-8 text-center text-xs text-neutral-500">
            Правок пока нет. Когда оператор корректирует параметры или сценарий в карточке заявки, диф автоматически попадает в обучающий датасет.
          </div>
        ) : (
          <div className="divide-y divide-neutral-800/60 max-h-[320px] overflow-y-auto">
            {correctionsData?.corrections?.map((c: AutopilotCorrection) => (
              <div
                key={c.id}
                className="px-4 py-2.5 flex items-center justify-between text-xs hover:bg-neutral-800/20 transition-colors"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span className="text-[11px] text-neutral-400 bg-neutral-900 border border-neutral-800 px-1.5 py-0.5 rounded font-mono shrink-0">
                    #{c.task_id}
                  </span>

                  <Badge variant="warning">
                    {c.correction_tag}
                  </Badge>

                  <div className="flex items-center gap-1.5 min-w-0 font-mono text-[11px]">
                    <span className="text-neutral-400 line-through truncate max-w-[120px]">
                      {c.original_scenario}
                    </span>
                    <span className="text-neutral-500">➔</span>
                    <span className="text-emerald-400 font-semibold truncate max-w-[140px]">
                      {c.corrected_scenario}
                    </span>
                  </div>

                  {c.operator_notes && (
                    <span className="text-[11px] text-neutral-400 italic truncate max-w-xs" title={c.operator_notes}>
                      "{c.operator_notes}"
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-3 text-neutral-500 text-[10px] shrink-0 font-mono">
                  <span>{c.operator_username}</span>
                  <span>{new Date(c.created_at).toLocaleString("ru-RU")}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
