import React, { useState } from "react";
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
  Check,
  Edit3,
  XCircle,
  UserCheck,
  BarChart3,
  Filter,
} from "lucide-react";
import { Badge, Button, useToast } from "@/shared/ui";
import { autopilotApi } from "./api";
import { AutopilotMode, AutopilotPolicy, RoutingFeedback, RoutingQualityMetrics } from "./types";

const SCENARIO_NAMES: Record<string, string> = {
  install_printer: "Установка и настройка принтеров",
  ad_password_reset: "Сброс пароля Active Directory",
  grant_wlan: "Доступ к Wi-Fi WLAN-WORKNET",
  service_redirect: "Регламентное перенаправление (Шлюз #1)",
  offline_host: "Диагностика недоступности ПК",
  rag_consultation: "База знаний RAG (Авто-ответы)",
};

const VERDICT_BADGES: Record<
  string,
  { label: string; variant: "success" | "warning" | "danger" | "neutral"; icon: any }
> = {
  approved: { label: "Approved", variant: "success", icon: Check },
  corrected: { label: "Corrected", variant: "warning", icon: Edit3 },
  rejected: { label: "Rejected", variant: "danger", icon: XCircle },
  manual_takeover: { label: "Manual Takeover", variant: "neutral", icon: UserCheck },
};

export function AutopilotConsole() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [feedbackVerdictFilter, setFeedbackVerdictFilter] = useState<string>("");

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

  // 4. Fetch canonical Routing Feedback Record stream
  const { data: feedbackData, isLoading: isLoadingFeedback } = useQuery({
    queryKey: ["autopilot", "feedback", feedbackVerdictFilter],
    queryFn: () =>
      autopilotApi.getFeedback({
        limit: 30,
        verdict: feedbackVerdictFilter || undefined,
      }),
    refetchInterval: 5000,
  });

  // 5. Fetch Routing Quality Metrics
  const { data: qualityMetricsData } = useQuery({
    queryKey: ["autopilot", "metrics", "quality"],
    queryFn: () => autopilotApi.getQualityMetrics(),
    refetchInterval: 10000,
  });

  // Policy update mutation (optimistic UI)
  const updatePolicyMutation = useMutation({
    mutationFn: ({
      scenarioKey,
      mode,
    }: {
      scenarioKey: string;
      mode: AutopilotMode;
    }) => autopilotApi.updatePolicy(scenarioKey, { mode }),
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
  const metrics: RoutingQualityMetrics | undefined = qualityMetricsData?.metrics;
  const isSampleSufficient = (metrics?.total_decisions ?? 0) >= 5;

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
            Мониторинг Evidence-Based Routing Cascade, управление режимами автономии и канонический Feedback-контур
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

      {/* Quality Metrics Panel */}
      {metrics && (
        <div className="bg-[#121418] border border-neutral-800 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-indigo-400" />
              <h2 className="text-xs font-semibold text-neutral-200 uppercase tracking-wider">
                Качество маршрутизации (Routing Quality Metrics)
              </h2>
            </div>
            {!isSampleSufficient && (
              <Badge variant="warning" className="text-[10px]">
                Малая выборка ({metrics.total_decisions} / 5)
              </Badge>
            )}
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="bg-[#0e1013] border border-neutral-800/80 rounded p-2.5">
              <span className="text-[11px] text-neutral-400">Routing Accuracy</span>
              <div className="text-lg font-bold font-mono text-emerald-400 mt-1">
                {(metrics.approve_rate * 100).toFixed(1)}%
              </div>
              <span className="text-[10px] text-neutral-500">
                {Math.round(metrics.approve_rate * metrics.total_decisions)} подтверждено
              </span>
            </div>

            <div className="bg-[#0e1013] border border-neutral-800/80 rounded p-2.5">
              <span className="text-[11px] text-neutral-400">Correction Rate</span>
              <div className="text-lg font-bold font-mono text-amber-400 mt-1">
                {(metrics.correction_rate * 100).toFixed(1)}%
              </div>
              <span className="text-[10px] text-neutral-500">
                {Math.round(metrics.correction_rate * metrics.total_decisions)} скорректировано
              </span>
            </div>

            <div className="bg-[#0e1013] border border-neutral-800/80 rounded p-2.5">
              <span className="text-[11px] text-neutral-400">Reject / Takeover Rate</span>
              <div className="text-lg font-bold font-mono text-rose-400 mt-1">
                {(metrics.reject_takeover_rate * 100).toFixed(1)}%
              </div>
              <span className="text-[10px] text-neutral-500">
                {Math.round(metrics.reject_takeover_rate * metrics.total_decisions)} отклонено/перехвачено
              </span>
            </div>

            <div className="bg-[#0e1013] border border-neutral-800/80 rounded p-2.5">
              <span className="text-[11px] text-neutral-400">Ground Truth Decisions</span>
              <div className="text-lg font-bold font-mono text-neutral-200 mt-1">
                {metrics.total_decisions}
              </div>
              <span className="text-[10px] text-neutral-500">
                Команд: {(metrics.command_success_rate * 100).toFixed(0)}% OK
              </span>
            </div>
          </div>
        </div>
      )}

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

      {/* 4. Canonical Routing Feedback Loop */}
      <div className="bg-[#121418] border border-neutral-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-neutral-800/80 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-[#15181d]">
          <div className="flex items-center gap-2">
            <FileJson className="w-4 h-4 text-indigo-400" />
            <h2 className="text-xs font-semibold text-neutral-200 uppercase tracking-wider">
              Единый Feedback-контур (Routing Feedback Record)
            </h2>
            <Badge variant="neutral" className="text-[10px] font-mono">
              {feedbackData?.total ?? 0} записей
            </Badge>
          </div>

          <div className="flex items-center gap-2">
            {/* Filter pills */}
            <div className="flex items-center bg-[#0a0b0d] border border-neutral-800 rounded-md p-0.5 text-xs">
              <button
                onClick={() => setFeedbackVerdictFilter("")}
                className={`px-2 py-0.5 rounded ${
                  feedbackVerdictFilter === ""
                    ? "bg-neutral-800 text-neutral-100"
                    : "text-neutral-400 hover:text-neutral-200"
                }`}
              >
                Все
              </button>
              <button
                onClick={() => setFeedbackVerdictFilter("approved")}
                className={`px-2 py-0.5 rounded ${
                  feedbackVerdictFilter === "approved"
                    ? "bg-emerald-500/20 text-emerald-300"
                    : "text-neutral-400 hover:text-neutral-200"
                }`}
              >
                Approved
              </button>
              <button
                onClick={() => setFeedbackVerdictFilter("corrected")}
                className={`px-2 py-0.5 rounded ${
                  feedbackVerdictFilter === "corrected"
                    ? "bg-amber-500/20 text-amber-300"
                    : "text-neutral-400 hover:text-neutral-200"
                }`}
              >
                Corrected
              </button>
              <button
                onClick={() => setFeedbackVerdictFilter("rejected")}
                className={`px-2 py-0.5 rounded ${
                  feedbackVerdictFilter === "rejected"
                    ? "bg-rose-500/20 text-rose-300"
                    : "text-neutral-400 hover:text-neutral-200"
                }`}
              >
                Rejected
              </button>
              <button
                onClick={() => setFeedbackVerdictFilter("manual_takeover")}
                className={`px-2 py-0.5 rounded ${
                  feedbackVerdictFilter === "manual_takeover"
                    ? "bg-blue-500/20 text-blue-300"
                    : "text-neutral-400 hover:text-neutral-200"
                }`}
              >
                Takeover
              </button>
            </div>

            <button
              onClick={() => autopilotApi.downloadFeedbackExport()}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-xs transition-colors shadow-xs cursor-pointer"
              title="Экспорт датасета калибровки без PII и секретов"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Экспорт JSONL</span>
            </button>
          </div>
        </div>

        {isLoadingFeedback ? (
          <div className="p-8 text-center text-xs text-neutral-500">
            Загрузка feedback-записей...
          </div>
        ) : feedbackData?.feedback?.length === 0 ? (
          <div className="p-8 text-center text-xs text-neutral-500">
            Записей обратной связи не найдено. Решения операторов в карточках заявок отображаются здесь в реальном времени.
          </div>
        ) : (
          <div className="divide-y divide-neutral-800/60 max-h-[360px] overflow-y-auto">
            {feedbackData?.feedback?.map((f: RoutingFeedback) => {
              const verdictMeta = VERDICT_BADGES[f.verdict] || {
                label: f.verdict,
                variant: "neutral",
                icon: CheckCircle2,
              };
              const VerdictIcon = verdictMeta.icon;

              return (
                <div
                  key={f.id}
                  className="px-4 py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs hover:bg-neutral-800/20 transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-[11px] text-neutral-400 bg-neutral-900 border border-neutral-800 px-1.5 py-0.5 rounded font-mono shrink-0">
                      #{f.task_id}
                    </span>

                    <Badge variant={verdictMeta.variant} className="flex items-center gap-1">
                      <VerdictIcon className="w-3 h-3" />
                      <span>{verdictMeta.label}</span>
                    </Badge>

                    <div className="flex items-center gap-1.5 min-w-0 font-mono text-[11px]">
                      {f.verdict === "corrected" ? (
                        <>
                          <span className="text-neutral-400 line-through truncate max-w-[120px]">
                            {f.original_scenario || "none"}
                          </span>
                          <span className="text-neutral-500">➔</span>
                          <span className="text-amber-400 font-semibold truncate max-w-[140px]">
                            {f.corrected_scenario}
                          </span>
                        </>
                      ) : (
                        <span className="text-neutral-200 font-semibold truncate max-w-[160px]">
                          {f.corrected_scenario || f.original_scenario || "none"}
                        </span>
                      )}
                    </div>

                    {f.reason_tag && (
                      <span className="text-[10px] text-neutral-400 bg-neutral-900 px-1.5 py-0.5 rounded border border-neutral-800">
                        {f.reason_tag}
                      </span>
                    )}

                    {f.operator_notes && (
                      <span
                        className="text-[11px] text-neutral-400 italic truncate max-w-xs"
                        title={f.operator_notes}
                      >
                        "{f.operator_notes}"
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-3 text-neutral-500 text-[10px] shrink-0 font-mono">
                    <span>{f.operator_username || "operator"}</span>
                    {f.router_version && <span>v{f.router_version}</span>}
                    <span>{f.created_at ? new Date(f.created_at).toLocaleString("ru-RU") : "—"}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
