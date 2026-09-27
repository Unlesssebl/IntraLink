import React, { useState, useEffect } from "react";
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
  FileText,
  Eye,
  Check,
  X,
  Clock,
  HelpCircle,
  ChevronDown,
  ChevronUp,
  UserCheck,
  Info,
  Layers,
  CheckSquare,
  ShieldCheck,
} from "lucide-react";
import { Button, Badge, KbdBadge, Lightbox, useToast, Modal } from "@/shared/ui";
import { TicketAttachment, ticketsApi } from "@/shared/api";
import { useAgentPlan, useTicketDetail } from "./queries";
import { autopilotApi } from "@/features/autopilot/api";
import {
  AgentPlan,
  ApprovePlanRequest,
  CorrectPlanRequest,
  EvidenceSummary,
  PreflightCheckItem,
  RejectPlanRequest,
  ScenarioCatalogItem,
} from "@/features/autopilot/types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ticketKeys } from "./queries";

export interface AgentPlanCardProps {
  ticketId: number;
  currentStatusId: number;
  attachments?: TicketAttachment[];
  onExecutionStarted?: (commandId: string) => void;
  onClose?: () => void;
}

const CORRECTION_TAG_OPTIONS = [
  { key: "typo", label: "Опечатка в тексте / имени ПК" },
  { key: "wrong_scenario", label: "Неверно определен сценарий" },
  { key: "missing_parameters", label: "Уточнение недостающих параметров" },
  { key: "applicant_changed_request", label: "Заявитель изменил запрос в диалоге" },
  { key: "policy_override", label: "Ручное переопределение регламента" },
  { key: "other", label: "Другая причина" },
];

export const AgentPlanCard: React.FC<AgentPlanCardProps> = ({
  ticketId,
  currentStatusId,
  attachments,
  onExecutionStarted,
  onClose,
}) => {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { data: plan, isLoading, error, refetch } = useAgentPlan(ticketId);
  const { data: ticketDetail } = useTicketDetail(ticketId);

  // Fetch server scenario catalog for correction
  const { data: catalogData } = useQuery({
    queryKey: ["autopilot", "scenario-catalog"],
    queryFn: () => autopilotApi.getScenarioCatalog(),
    staleTime: 60_000,
  });

  const scenarioCatalog: ScenarioCatalogItem[] = catalogData?.scenarios || [];

  const effectiveAttachments: TicketAttachment[] = attachments ?? ticketDetail?.attachments ?? [];

  // Lightbox preview for images
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [lightboxAlt, setLightboxAlt] = useState<string>("");

  // Progressive disclosure accordion state
  const [openSection, setOpenSection] = useState<string | null>(null);

  // Correction Modal State
  const [isCorrectModalOpen, setIsCorrectModalOpen] = useState(false);
  const [selectedScenarioKey, setSelectedScenarioKey] = useState<string>("");
  const [formParams, setFormParams] = useState<Record<string, any>>({});
  const [formComment, setFormComment] = useState<string>("");
  const [correctionTag, setCorrectionTag] = useState<string>("typo");
  const [operatorNotes, setOperatorNotes] = useState<string>("");
  const [formValidationErrors, setFormValidationErrors] = useState<Record<string, string>>({});

  // Reject / Takeover State
  const [isRejectModalOpen, setIsRejectModalOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState<string>("rejected");
  const [rejectNotes, setRejectNotes] = useState<string>("");

  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isAnalyzing, setIsAnalyzing] = useState<boolean>(false);
  const [conflictError, setConflictError] = useState<string | null>(null);

  const toggleSection = (section: string) => {
    setOpenSection((prev) => (prev === section ? null : section));
  };

  const handleOpenCorrection = () => {
    if (!plan) return;
    setSelectedScenarioKey(plan.scenario_key || "install_printer");
    setFormParams(plan.proposed_params ? { ...plan.proposed_params } : {});
    setFormComment(plan.suggested_comment || "");
    setCorrectionTag("typo");
    setOperatorNotes("");
    setFormValidationErrors({});
    setConflictError(null);
    setIsCorrectModalOpen(true);
  };

  const activeScenarioMeta = scenarioCatalog.find((s) => s.scenario_key === selectedScenarioKey);

  const handleFormParamChange = (fieldKey: string, value: any) => {
    setFormParams((prev) => ({ ...prev, [fieldKey]: value }));
    if (formValidationErrors[fieldKey]) {
      setFormValidationErrors((prev) => {
        const copy = { ...prev };
        delete copy[fieldKey];
        return copy;
      });
    }
  };

  // 1. Analyze / Reanalyze
  const handleAnalyze = async (force: boolean) => {
    setIsAnalyzing(true);
    setConflictError(null);
    try {
      if (force) {
        await autopilotApi.reanalyzePlan(ticketId);
        toast.success("План успешно переанализирован");
      } else {
        await autopilotApi.analyzePlan(ticketId);
        toast.success("Анализ доказательного каскада завершен");
      }
      queryClient.invalidateQueries({ queryKey: ticketKeys.detail(ticketId) });
      queryClient.invalidateQueries({ queryKey: ["agentPlan", ticketId] });
      refetch();
    } catch (err: any) {
      toast.error(err.message || "Ошибка при выполнении анализа");
    } finally {
      setIsAnalyzing(false);
    }
  };

  // 2. Approve Plan
  const handleApprove = async () => {
    if (!plan || !plan.plan_id || !plan.decision_id) return;
    setIsSubmitting(true);
    setConflictError(null);

    const payload: ApprovePlanRequest = {
      decision_id: plan.decision_id,
      plan_id: plan.plan_id,
      plan_hash: plan.plan_hash,
      snapshot_hash: plan.snapshot_hash,
      expected_status_id: currentStatusId,
      last_event_id: plan.last_event_id ?? null,
    };

    try {
      const res = await autopilotApi.approvePlan(ticketId, payload);
      toast.success(res.is_duplicate ? "План уже утвержден" : "План утвержден! Команда отправлена на исполнение.");
      queryClient.invalidateQueries({ queryKey: ticketKeys.detail(ticketId) });
      queryClient.invalidateQueries({ queryKey: ["agentPlan", ticketId] });
      if (onExecutionStarted && res.command_id) {
        onExecutionStarted(res.command_id);
      }
    } catch (err: any) {
      if (err.status === 409) {
        setConflictError(err.message || "План устарел или контекст заявки изменился.");
        refetch();
      }
      toast.error(err.message || "Не удалось утвердить план");
    } finally {
      setIsSubmitting(false);
    }
  };

  // 3. Submit Correction
  const handleCorrectSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!plan || !plan.plan_id || !plan.decision_id) return;

    // Validate required editable fields
    if (activeScenarioMeta) {
      const errors: Record<string, string> = {};
      for (const field of activeScenarioMeta.editable_fields) {
        if (field.required && (!formParams[field.field_key] || !String(formParams[field.field_key]).trim())) {
          errors[field.field_key] = `Поле «${field.label}» обязательно для заполнения`;
        }
      }
      if (Object.keys(errors).length > 0) {
        setFormValidationErrors(errors);
        return;
      }
    }

    setIsSubmitting(true);
    setConflictError(null);

    const payload: CorrectPlanRequest = {
      decision_id: plan.decision_id,
      plan_id: plan.plan_id,
      plan_hash: plan.plan_hash,
      snapshot_hash: plan.snapshot_hash,
      expected_status_id: currentStatusId,
      last_event_id: plan.last_event_id ?? null,
      corrected_scenario: selectedScenarioKey,
      corrected_params: formParams,
      corrected_comment: formComment,
      correction_tag: correctionTag,
      operator_notes: operatorNotes.trim() || undefined,
    };

    try {
      const res = await autopilotApi.correctPlan(ticketId, payload);
      toast.success("Корректировка зафиксирована! Команда отправлена на исполнение.");
      setIsCorrectModalOpen(false);
      queryClient.invalidateQueries({ queryKey: ticketKeys.detail(ticketId) });
      queryClient.invalidateQueries({ queryKey: ["agentPlan", ticketId] });
      if (onExecutionStarted && res.command_id) {
        onExecutionStarted(res.command_id);
      }
    } catch (err: any) {
      if (err.status === 409) {
        setConflictError(err.message || "План устарел или проверка параметров не прошла.");
        refetch();
      }
      toast.error(err.message || "Не удалось скорректировать план");
    } finally {
      setIsSubmitting(false);
    }
  };

  // 4. Reject Plan
  const handleReject = async () => {
    if (!plan || !plan.plan_id || !plan.decision_id) return;
    setIsSubmitting(true);
    try {
      const payload: RejectPlanRequest = {
        decision_id: plan.decision_id,
        plan_id: plan.plan_id,
        plan_hash: plan.plan_hash,
        snapshot_hash: plan.snapshot_hash,
        reason_tag: rejectReason,
        operator_notes: rejectNotes.trim() || undefined,
      };
      await autopilotApi.rejectPlan(ticketId, payload);
      toast.success("Предложение плана отклонено");
      setIsRejectModalOpen(false);
      queryClient.invalidateQueries({ queryKey: ticketKeys.detail(ticketId) });
      queryClient.invalidateQueries({ queryKey: ["agentPlan", ticketId] });
      refetch();
    } catch (err: any) {
      toast.error(err.message || "Не удалось отклонить план");
    } finally {
      setIsSubmitting(false);
    }
  };

  // 5. Manual Takeover
  const handleManualTakeover = async () => {
    if (!plan || !plan.plan_id || !plan.decision_id) return;
    setIsSubmitting(true);
    try {
      const result = await autopilotApi.manualTakeover(ticketId, {
        decision_id: plan.decision_id,
        plan_id: plan.plan_id,
        plan_hash: plan.plan_hash,
        snapshot_hash: plan.snapshot_hash,
        reason_tag: "manual_takeover",
        operator_notes: "Заявка оставлена инженеру без применения автоматизации",
      });
      if (result.external_update_succeeded === false) {
        toast.warning(result.warning || "Автоматизация остановлена, но состояние заявки нужно проверить вручную");
      } else {
        toast.success("Заявка оставлена инженеру для ручной обработки");
      }
      queryClient.invalidateQueries({ queryKey: ticketKeys.detail(ticketId) });
      queryClient.invalidateQueries({ queryKey: ["agentPlan", ticketId] });
      refetch();
    } catch (err: any) {
      toast.error(err.message || "Не удалось передать заявку оператору");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Helper renderers
  const renderRoutingBadge = (state: string) => {
    switch (state) {
      case "selected":
        return <Badge variant="success">Сценарий определен</Badge>;
      case "needs_clarification":
        return <Badge variant="warning">Требуется уточнение</Badge>;
      case "ambiguous":
        return <Badge variant="warning">Неоднозначный запрос</Badge>;
      case "unmatched":
        return <Badge variant="neutral">Сценарий не найден</Badge>;
      case "degraded":
        return <Badge variant="danger">Компоненты деградировали</Badge>;
      case "refused":
        return <Badge variant="danger">Отказ регламента</Badge>;
      default:
        return <Badge variant="neutral">{state}</Badge>;
    }
  };

  const renderApprovalStateBadge = (state: string) => {
    switch (state) {
      case "ready_for_approval":
        return <Badge variant="success">Готов к подтверждению</Badge>;
      case "approved":
        return <Badge variant="info">Утвержден оператором</Badge>;
      case "corrected":
        return <Badge variant="info">Скорректирован</Badge>;
      case "rejected":
        return <Badge variant="danger">Отклонен</Badge>;
      case "manual_takeover":
        return <Badge variant="neutral">Передан инженеру</Badge>;
      case "blocked":
        return <Badge variant="warning">Требует доработки</Badge>;
      default:
        return null;
    }
  };

  if (isLoading) {
    return (
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6 text-center animate-pulse text-slate-400">
        <Bot className="w-8 h-8 mx-auto mb-2 text-indigo-400 animate-spin" />
        <p className="text-sm font-medium">Загрузка контекста плана автопилота...</p>
      </div>
    );
  }

  if (error || !plan) {
    return (
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6 text-center text-slate-400">
        <AlertTriangle className="w-8 h-8 mx-auto mb-2 text-amber-400" />
        <p className="text-sm">Не удалось загрузить план решения.</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => refetch()}>
          Повторить запрос
        </Button>
      </div>
    );
  }

  const isNotAnalyzed = plan.analysis_state === "not_analyzed";
  const isInProgress = plan.analysis_state === "in_progress";

  return (
    <div className="bg-slate-900/90 border border-slate-800 rounded-xl shadow-xl overflow-hidden text-slate-200">
      {/* 1. Header with Scenario, Status and Freshness */}
      <div className="px-5 py-4 border-b border-slate-800 bg-slate-950/40 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-slate-100">
                {plan.scenario_name || "Автономный анализ заявки"}
              </h3>
              {renderRoutingBadge(plan.routing_state)}
              {renderApprovalStateBadge(plan.approval_state)}
            </div>
            <p className="text-xs text-slate-400 mt-0.5">{plan.description}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {plan.is_stale && (
            <Badge variant="warning" className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              План устарел
            </Badge>
          )}
          {plan.is_circuit_broken && (
            <Badge variant="danger" className="flex items-center gap-1">
              <ShieldAlert className="w-3 h-3" />
              Circuit Breaker
            </Badge>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => handleAnalyze(true)}
            disabled={isAnalyzing}
            className="text-xs text-slate-400 hover:text-slate-200"
          >
            <RefreshCw className={`w-3.5 h-3.5 mr-1 ${isAnalyzing ? "animate-spin" : ""}`} />
            Переанализировать
          </Button>
        </div>
      </div>

      {/* 409 Conflict Alert */}
      {conflictError && (
        <div className="mx-5 mt-4 p-3.5 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-start justify-between gap-3">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-xs font-semibold text-amber-300">Состояние плана изменилось (409 Conflict)</p>
              <p className="text-xs text-amber-400/90 mt-0.5">{conflictError}</p>
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={() => handleAnalyze(true)} className="text-xs shrink-0">
            Обновить анализ
          </Button>
        </div>
      )}

      {/* 2. Main Executive Summary */}
      <div className="p-5 space-y-4">
        {isNotAnalyzed ? (
          <div className="text-center py-6">
            <Bot className="w-10 h-10 mx-auto text-slate-600 mb-2" />
            <p className="text-sm text-slate-300 font-medium">Анализ доказательного каскада еще не выполнялся</p>
            <p className="text-xs text-slate-500 mt-1 max-w-md mx-auto">
              Нажмите кнопку «Анализировать», чтобы извлечь параметры, сопоставить профили каталога и выполнить preflight-проверки.
            </p>
            <Button
              variant="primary"
              size="sm"
              onClick={() => handleAnalyze(false)}
              disabled={isAnalyzing}
              className="mt-4"
            >
              <Sparkles className="w-4 h-4 mr-1.5" />
              Запустить анализ заявки
            </Button>
          </div>
        ) : isInProgress ? (
          <div className="text-center py-6">
            <RefreshCw className="w-8 h-8 mx-auto text-indigo-400 animate-spin mb-2" />
            <p className="text-sm font-medium text-slate-200">Выполняется доказательный анализ заявки...</p>
            <p className="text-xs text-slate-500 mt-1">Опрос источников, эмбеддингов и верификатора</p>
          </div>
        ) : (
          <>
            {/* Target & Action Summary Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5 bg-slate-950/30 border border-slate-800/80 rounded-lg p-4">
              <div>
                <span className="text-xs font-medium text-slate-400 block mb-1">Предлагаемое действие</span>
                <div className="flex items-center gap-2 text-sm text-slate-200 font-medium">
                  <Terminal className="w-4 h-4 text-indigo-400" />
                  <span>{plan.proposed_action || plan.scenario_key}</span>
                </div>
              </div>

              <div>
                <span className="text-xs font-medium text-slate-400 block mb-1">Целевой объект / Параметры</span>
                <div className="text-xs text-slate-300 space-y-0.5">
                  {Object.entries(plan.proposed_params || {}).length > 0 ? (
                    Object.entries(plan.proposed_params).map(([k, v]) => (
                      <div key={k} className="flex items-center gap-1.5 font-mono">
                        <span className="text-slate-500">{k}:</span>
                        <span className="text-indigo-300">{String(v)}</span>
                      </div>
                    ))
                  ) : (
                    <span className="text-slate-500 italic">Параметры не требуются</span>
                  )}
                </div>
              </div>
            </div>

            {/* Blockers & Missing Facts Alert */}
            {plan.blocking_reason_codes && plan.blocking_reason_codes.length > 0 && (
              <div className="p-3.5 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-300 space-y-1">
                <div className="flex items-center gap-1.5 font-semibold text-red-200">
                  <AlertTriangle className="w-4 h-4 text-red-400" />
                  <span>Обнаружены блокирующие факторы для прямого исполнения:</span>
                </div>
                <ul className="list-disc list-inside space-y-0.5 pl-2 text-red-300/90">
                  {plan.missing_facts && plan.missing_facts.length > 0 && (
                    <li>Не указаны обязательные реквизиты: <b>{plan.missing_facts.join(", ")}</b></li>
                  )}
                  {plan.blocking_reason_codes.map((code) => {
                    if (code === "missing_facts") return null;
                    if (code === "not_selected") return <li key={code}>Сценарий не выбран однозначно</li>;
                    if (code === "scenario_disabled") return <li key={code}>Сценарий отключен политикой</li>;
                    if (code === "circuit_breaker_tripped") return <li key={code}>Сработал защитный предохранитель</li>;
                    if (code === "preflight_missing") return <li key={code}>Отсутствует результат preflight-проверки</li>;
                    if (code === "preflight_expired") return <li key={code}>Preflight-проверка устарела (TTL 120с)</li>;
                    if (code === "preflight_failed") return <li key={code}>Сетевые/доменные проверки не прошли</li>;
                    if (code === "preflight_degraded") return <li key={code}>Компоненты проверки деградировали</li>;
                    if (code === "plan_stale") return <li key={code}>План устарел по отношению к заявке</li>;
                    if (code === "terminal_feedback_present") return <li key={code}>Для плана уже зафиксировано финальное решение</li>;
                    return <li key={code}>{code}</li>;
                  })}
                </ul>
              </div>
            )}

            {/* Preflight Quick Status Banner */}
            {plan.preflight && (
              <div className="flex items-center justify-between px-3.5 py-2.5 rounded-lg bg-slate-950/40 border border-slate-800 text-xs">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-emerald-400" />
                  <span className="font-medium text-slate-300">Preflight-проверки среды:</span>
                  <Badge variant={plan.preflight.status === "passed" ? "success" : plan.preflight.status === "degraded" ? "warning" : "danger"}>
                    {plan.preflight.status}
                  </Badge>
                </div>
                {plan.preflight.expires_at && (
                  <span className="text-slate-500 font-mono text-[11px]">
                    TTL до: {new Date(plan.preflight.expires_at).toLocaleTimeString()}
                  </span>
                )}
              </div>
            )}

            {/* Command Status if created */}
            {plan.command_id && (
              <div className="p-3 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-indigo-400" />
                  <span className="text-slate-300 font-medium">Команда исполнения:</span>
                  <Badge variant={plan.command_status === "succeeded" ? "success" : plan.command_status === "failed" ? "danger" : "info"}>
                    {plan.command_status || "создана"}
                  </Badge>
                </div>
                <span className="font-mono text-[11px] text-slate-400">{plan.command_id.slice(0, 8)}...</span>
              </div>
            )}

            {/* 3. Action Buttons */}
            <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-800/80">
              <Button
                variant="primary"
                size="sm"
                onClick={handleApprove}
                disabled={!plan.can_approve || isSubmitting}
                className="bg-emerald-600 hover:bg-emerald-500 text-white"
              >
                <Check className="w-4 h-4 mr-1.5" />
                Подтвердить план
              </Button>

              <Button
                variant="outline"
                size="sm"
                onClick={handleOpenCorrection}
                disabled={!plan.can_correct || isSubmitting}
                className="border-slate-700 text-slate-200 hover:bg-slate-800"
              >
                <Edit3 className="w-3.5 h-3.5 mr-1.5 text-indigo-400" />
                Исправить
              </Button>

              <Button
                variant="ghost"
                size="sm"
                onClick={() => setIsRejectModalOpen(true)}
                disabled={!plan.can_reject || isSubmitting}
                className="text-slate-400 hover:text-red-300 hover:bg-red-500/10"
              >
                <X className="w-3.5 h-3.5 mr-1 text-red-400" />
                Отклонить
              </Button>

              <Button
                variant="ghost"
                size="sm"
                onClick={handleManualTakeover}
                disabled={!plan.can_reject || isSubmitting}
                className="text-slate-400 hover:text-amber-300 hover:bg-amber-500/10 ml-auto"
              >
                <UserCheck className="w-3.5 h-3.5 mr-1 text-amber-400" />
                Оставить оператору
              </Button>
            </div>
          </>
        )}
      </div>

      {/* 4. Progressive Disclosure Accordions */}
      {!isNotAnalyzed && !isInProgress && (
        <div className="border-t border-slate-800 bg-slate-950/20 divide-y divide-slate-800/60 text-xs">
          {/* Section A: Why scenario was selected (Reason codes) */}
          <div>
            <button
              onClick={() => toggleSection("reasons")}
              className="w-full px-5 py-3 flex items-center justify-between text-left text-slate-300 hover:bg-slate-800/30 transition-colors"
            >
              <span className="font-medium flex items-center gap-2">
                <Info className="w-3.5 h-3.5 text-indigo-400" />
                Почему выбран сценарий (Обоснование)
              </span>
              {openSection === "reasons" ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
            </button>
            {openSection === "reasons" && (
              <div className="px-5 pb-4 pt-1 space-y-2 text-slate-400">
                <div className="flex flex-wrap gap-1.5">
                  {plan.decision_reason_codes && plan.decision_reason_codes.length > 0 ? (
                    plan.decision_reason_codes.map((code) => (
                      <Badge key={code} variant="neutral" className="font-mono text-[11px]">
                        {code}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-slate-500 italic">Коды решения не зафиксированы</span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Section B: Evidence Summaries */}
          <div>
            <button
              onClick={() => toggleSection("evidence")}
              className="w-full px-5 py-3 flex items-center justify-between text-left text-slate-300 hover:bg-slate-800/30 transition-colors"
            >
              <span className="font-medium flex items-center gap-2">
                <Layers className="w-3.5 h-3.5 text-indigo-400" />
                Доказательства каскада ({plan.evidence_summaries?.length || 0})
              </span>
              {openSection === "evidence" ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
            </button>
            {openSection === "evidence" && (
              <div className="px-5 pb-4 pt-1 space-y-2">
                {plan.evidence_summaries && plan.evidence_summaries.length > 0 ? (
                  plan.evidence_summaries.map((ev: EvidenceSummary, idx: number) => (
                    <div
                      key={idx}
                      className="p-2.5 rounded bg-slate-900/60 border border-slate-800/80 flex items-start justify-between gap-2"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <Badge variant="neutral" className="text-[10px] uppercase">
                            {ev.source_type}
                          </Badge>
                          <span className="font-medium text-slate-200 text-xs">{ev.reason_code}</span>
                        </div>
                        <p className="text-slate-400 mt-1">{ev.description}</p>
                      </div>
                      <span className="text-slate-500 text-[10px] font-mono shrink-0">{ev.provider}</span>
                    </div>
                  ))
                ) : (
                  <p className="text-slate-500 italic">Доказательства не сформированы</p>
                )}
              </div>
            )}
          </div>

          {/* Section C: Preflight Checks Detail */}
          <div>
            <button
              onClick={() => toggleSection("preflight")}
              className="w-full px-5 py-3 flex items-center justify-between text-left text-slate-300 hover:bg-slate-800/30 transition-colors"
            >
              <span className="font-medium flex items-center gap-2">
                <CheckSquare className="w-3.5 h-3.5 text-emerald-400" />
                Проверки перед запуском (Preflight)
              </span>
              {openSection === "preflight" ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
            </button>
            {openSection === "preflight" && (
              <div className="px-5 pb-4 pt-1 space-y-2">
                {plan.preflight?.checks && plan.preflight.checks.length > 0 ? (
                  plan.preflight.checks.map((chk: PreflightCheckItem, idx: number) => (
                    <div
                      key={idx}
                      className="p-2 rounded bg-slate-900/60 border border-slate-800/80 flex items-center justify-between text-xs"
                    >
                      <div className="flex items-center gap-2">
                        {chk.status === "passed" ? (
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                        ) : chk.status === "warning" ? (
                          <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                        ) : (
                          <X className="w-3.5 h-3.5 text-red-400" />
                        )}
                        <span className="text-slate-200 font-mono">{chk.name}</span>
                        {chk.message && <span className="text-slate-400">— {chk.message}</span>}
                      </div>
                      <Badge variant={chk.status === "passed" ? "success" : chk.status === "warning" ? "warning" : "danger"}>
                        {chk.status}
                      </Badge>
                    </div>
                  ))
                ) : (
                  <p className="text-slate-500 italic">Preflight-проверки не выполнялись</p>
                )}
              </div>
            )}
          </div>

          {/* Section D: Technical Provenance (UUIDs & Hashes) */}
          <div>
            <button
              onClick={() => toggleSection("technical")}
              className="w-full px-5 py-3 flex items-center justify-between text-left text-slate-300 hover:bg-slate-800/30 transition-colors"
            >
              <span className="font-medium flex items-center gap-2">
                <Terminal className="w-3.5 h-3.5 text-slate-400" />
                Технические сведения (Audit & Hashes)
              </span>
              {openSection === "technical" ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
            </button>
            {openSection === "technical" && (
              <div className="px-5 pb-4 pt-1 space-y-1.5 font-mono text-[11px] text-slate-400 bg-slate-950/60 p-3 rounded m-5">
                <div><span className="text-slate-600">Decision ID:</span> <span className="text-slate-300">{plan.decision_id || "null"}</span></div>
                <div><span className="text-slate-600">Plan ID:</span> <span className="text-slate-300">{plan.plan_id || "null"}</span></div>
                <div><span className="text-slate-600">Plan Hash:</span> <span className="text-slate-300">{plan.plan_hash || "null"}</span></div>
                <div><span className="text-slate-600">Snapshot Hash:</span> <span className="text-slate-300">{plan.snapshot_hash || "null"}</span></div>
                <div><span className="text-slate-600">Last Event ID:</span> <span className="text-slate-300">{plan.last_event_id ?? "null"}</span></div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 5. Correction Modal with Server Catalog Schema */}
      {isCorrectModalOpen && (
        <Modal
          isOpen={isCorrectModalOpen}
          onClose={() => setIsCorrectModalOpen(false)}
          title="Корректировка плана автопилота"
          maxWidth="lg"
        >
          <form onSubmit={handleCorrectSubmit} className="space-y-4 text-slate-200">
            {/* Scenario Selector from Server Catalog */}
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">
                Выберите целевой сценарий
              </label>
              <select
                value={selectedScenarioKey}
                onChange={(e) => {
                  const newKey = e.target.value;
                  setSelectedScenarioKey(newKey);
                  // Pre-seed comment from meta if available
                  const found = scenarioCatalog.find((s) => s.scenario_key === newKey);
                  if (found) {
                    setFormComment("");
                  }
                }}
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                {scenarioCatalog.map((s) => (
                  <option key={s.scenario_key} value={s.scenario_key} disabled={!s.is_enabled}>
                    {s.name} {!s.is_enabled ? `(${s.disabled_reason || "Отключен"})` : ""}
                  </option>
                ))}
              </select>
              {activeScenarioMeta && (
                <p className="text-xs text-slate-400 mt-1">{activeScenarioMeta.description}</p>
              )}
            </div>

            {/* Dynamic Typed Editable Fields */}
            {activeScenarioMeta && activeScenarioMeta.editable_fields.length > 0 && (
              <div className="space-y-3 p-3.5 bg-slate-950/40 rounded-lg border border-slate-800">
                <span className="text-xs font-semibold text-slate-300 block mb-1">
                  Параметры выполнения сценария
                </span>
                {activeScenarioMeta.editable_fields.map((field) => (
                  <div key={field.field_key}>
                    <label className="block text-xs font-medium text-slate-300 mb-1">
                      {field.label} {field.required && <span className="text-red-400">*</span>}
                    </label>
                    <input
                      type={field.field_type === "integer" ? "number" : "text"}
                      value={formParams[field.field_key] || ""}
                      onChange={(e) => handleFormParamChange(field.field_key, e.target.value)}
                      placeholder={field.hint || ""}
                      className={`w-full bg-slate-900 border ${
                        formValidationErrors[field.field_key] ? "border-red-500" : "border-slate-700"
                      } rounded-lg px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-indigo-500 font-mono`}
                    />
                    {formValidationErrors[field.field_key] && (
                      <p className="text-xs text-red-400 mt-0.5">{formValidationErrors[field.field_key]}</p>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Reason Tag (Mandatory) */}
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">
                Причина корректировки <span className="text-red-400">*</span>
              </label>
              <select
                value={correctionTag}
                onChange={(e) => setCorrectionTag(e.target.value)}
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                {CORRECTION_TAG_OPTIONS.map((tag) => (
                  <option key={tag.key} value={tag.key}>
                    {tag.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Operator Notes */}
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">
                Заметки оператора (необязательно)
              </label>
              <textarea
                value={operatorNotes}
                onChange={(e) => setOperatorNotes(e.target.value)}
                rows={2}
                placeholder="Пояснение для калибровки и обучения..."
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>

            {/* Modal Actions */}
            <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
              <Button variant="ghost" size="sm" onClick={() => setIsCorrectModalOpen(false)}>
                Отмена
              </Button>
              <Button variant="primary" size="sm" type="submit" disabled={isSubmitting}>
                <Send className="w-4 h-4 mr-1.5" />
                Сохранить и исполнить
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* 6. Reject Confirmation Modal */}
      {isRejectModalOpen && (
        <Modal
          isOpen={isRejectModalOpen}
          onClose={() => setIsRejectModalOpen(false)}
          title="Отклонение плана автопилота"
          maxWidth="md"
        >
          <div className="space-y-4 text-slate-200">
            <p className="text-xs text-slate-400">
              Предложенный сценарий будет отклонен и зафиксирован в калибровочном датасете как неприменимый. Команда исполнения создана не будет.
            </p>
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">Причина отклонения</label>
              <select
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                <option value="rejected">Неприменимо к данной заявке</option>
                <option value="not_it_task">Не является задачей техподдержки</option>
                <option value="policy_prohibited">Запрещено регламентом</option>
                <option value="requires_manual_inspection">Требуется физический выезд / осмотр</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">Заметки оператора</label>
              <textarea
                value={rejectNotes}
                onChange={(e) => setRejectNotes(e.target.value)}
                rows={2}
                placeholder="Причина отклонения..."
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>
            <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
              <Button variant="ghost" size="sm" onClick={() => setIsRejectModalOpen(false)}>
                Отмена
              </Button>
              <Button variant="danger" size="sm" onClick={handleReject} disabled={isSubmitting}>
                Отклонить предложение
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {/* Lightbox Modal for Image Attachments */}
      {lightboxSrc && (
        <Lightbox
          src={lightboxSrc}
          alt={lightboxAlt}
          isOpen={Boolean(lightboxSrc)}
          onClose={() => setLightboxSrc(null)}
        />
      )}
    </div>
  );
};
