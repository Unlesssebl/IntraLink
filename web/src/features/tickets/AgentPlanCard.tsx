import React from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  GitBranch,
  Pencil,
  Play,
  RefreshCw,
  Route,
  ShieldCheck,
  UserRound,
  Wrench,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { autopilotApi } from "@/features/autopilot/api";
import { Badge, Button, useToast } from "@/shared/ui";
import type { TicketAttachment } from "@/shared/api";
import { useAgentPlan } from "./queries";

export interface AgentPlanCardProps {
  ticketId: number;
  currentStatusId: number;
  attachments?: TicketAttachment[];
  onExecutionStarted?: (commandId: string) => void;
  onClose?: () => void;
}

const CASE_NAMES: Record<string, string> = {
  printer_connection_request: "Подключение принтера",
  printing_incident: "Инцидент печати",
  wireless_access_request: "Доступ к WLAN",
  employee_onboarding: "Онбординг сотрудника",
  access_revocation_request: "Отзыв доступа",
  workstation_unavailable_incident: "Недоступно рабочее место",
  workstation_hardware_diagnostic: "Диагностика оборудования",
  knowledge_request: "Консультация",
  non_it_request: "Непрофильное обращение",
  software_installation_request: "Установка программного обеспечения",
};

export const AgentPlanCard: React.FC<AgentPlanCardProps> = ({ ticketId, onExecutionStarted }) => {
  const { data, isLoading, error, refetch } = useAgentPlan(ticketId);
  const [detailsOpen, setDetailsOpen] = React.useState(false);
  const [caseEditorOpen, setCaseEditorOpen] = React.useState(false);
  const [selectedCaseType, setSelectedCaseType] = React.useState("");
  const [targetEditorOpen, setTargetEditorOpen] = React.useState(false);
  const [selectedTargetService, setSelectedTargetService] = React.useState("");
  const [actionEditorOpen, setActionEditorOpen] = React.useState(false);
  const [factEditorOpen, setFactEditorOpen] = React.useState(false);
  const [factDraft, setFactDraft] = React.useState<Record<string, string>>({});
  const [actionDraft, setActionDraft] = React.useState<Array<{ capability_key: string; params: Record<string, string> }>>([]);
  const [busy, setBusy] = React.useState<string | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();

  const analyze = async (force = false) => {
    setBusy("analyze");
    try {
      await (force ? autopilotApi.reanalyze(ticketId) : autopilotApi.analyze(ticketId));
      await queryClient.invalidateQueries({ queryKey: ["autopilot", "automation", ticketId] });
      toast.success(force ? "Анализ пересобран" : "Заявка проанализирована");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Не удалось выполнить анализ");
    } finally {
      setBusy(null);
    }
  };

  const approve = async () => {
    if (!data) return;
    setBusy("approve");
    try {
      const result = await autopilotApi.approve(ticketId, data);
      if (result.command_id) onExecutionStarted?.(result.command_id);
      await refetch();
      toast.success("ActionPlan подтверждён");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "План не подтверждён");
    } finally {
      setBusy(null);
    }
  };

  const approveRedirect = async () => {
    if (!data?.redirect_plan) return;
    setBusy("approve-redirect");
    try {
      await autopilotApi.approveRedirect(ticketId, data);
      await refetch();
      toast.success("RedirectPlan выполнен");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Перенаправление не подтверждено");
    } finally {
      setBusy(null);
    }
  };

  const stopRedirect = async (mode: "reject" | "manual-takeover") => {
    if (!data?.redirect_plan) return;
    setBusy(`redirect-${mode}`);
    try {
      await autopilotApi.stopRedirect(ticketId, data, mode);
      await refetch();
      toast.success(mode === "reject" ? "RedirectPlan отклонён" : "Заявка передана оператору");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Операция не выполнена");
    } finally {
      setBusy(null);
    }
  };

  const stop = async (mode: "reject" | "manual") => {
    if (!data?.action_plan) return;
    setBusy(mode);
    try {
      if (mode === "reject") await autopilotApi.reject(ticketId, data);
      else await autopilotApi.manualTakeover(ticketId, data);
      await refetch();
      toast.success(mode === "reject" ? "План отклонён" : "Заявка оставлена оператору");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Операция не выполнена");
    } finally {
      setBusy(null);
    }
  };

  const saveCaseCorrection = async () => {
    if (!data || !selectedCaseType) return;
    setBusy("correct-case");
    try {
      await autopilotApi.correctCase(ticketId, data, selectedCaseType);
      setCaseEditorOpen(false);
      await refetch();
      toast.success("Тип обращения исправлен, планы пересобраны");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Тип обращения не изменён");
    } finally {
      setBusy(null);
    }
  };

  const saveTargetCorrection = async () => {
    if (!data || !selectedTargetService) return;
    setBusy("correct-target");
    try {
      await autopilotApi.correctTargetService(ticketId, data, Number(selectedTargetService));
      setTargetEditorOpen(false);
      await refetch();
      toast.success("Целевой сервис подтверждён, анализ пересобран");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Целевой сервис не изменён");
    } finally {
      setBusy(null);
    }
  };

  const openActionEditor = () => {
    if (!data?.action_plan) return;
    setActionDraft(data.action_plan.actions.map((action) => ({
      capability_key: action.capability_key,
      params: Object.fromEntries(Object.entries(action.params).map(([key, value]) => [key, String(value)])),
    })));
    setActionEditorOpen(true);
  };

  const saveActionCorrection = async () => {
    if (!data?.action_plan) return;
    setBusy("correct-action");
    try {
      await autopilotApi.correctActionPlan(ticketId, data, actionDraft);
      setActionEditorOpen(false);
      await refetch();
      toast.success("ActionPlan заменён и повторно проверен");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "ActionPlan не изменён");
    } finally {
      setBusy(null);
    }
  };

  const openFactEditor = () => {
    if (!data) return;
    const keys = new Set(["last_name", "first_name", "middle_name", "department", "title", "phone", "company"]);
    setFactDraft(Object.fromEntries(
      [...keys].map((key) => {
        const sources = data.workflow_plan.fact_provenance?.[key] || [];
        return [
        key,
        String(sources[sources.length - 1]?.value || ""),
        ];
      })
    ));
    setFactEditorOpen(true);
  };

  const saveFactCorrection = async () => {
    if (!data) return;
    setBusy("correct-facts");
    try {
      const facts = Object.fromEntries(Object.entries(factDraft).filter(([, value]) => value.trim()));
      await autopilotApi.correctOnboardingFacts(ticketId, data, facts);
      setFactEditorOpen(false);
      await refetch();
      toast.success("Факты подтверждены оператором, workflow пересобран");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Факты не изменены");
    } finally {
      setBusy(null);
    }
  };

  if (isLoading) {
    return <div className="h-36 animate-pulse rounded-lg border border-neutral-800/80 bg-[#12151c]" />;
  }

  if (error || !data) {
    return (
      <section className="rounded-lg border border-neutral-800/80 bg-[#12151c] p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-neutral-100">Автоматизация ещё не построена</p>
            <p className="mt-1 text-xs text-neutral-500">GET остаётся read-only. Анализ запускается явным действием.</p>
          </div>
          <Button loading={busy === "analyze"} onClick={() => analyze(false)} icon={<Route className="h-3.5 w-3.5" />}>
            Анализировать
          </Button>
        </div>
      </section>
    );
  }

  const decision = data.case_decision;
  const workflow = data.workflow_plan;
  const actionPlan = data.action_plan;
  const compatibility = data.service_compatibility;
  const redirectPlan = data.redirect_plan;
  const selectedName = decision.primary_case_type
    ? CASE_NAMES[decision.primary_case_type] || decision.primary_case_type
    : "Тип обращения не определён";
  const degradationDetails = Object.entries(data.case_frame.degraded_components)
    .map(([component, reason]) => `${component}: ${reason}`)
    .join(", ");
  const preflightPassed = !!actionPlan && actionPlan.actions.every((action) =>
    data.preflight.some((item) => item.action_id === action.id && ["passed", "not_applicable"].includes(item.status))
  );
  const canApprove = actionPlan?.state === "ready"
    && data.approval.state === "pending"
    && preflightPassed
    && data.approval.execution_enabled !== false;
  const targetLabel = compatibility.target_service_path
    || (compatibility.target_selection_state === "ambiguous"
      ? "Несколько возможных сервисов"
      : "Целевой сервис не определён");
  const targetStateLabel: Record<string, string> = {
    source_match: "Текущий сервис совпадает с целевым",
    target_suggested: "Предложен другой целевой сервис",
    ambiguous: "Нужен выбор из нескольких сервисов",
    not_found: "Подходящий сервис не найден",
    unavailable: "Определение сервиса недоступно",
  };
  const workflowLabel = workflow.state === "unsupported"
    ? "Автоматизация не поддерживается"
    : workflow.workflow_key === "workstation_hardware_diagnostic_workflow"
      ? "Диагностика оборудования"
      : workflow.workflow_key;

  return (
    <section className="overflow-hidden rounded-lg border border-neutral-800/80 bg-[#0d0f14]">
      <header className="flex items-start justify-between gap-4 border-b border-neutral-800/80 px-4 py-3.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-violet-400" strokeWidth={1.5} />
            <h3 className="truncate text-sm font-semibold text-neutral-100">{selectedName}</h3>
            <Badge variant={decision.state === "selected" ? "success" : "warning"}>{decision.state}</Badge>
          </div>
          <p className="mt-1 font-mono text-[10px] text-neutral-500">
            {decision.router_version} · {data.snapshot_hash.slice(0, 12)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setSelectedTargetService(String(compatibility.target_service_id || ""));
              setTargetEditorOpen((value) => !value);
            }}
            icon={<Route className="h-3.5 w-3.5" />}
          >
            Исправить сервис
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setSelectedCaseType(decision.primary_case_type || "unknown");
              setCaseEditorOpen((value) => !value);
            }}
            icon={<Pencil className="h-3.5 w-3.5" />}
          >
            Исправить тип
          </Button>
          <Button
            size="sm"
            variant="secondary"
            loading={busy === "analyze"}
            onClick={() => analyze(true)}
            icon={<RefreshCw className="h-3.5 w-3.5" />}
          >
            Пересобрать
          </Button>
        </div>
      </header>

      {targetEditorOpen && (
        <div className="flex flex-wrap items-end gap-3 border-b border-neutral-800/80 bg-[#12151c] px-4 py-3">
          <label className="min-w-64 flex-1 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
            ID конечного сервиса из актуального каталога
            <input
              type="number"
              value={selectedTargetService}
              onChange={(event) => setSelectedTargetService(event.target.value)}
              list={`target-services-${ticketId}`}
              className="mt-1.5 h-9 w-full rounded-md border border-neutral-700 bg-[#0d0f14] px-3 text-xs normal-case tracking-normal text-neutral-100 outline-none focus:border-violet-500"
            />
            <datalist id={`target-services-${ticketId}`}>
              {compatibility.target_candidates.map((candidate) => (
                <option key={candidate.service_id} value={candidate.service_id}>{candidate.service_path}</option>
              ))}
            </datalist>
          </label>
          <Button
            size="sm"
            loading={busy === "correct-target"}
            disabled={!selectedTargetService}
            onClick={saveTargetCorrection}
          >
            Подтвердить и пересобрать
          </Button>
        </div>
      )}

      {caseEditorOpen && (
        <div className="flex flex-wrap items-end gap-3 border-b border-neutral-800/80 bg-[#12151c] px-4 py-3">
          <label className="min-w-64 flex-1 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
            Подтверждённый тип обращения
            <select
              value={selectedCaseType}
              onChange={(event) => setSelectedCaseType(event.target.value)}
              className="mt-1.5 h-9 w-full rounded-md border border-neutral-700 bg-[#0d0f14] px-3 text-xs normal-case tracking-normal text-neutral-100 outline-none focus:border-violet-500"
            >
              {Object.entries(CASE_NAMES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              <option value="unknown">Неизвестный тип</option>
            </select>
          </label>
          <Button size="sm" loading={busy === "correct-case"} onClick={saveCaseCorrection}>Применить и пересобрать</Button>
        </div>
      )}

      <div className="grid gap-px bg-neutral-800/70 md:grid-cols-5">
        <Stage
          icon={<ShieldCheck />}
          label="Текущий сервис"
          value={compatibility.source_service_path || `ID ${compatibility.source_service_id ?? "неизвестен"}`}
          meta={`ID ${compatibility.source_service_id ?? "неизвестен"}`}
        />
        <Stage
          icon={<Route />}
          label="Целевой сервис"
          value={targetLabel}
          meta={targetStateLabel[compatibility.target_selection_state] || compatibility.target_selection_state}
        />
        <Stage
          icon={<GitBranch />}
          label="Обращение"
          value={selectedName}
          meta={[decision.reason_codes.join(", "), degradationDetails].filter(Boolean).join(" · ")}
        />
        <Stage icon={<GitBranch />} label="Workflow" value={workflowLabel} meta={workflow.state} />
        <Stage
          icon={<Wrench />}
          label="Disposition"
          value={workflow.disposition}
          meta={actionPlan ? `${actionPlan.actions.length} действий` : "без технической команды"}
        />
      </div>

      <div className="m-4 grid gap-3 lg:grid-cols-[1.4fr_1fr]">
        <div className="rounded-md border border-neutral-800 bg-[#12151c] p-3 text-xs">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 font-medium text-neutral-200">
              <Route className="h-4 w-4 text-violet-400" strokeWidth={1.5} />
              Решение по маршруту
            </div>
            <Badge variant={compatibility.target_selection_state === "source_match" ? "success" : "warning"} dot>
              {compatibility.target_selection_state}
            </Badge>
          </div>
          <p className="mt-2 text-neutral-300">{targetStateLabel[compatibility.target_selection_state]}</p>
          <p className="mt-1 font-mono text-[10px] text-neutral-500">
            {compatibility.target_selection_method || "метод не указан"}
          </p>
          {compatibility.target_reranker_verdict && (
            <p className="mt-1 text-[10px] leading-4 text-neutral-500">
              reranker: {compatibility.target_reranker_verdict}
              {compatibility.target_reranker_confidence != null
                ? ` · ${Math.round(compatibility.target_reranker_confidence * 100)}%`
                : ""}
              {compatibility.target_reranker_reason ? ` · ${compatibility.target_reranker_reason}` : ""}
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] text-neutral-400">
            <span className="inline-flex items-center gap-1 rounded border border-neutral-800 px-2 py-1">
              <ShieldCheck className="h-3 w-3" /> каталог: {compatibility.catalog_state}
            </span>
            {Object.entries(compatibility.analysis_timings_ms).map(([stage, duration]) => (
              <span key={stage} className="inline-flex items-center gap-1 rounded border border-neutral-800 px-2 py-1">
                <Clock3 className="h-3 w-3" /> {stage}: {duration} мс
              </span>
            ))}
            <span className="rounded border border-neutral-800 px-2 py-1">
              LLM: {compatibility.llm_used ? "использован" : "не потребовался"}
            </span>
          </div>
          {compatibility.target_candidates.length > 0 && (
            <div className="mt-3 space-y-1.5 border-t border-neutral-800 pt-2.5">
              {compatibility.target_candidates.map((candidate) => (
                <div key={candidate.service_id} className="flex items-start justify-between gap-3 rounded border border-neutral-800/80 bg-black/10 px-2.5 py-2">
                  <div>
                    <p className="text-neutral-200">{candidate.service_path}</p>
                    <p className="mt-0.5 text-[10px] text-neutral-500">{candidate.evidence.join(" · ")}</p>
                  </div>
                  <span className="shrink-0 text-right font-mono text-[10px] text-neutral-500">
                    <span>#{candidate.service_id} · текст {Math.round(candidate.score * 100)}%</span>
                    {candidate.service_id === compatibility.target_service_id
                      && compatibility.target_reranker_confidence != null && (
                        <span className="mt-0.5 block text-emerald-400">
                          выбран LLM · {Math.round(compatibility.target_reranker_confidence * 100)}%
                        </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className={`rounded-md border p-3 text-xs ${compatibility.authorization_state === "authorized" ? "border-emerald-800/50 bg-emerald-950/15" : "border-amber-800/50 bg-amber-950/20"}`}>
          <div className="flex items-center gap-2 font-medium text-neutral-200">
            {compatibility.authorization_state === "authorized"
              ? <Check className="h-4 w-4 text-emerald-400" strokeWidth={1.5} />
              : <AlertTriangle className="h-4 w-4 text-amber-400" strokeWidth={1.5} />}
            Разрешение автоматизации
          </div>
          <p className="mt-2 text-neutral-300">{compatibility.authorization_state}</p>
          <p className="mt-1 text-[10px] leading-4 text-neutral-500">
            {compatibility.binding_key
              ? `${compatibility.binding_key}@${compatibility.binding_version}`
              : "Маршрут рассчитан отдельно; подтверждённый binding отсутствует."}
          </p>
        </div>
      </div>

      {compatibility.candidates.length > 0 && compatibility.state !== "compatible" && (
        <div className="mx-4 mb-4 rounded-md border border-amber-800/50 bg-amber-950/20 p-3 text-xs text-amber-100">
          <div className="font-medium">Подтверждённые binding-кандидаты</div>
          <div className="mt-2 space-y-1.5">
            {compatibility.candidates.map((candidate) => (
              <div key={candidate.service_id} className="rounded border border-amber-900/50 bg-black/10 px-2.5 py-2">
                <p className="font-medium">{candidate.service_path}</p>
                <p className="mt-0.5 text-[10px] text-amber-300/70">{candidate.evidence.join(" · ")}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {redirectPlan && (
        <div className="mx-4 my-3 rounded-md border border-violet-800/50 bg-violet-950/10 p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-medium text-neutral-100">RedirectPlan · {redirectPlan.strategy}</p>
              <p className="mt-1 text-[11px] text-neutral-400">{redirectPlan.target_service_path}</p>
            </div>
            <Badge variant={redirectPlan.execution_state === "partial_unknown" ? "danger" : "warning"}>{redirectPlan.execution_state}</Badge>
          </div>
          <p className="mt-2 rounded border border-neutral-800 bg-[#0d0f14] p-2 text-[11px] leading-relaxed text-neutral-300">
            {redirectPlan.rendered_public_comment}
          </p>
        </div>
      )}

      {workflow.missing_facts.length > 0 && (
        <div className="m-4 flex gap-2 rounded-md border border-amber-800/50 bg-amber-950/20 p-3 text-xs text-amber-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.5} />
          <span>Нужно уточнить: {workflow.missing_facts.join(", ")}</span>
        </div>
      )}

      {workflow.workflow_key === "employee_onboarding_workflow" && (
        <div className="mx-4 my-3 rounded-md border border-neutral-800/80 bg-[#12151c] p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-medium text-neutral-100">Факты создания учётной записи</p>
            <span className="font-mono text-[10px] text-neutral-500">
              binding {actionPlan?.service_binding_key || compatibility.binding_key || "—"}@{actionPlan?.service_binding_version || compatibility.binding_version || "—"}
            </span>
          </div>
          <div className="mt-3 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(210px,1fr))]">
            {Object.entries(workflow.fact_provenance || {}).map(([key, sources]) => {
              const latest = sources[sources.length - 1] || {};
              const conflict = workflow.fact_conflicts?.[key];
              return (
                <div key={key} className={`rounded border px-2.5 py-2 ${conflict ? "border-rose-800/60 bg-rose-950/20" : "border-neutral-800 bg-black/10"}`}>
                  <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-wider text-neutral-500">
                    <span>{key}</span>
                    <span className="normal-case tracking-normal">{String(latest.source || "unknown")}</span>
                  </div>
                  <p className="mt-1 text-xs text-neutral-100">{String(latest.value || "")}</p>
                  {conflict && <p className="mt-1 text-[10px] text-rose-300">Конфликт: {conflict.join(" / ")}</p>}
                </div>
              );
            })}
          </div>
          {workflow.active_clarification_id && (
            <p className="mt-2 font-mono text-[10px] text-neutral-500">clarification {workflow.active_clarification_id}</p>
          )}
          {!factEditorOpen ? (
            <Button size="sm" variant="ghost" className="mt-2" onClick={openFactEditor} icon={<Pencil className="h-3.5 w-3.5" />}>
              Подтвердить или исправить факты
            </Button>
          ) : (
            <div className="mt-3 space-y-3 border-t border-neutral-800 pt-3">
              <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(210px,1fr))]">
                {Object.entries(factDraft).map(([key, value]) => (
                  <label key={key} className="text-[10px] uppercase tracking-wider text-neutral-500">
                    {key}
                    <input
                      value={value}
                      onChange={(event) => setFactDraft((current) => ({ ...current, [key]: event.target.value }))}
                      className="mt-1 h-8 w-full rounded-md border border-neutral-700 bg-[#0d0f14] px-2.5 text-xs normal-case tracking-normal text-neutral-100 outline-none focus:border-violet-500"
                    />
                  </label>
                ))}
              </div>
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setFactEditorOpen(false)}>Отмена</Button>
                <Button size="sm" loading={busy === "correct-facts"} onClick={saveFactCorrection}>Сохранить подтверждение</Button>
              </div>
            </div>
          )}
        </div>
      )}

      {actionPlan && (
        <div className="space-y-2 px-4 py-3">
          {actionPlan.actions.map((action) => (
            <div key={action.id} className="flex items-start justify-between gap-4 rounded-md border border-neutral-800 bg-[#12151c] p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-xs font-medium text-neutral-100">
                  <span className="font-mono text-neutral-500">{action.sequence_no + 1}</span>
                  {action.capability_key}
                </div>
                <p className="mt-1 truncate font-mono text-[10px] text-neutral-500">
                  {Object.entries(action.params).map(([key, value]) => `${key}=${String(value)}`).join(" · ")}
                </p>
              </div>
              <Badge variant={action.risk === "high" ? "danger" : "neutral"}>{action.risk}</Badge>
            </div>
          ))}
          {data.preflight.map((item) => (
            <div key={`${item.action_id}-${item.expires_at}`} className="rounded-md border border-neutral-800 bg-black/10 p-3 text-[11px]">
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium text-neutral-200">Preflight · {item.capability_key}</span>
                <Badge variant={item.status === "passed" ? "success" : item.status === "failed" ? "danger" : "warning"}>{item.status}</Badge>
              </div>
              <p className="mt-1 text-neutral-500">{item.checks.join(" · ")}</p>
              {Object.keys(item.details).length > 0 && (
                <div className="mt-2 grid gap-1 font-mono text-[10px] text-neutral-400 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]">
                  {Object.entries(item.details).map(([key, value]) => <span key={key}>{key}={String(value)}</span>)}
                </div>
              )}
              {item.error && <p className="mt-1 text-rose-300">{item.error}</p>}
            </div>
          ))}
          {actionPlan.state === "ready" && !actionEditorOpen && (
            <Button size="sm" variant="ghost" onClick={openActionEditor} icon={<Pencil className="h-3.5 w-3.5" />}>
              Исправить параметры действий
            </Button>
          )}
          {actionEditorOpen && (
            <div className="space-y-3 rounded-md border border-violet-800/50 bg-violet-950/10 p-3">
              <p className="text-xs font-medium text-neutral-100">Коррекция ActionPlan</p>
              {actionDraft.map((action, actionIndex) => (
                <div key={`${action.capability_key}-${actionIndex}`} className="space-y-2">
                  <p className="font-mono text-[10px] text-violet-300">{action.capability_key}</p>
                  <div className="grid gap-2 md:grid-cols-2">
                    {Object.entries(action.params).map(([key, value]) => (
                      <label key={key} className="text-[10px] uppercase tracking-wider text-neutral-500">
                        {key}
                        <input
                          value={value}
                          onChange={(event) => setActionDraft((current) => current.map((item, index) => (
                            index === actionIndex
                              ? { ...item, params: { ...item.params, [key]: event.target.value } }
                              : item
                          )))}
                          className="mt-1 h-8 w-full rounded-md border border-neutral-700 bg-[#0d0f14] px-2.5 font-mono text-xs normal-case tracking-normal text-neutral-100 outline-none focus:border-violet-500"
                        />
                      </label>
                    ))}
                  </div>
                </div>
              ))}
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setActionEditorOpen(false)}>Отмена</Button>
                <Button size="sm" loading={busy === "correct-action"} onClick={saveActionCorrection}>Сохранить новый план</Button>
              </div>
            </div>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={() => setDetailsOpen((value) => !value)}
        className="flex w-full items-center justify-between border-t border-neutral-800/80 px-4 py-2.5 text-left text-xs text-neutral-400 hover:bg-white/[0.02]"
      >
        <span>Доказательства и извлечённые факты</span>
        {detailsOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>
      {detailsOpen && (
        <div className="grid gap-3 border-t border-neutral-800/80 px-4 py-3 md:grid-cols-2">
          <Detail title="Assertions" values={data.case_frame.assertions.map((item) => item.text_span || `${item.key}: ${item.value}`)} />
          <Detail title="Evidence" values={decision.evidence.map((item) => item.text_span || item.source_ref)} />
          <Detail
            title="Service evidence"
            values={[
              ...compatibility.routing_evidence,
              ...compatibility.routing_contradictions,
              ...compatibility.evidence,
              ...compatibility.contradictions,
            ]}
          />
        </div>
      )}

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-neutral-800/80 bg-[#101218] px-4 py-3">
        <div className="flex items-center gap-1.5 text-[11px] text-neutral-500">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} />
          Исполнение только после approval и preflight
          {data.approval.execution_enabled === false && <span className="text-amber-400">· AD execution выключен</span>}
        </div>
        <div className="flex items-center gap-2">
          {actionPlan && data.approval.state === "pending" && (
            <>
              <Button size="sm" variant="ghost" loading={busy === "manual"} onClick={() => stop("manual")} icon={<UserRound className="h-3.5 w-3.5" />}>
                Вручную
              </Button>
              <Button size="sm" variant="secondary" loading={busy === "reject"} onClick={() => stop("reject")}>
                Отклонить
              </Button>
            </>
          )}
          {canApprove && (
            <Button size="sm" loading={busy === "approve"} onClick={approve} icon={<Play className="h-3.5 w-3.5" />}>
              Подтвердить
            </Button>
          )}
          {redirectPlan?.state === "ready" && redirectPlan.approval_state === "pending" && (
            <>
              <Button size="sm" variant="ghost" loading={busy === "redirect-manual-takeover"} onClick={() => stopRedirect("manual-takeover")} icon={<UserRound className="h-3.5 w-3.5" />}>
                Вручную
              </Button>
              <Button size="sm" variant="secondary" loading={busy === "redirect-reject"} onClick={() => stopRedirect("reject")}>Отклонить</Button>
              <Button size="sm" loading={busy === "approve-redirect"} onClick={approveRedirect} icon={<Play className="h-3.5 w-3.5" />}>
                Подтвердить RedirectPlan
              </Button>
            </>
          )}
          {data.approval.state && data.approval.state !== "pending" && (
            <div className="flex items-center gap-1.5 text-xs text-emerald-300">
              <Check className="h-3.5 w-3.5" /> {data.approval.state}
            </div>
          )}
        </div>
      </footer>
    </section>
  );
};

function Stage({ icon, label, value, meta }: { icon: React.ReactElement<{ className?: string; strokeWidth?: number }>; label: string; value: string; meta: string }) {
  return (
    <div className="bg-[#12151c] p-3">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
        {React.cloneElement(icon, { className: "h-3.5 w-3.5", strokeWidth: 1.5 })}
        {label}
      </div>
      <p className="mt-1.5 truncate text-xs font-medium text-neutral-100">{value}</p>
      <p className="mt-0.5 truncate text-[10px] text-neutral-500">{meta || "—"}</p>
    </div>
  );
}

function Detail({ title, values }: { title: string; values: string[] }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">{title}</p>
      <ul className="mt-1.5 space-y-1 text-[11px] text-neutral-300">
        {values.length ? values.map((value, index) => <li key={`${value}-${index}`}>{value}</li>) : <li>Нет данных</li>}
      </ul>
    </div>
  );
}
