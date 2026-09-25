import React, { useState, useEffect, useMemo } from "react";
import {
  Sparkles,
  Bot,
  CheckCircle2,
  AlertTriangle,
  Server,
  Terminal,
  ShieldAlert,
  ArrowRight,
  Edit3,
  RefreshCw,
  Send,
  Zap,
  Paperclip,
} from "lucide-react";
import { Button, Badge, KbdBadge, useToast } from "@/shared/ui";
import { HostBadge } from "@/features/diagnostics/HostBadge";
import { useAgentPlan } from "./queries";
import { autopilotApi } from "@/features/autopilot/api";
import { AgentPlan, ApprovePlanRequest, CorrectPlanRequest } from "@/features/autopilot/types";
import { useQueryClient } from "@tanstack/react-query";
import { ticketKeys } from "./queries";

export interface AgentPlanCardProps {
  ticketId: number;
  currentStatusId: number;
  onExecutionStarted: (commandId: string) => void;
  onClose?: () => void;
}

const SCENARIO_OPTIONS = [
  { key: "printer_spooler_restart", name: "Перезапуск очереди печати (Spooler)" },
  { key: "default_printer_fix", name: "Установка принтера по умолчанию" },
  { key: "ad_account_unlock", name: "Разблокировка учетной записи AD" },
  { key: "ad_password_reset", name: "Сброс пароля пользователя в AD" },
  { key: "shared_folder_access", name: "Проверка доступа к сетевой папке (SMB)" },
  { key: "vpn_diagnostic", name: "Диагностика VPN подключения" },
  { key: "directum_cache_clear", name: "Очистка локального кэша Directum" },
  { key: "rag_consultation", name: "RAG Консультация / Регламент" },
  { key: "cancel_irrelevant", name: "Отмена нецелевой заявки (Статус 30)" },
];

const CORRECTION_TAGS = [
  { key: "typo", label: "Опечатка в тексте / имени ПК" },
  { key: "wrong_printer_model", label: "Неверная модель МФУ / принтера" },
  { key: "slang", label: "Жаргон / сленг заявителя" },
  { key: "false_duplicate", label: "Ложный дубликат" },
  { key: "policy_override", label: "Ручное переопределение регламента" },
];

export const AgentPlanCard: React.FC<AgentPlanCardProps> = ({
  ticketId,
  currentStatusId,
  onExecutionStarted,
  onClose,
}) => {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { data: plan, isLoading, error, refetch } = useAgentPlan(ticketId);

  // Form states
  const [scenarioKey, setScenarioKey] = useState<string>("");
  const [params, setParams] = useState<Record<string, any>>({});
  const [comment, setComment] = useState<string>("");
  const [correctionTag, setCorrectionTag] = useState<string>("typo");
  const [operatorNotes, setOperatorNotes] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  // Sync state when new plan arrives
  useEffect(() => {
    if (plan) {
      setScenarioKey(plan.scenario_key);
      setParams(plan.proposed_params ? { ...plan.proposed_params } : {});
      setComment(plan.suggested_comment || "");
      setCorrectionTag("typo");
      setOperatorNotes("");
    }
  }, [plan?.scenario_key, plan?.task_id]);

  // Compute if modified by human supervisor
  const isDirty = useMemo(() => {
    if (!plan) return false;
    if (scenarioKey !== plan.scenario_key) return true;
    if (comment.trim() !== (plan.suggested_comment || "").trim()) return true;

    // Compare params shallowly
    const originalParams = plan.proposed_params || {};
    const keysA = Object.keys(params);
    const keysB = Object.keys(originalParams);
    if (keysA.length !== keysB.length) return true;
    for (const k of keysA) {
      if (String(params[k] ?? "").trim() !== String(originalParams[k] ?? "").trim()) {
        return true;
      }
    }
    return false;
  }, [plan, scenarioKey, params, comment]);

  const handleParamChange = (key: string, val: string) => {
    setParams((prev) => ({ ...prev, [key]: val }));
  };

  const handleHostChipClick = (candidate: string) => {
    setParams((prev) => ({ ...prev, pc_name: candidate }));
    toast.success(`Хост переключен на: ${candidate}`);
  };

  // 1. APPROVE (Unchanged Plan)
  const handleApprove = async () => {
    if (!plan || isSubmitting) return;
    try {
      setIsSubmitting(true);
      const req: ApprovePlanRequest = {
        expected_status_id: currentStatusId,
        last_event_id: plan.last_event_id ?? undefined,
        override_comment: comment !== plan.suggested_comment ? comment : undefined,
      };
      const res = await autopilotApi.approvePlan(ticketId, req);
      toast.success("План агента одобрен. Команда запущена!");
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
      queryClient.invalidateQueries({ queryKey: ["autopilot", "plan", ticketId] });
      if (res.command_id) {
        onExecutionStarted(res.command_id);
      }
    } catch (err: any) {
      toast.error(`Ошибка одобрения: ${err?.message || "Сбой запроса"}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  // 2. CORRECT (Human in the Loop Feedback)
  const handleCorrect = async () => {
    if (!plan || isSubmitting) return;
    try {
      setIsSubmitting(true);
      const req: CorrectPlanRequest = {
        expected_status_id: currentStatusId,
        last_event_id: plan.last_event_id ?? undefined,
        corrected_scenario: scenarioKey,
        corrected_params: params,
        corrected_comment: comment,
        correction_tag: correctionTag,
        operator_notes: operatorNotes || undefined,
      };
      const res = await autopilotApi.correctPlan(ticketId, req);
      toast.success("Корректировка зафиксирована в Harness и запущена!");
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
      queryClient.invalidateQueries({ queryKey: ["autopilot", "plan", ticketId] });
      if (res.command_id) {
        onExecutionStarted(res.command_id);
      }
    } catch (err: any) {
      toast.error(`Ошибка отправки корректировки: ${err?.message || "Сбой запроса"}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Keyboard shortcut handler (Enter = approve if not dirty, Ctrl+Enter = correct or approve)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLTextAreaElement && !e.ctrlKey && !e.metaKey) {
        return; // Allow newlines in textarea unless Ctrl is held
      }

      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if (isDirty) {
          handleCorrect();
        } else {
          handleApprove();
        }
      } else if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) {
        // If focused in regular input or outside
        if (!(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) {
          e.preventDefault();
          if (!isDirty) {
            handleApprove();
          }
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isDirty, plan, currentStatusId, scenarioKey, params, comment, correctionTag, operatorNotes, isSubmitting]);

  if (isLoading) {
    return (
      <div className="p-4 bg-[#0e1014] border border-neutral-800 rounded-lg animate-pulse space-y-3">
        <div className="flex items-center justify-between">
          <div className="h-4 bg-neutral-800 rounded w-1/3" />
          <div className="h-4 bg-neutral-800 rounded w-1/6" />
        </div>
        <div className="h-8 bg-neutral-800/60 rounded" />
        <div className="h-16 bg-neutral-800/40 rounded" />
      </div>
    );
  }

  if (error || !plan) {
    return (
      <div className="p-3 bg-rose-950/20 border border-rose-800/40 rounded text-rose-300 text-xs flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{(error as any)?.message || "Не удалось загрузить план автономного агента"}</span>
        </div>
        <Button size="sm" variant="ghost" onClick={() => refetch()} icon={<RefreshCw className="w-3 h-3" />}>
          Повторить
        </Button>
      </div>
    );
  }

  const confidencePct = Math.round(plan.confidence * 100);
  const confidenceColor =
    confidencePct >= 80 ? "text-emerald-400" : confidencePct >= 60 ? "text-amber-400" : "text-rose-400";
  const confidenceBarColor =
    confidencePct >= 80 ? "bg-emerald-500" : confidencePct >= 60 ? "bg-amber-500" : "bg-rose-500";

  return (
    <div className="p-3.5 bg-[#0d0f12] border border-neutral-800 rounded-lg shadow-xl space-y-3 relative overflow-hidden">
      {/* Top Banner: Circuit Breaker Alert if tripped */}
      {plan.is_circuit_broken && (
        <div className="p-2 bg-rose-950/40 border border-rose-800/70 rounded text-[11px] text-rose-200 flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0" />
          <span>Предохранитель активен (Circuit Breaker): 3 ошибки подряд. Требуется ручная валидация.</span>
        </div>
      )}

      {/* Header: Agent Plan, Scenario Switcher, Confidence Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 border-b border-neutral-800/80">
        <div className="flex items-center gap-2 min-w-0">
          <div className="p-1 rounded bg-purple-950/60 border border-purple-800/60 text-purple-300">
            <Bot className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-neutral-100">План действий автономного агента</span>
              <Badge variant={plan.mode === "FULL_AUTO" ? "success" : "warning"}>
                {plan.mode === "FULL_AUTO" ? "FULL-AUTO" : "ASSISTED"}
              </Badge>
              {plan.is_tense && (
                <span
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse"
                  title={plan.tense_reason || "Срочный / обеспокоенный тон"}
                >
                  <Zap className="w-3 h-3 text-amber-400" />
                  ⚡ Заявитель обеспокоен / Срочно
                </span>
              )}
              {plan.has_attachments && (
                <span
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-500/20 text-blue-300 border border-blue-500/40"
                  title="В тикете есть прикрепленные файлы"
                >
                  <Paperclip className="w-3 h-3 text-blue-400" />
                  Вложения
                </span>
              )}
            </div>
            <div className="text-[10px] text-neutral-400 truncate">{plan.description}</div>
          </div>
        </div>

        {/* Confidence Gauge */}
        <div className="flex items-center gap-2 shrink-0 bg-[#121418] px-2.5 py-1 rounded border border-neutral-800">
          <span className="text-[10px] text-neutral-400">Уверенность:</span>
          <span className={`text-xs font-mono font-bold ${confidenceColor}`}>{confidencePct}%</span>
          <div className="w-12 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
            <div className={`h-full ${confidenceBarColor}`} style={{ width: `${confidencePct}%` }} />
          </div>
        </div>
      </div>

      {/* Precondition Barriers Banner if invalid */}
      {!plan.preconditions.is_valid && (
        <div className="p-2.5 bg-amber-950/30 border border-amber-800/60 rounded text-[11px] text-amber-200 space-y-1">
          <div className="font-semibold flex items-center gap-1.5 text-amber-300">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span>Требуются дополнительные данные или хост недоступен</span>
          </div>
          {plan.preconditions.missing_facts.length > 0 && (
            <div className="text-[10px] text-amber-300/80">
              Недостающие факты: {plan.preconditions.missing_facts.join(", ")}
            </div>
          )}
          {plan.preconditions.environment_barriers.length > 0 && (
            <div className="text-[10px] text-rose-300">
              Сетевые барьеры: {plan.preconditions.environment_barriers.join(", ")}
            </div>
          )}
        </div>
      )}

      {/* Host Diagnostics Probe & Candidate Host Switcher */}
      <div className="p-2.5 bg-[#121418] border border-neutral-800/90 rounded text-xs space-y-2">
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-neutral-400 font-medium flex items-center gap-1.5">
            <Server className="w-3.5 h-3.5 text-blue-400" />
            Целевое рабочее место
          </span>
          {plan.host_diagnostic && (
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <span
                className={`flex items-center gap-1 ${
                  plan.host_diagnostic.is_online ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    plan.host_diagnostic.is_online ? "bg-emerald-400" : "bg-rose-400"
                  }`}
                />
                {plan.host_diagnostic.is_online ? "Online" : "Offline"}
                {plan.host_diagnostic.avg_rtt && ` (${plan.host_diagnostic.avg_rtt})`}
              </span>
              {plan.host_diagnostic.ports["5985"] !== undefined && (
                <span className={plan.host_diagnostic.ports["5985"] ? "text-emerald-400" : "text-neutral-500"}>
                  WinRM:{plan.host_diagnostic.ports["5985"] ? "OK" : "Closed"}
                </span>
              )}
              {plan.host_diagnostic.ports["9100"] !== undefined && (
                <span className={plan.host_diagnostic.ports["9100"] ? "text-emerald-400" : "text-neutral-500"}>
                  P9100:{plan.host_diagnostic.ports["9100"] ? "OK" : "Closed"}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Candidate Host Chips */}
        {plan.candidate_hosts && plan.candidate_hosts.length > 0 && (
          <div className="flex items-center gap-1.5 pt-1 text-[11px] overflow-x-auto no-scrollbar">
            <span className="text-[10px] text-neutral-500 shrink-0">Кандидаты из заявки:</span>
            {plan.candidate_hosts.map((host: string) => {
              const isActive = (params.pc_name || "").toLowerCase() === host.toLowerCase();
              return (
                <button
                  key={host}
                  type="button"
                  onClick={() => handleHostChipClick(host)}
                  className={`px-2 py-0.5 rounded text-[10px] font-mono transition-colors shrink-0 ${
                    isActive
                      ? "bg-blue-600 text-white font-medium shadow-xs"
                      : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700 hover:text-white"
                  }`}
                  title="Нажмите, чтобы переключить целевой ПК"
                >
                  {host}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Editable Scenario & Parameters Form */}
      <div className="space-y-2.5 pt-1">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
          {/* Scenario Selector */}
          <div>
            <label className="block text-[10px] font-semibold text-neutral-400 uppercase tracking-wider mb-1">
              Сценарий решения
            </label>
            <select
              value={scenarioKey}
              onChange={(e) => setScenarioKey(e.target.value)}
              className="w-full bg-[#121418] border border-neutral-800 rounded px-2.5 py-1.5 text-xs text-neutral-100 focus:outline-none focus:border-neutral-500"
            >
              {SCENARIO_OPTIONS.map((opt) => (
                <option key={opt.key} value={opt.key}>
                  {opt.name}
                </option>
              ))}
            </select>
          </div>

          {/* PC Name parameter input */}
          <div>
            <label className="block text-[10px] font-semibold text-neutral-400 uppercase tracking-wider mb-1">
              Имя ПК (Hostname)
            </label>
            <input
              type="text"
              value={params.pc_name || ""}
              onChange={(e) => handleParamChange("pc_name", e.target.value)}
              placeholder="WS-000..."
              className="w-full bg-[#121418] border border-neutral-800 rounded px-2.5 py-1.5 text-xs text-neutral-100 font-mono focus:outline-none focus:border-neutral-500"
            />
          </div>
        </div>

        {/* Dynamic Secondary Parameters */}
        {(scenarioKey === "ad_account_unlock" || scenarioKey === "ad_password_reset") && (
          <div>
            <label className="block text-[10px] font-semibold text-neutral-400 uppercase tracking-wider mb-1">
              Логин Active Directory
            </label>
            <input
              type="text"
              value={params.target_user || ""}
              onChange={(e) => handleParamChange("target_user", e.target.value)}
              placeholder="ivanov.i"
              className="w-full bg-[#121418] border border-neutral-800 rounded px-2.5 py-1.5 text-xs text-neutral-100 font-mono focus:outline-none focus:border-neutral-500"
            />
          </div>
        )}

        {(scenarioKey === "printer_spooler_restart" || scenarioKey === "default_printer_fix") && (
          <div>
            <label className="block text-[10px] font-semibold text-neutral-400 uppercase tracking-wider mb-1">
              Модель / Очередь принтера
            </label>
            <input
              type="text"
              value={params.printer_model || params.printer_name || ""}
              onChange={(e) => handleParamChange("printer_model", e.target.value)}
              placeholder="Kyocera ECOSYS M2040dn"
              className="w-full bg-[#121418] border border-neutral-800 rounded px-2.5 py-1.5 text-xs text-neutral-100 focus:outline-none focus:border-neutral-500"
            />
          </div>
        )}

        {/* Proposed Public Comment to Applicant */}
        <div>
          <label className="block text-[10px] font-semibold text-neutral-400 uppercase tracking-wider mb-1">
            Текст ответа заявителю (при закрытии)
          </label>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
            className="w-full bg-[#121418] border border-neutral-800 rounded p-2 text-xs text-neutral-100 placeholder:text-neutral-500 focus:outline-none focus:border-neutral-500 resize-none leading-relaxed"
          />
        </div>

        {/* Correction Tag & Notes (Only visible if supervisor altered the plan) */}
        {isDirty && (
          <div className="p-2.5 bg-amber-950/20 border border-amber-800/50 rounded space-y-2">
            <div className="flex items-center gap-1.5 text-amber-300 text-xs font-semibold">
              <Edit3 className="w-3.5 h-3.5" />
              <span>Параметры изменены человеком (Ground Truth)</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              <div>
                <label className="block text-[10px] text-neutral-400 uppercase tracking-wider mb-1 font-semibold">
                  Причина исправления (для датасета)
                </label>
                <select
                  value={correctionTag}
                  onChange={(e) => setCorrectionTag(e.target.value)}
                  className="w-full bg-[#14171d] border border-neutral-700/80 rounded px-2 py-1 text-xs text-neutral-200 focus:outline-none"
                >
                  {CORRECTION_TAGS.map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-[10px] text-neutral-400 uppercase tracking-wider mb-1 font-semibold">
                  Заметка оператора (опционально)
                </label>
                <input
                  type="text"
                  value={operatorNotes}
                  onChange={(e) => setOperatorNotes(e.target.value)}
                  placeholder="Заявитель указал не тот номер кабинета..."
                  className="w-full bg-[#14171d] border border-neutral-700/80 rounded px-2 py-1 text-xs text-neutral-200 focus:outline-none"
                />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Footer Execution Actions */}
      <div className="pt-2 border-t border-neutral-800/80 flex items-center justify-between gap-3">
        <div className="text-[11px] text-neutral-500 flex items-center gap-2">
          {isDirty ? (
            <span className="text-amber-400 font-medium flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
              План скорректирован
            </span>
          ) : (
            <span className="text-emerald-400 font-medium flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
              План агента проверен
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {isDirty ? (
            <Button
              variant="secondary"
              loading={isSubmitting}
              onClick={handleCorrect}
              icon={<Zap className="w-3.5 h-3.5 text-amber-400" />}
              className="text-xs bg-amber-600 hover:bg-amber-500 text-neutral-950 font-semibold border-amber-500"
            >
              <span>Скорректировать и обучить</span>
              <KbdBadge shortcut="Ctrl+Enter" />
            </Button>
          ) : (
            <Button
              variant="primary"
              loading={isSubmitting}
              onClick={handleApprove}
              icon={<CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />}
              className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white"
            >
              <span>Одобрить и выполнить</span>
              <KbdBadge shortcut="Enter" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};
