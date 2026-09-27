import React from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
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
  knowledge_request: "Консультация",
  non_it_request: "Непрофильное обращение",
};

export const AgentPlanCard: React.FC<AgentPlanCardProps> = ({ ticketId, onExecutionStarted }) => {
  const { data, isLoading, error, refetch } = useAgentPlan(ticketId);
  const [detailsOpen, setDetailsOpen] = React.useState(false);
  const [caseEditorOpen, setCaseEditorOpen] = React.useState(false);
  const [selectedCaseType, setSelectedCaseType] = React.useState("");
  const [actionEditorOpen, setActionEditorOpen] = React.useState(false);
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
  const selectedName = decision.primary_case_type
    ? CASE_NAMES[decision.primary_case_type] || decision.primary_case_type
    : "Тип обращения не определён";
  const degradationDetails = Object.entries(data.case_frame.degraded_components)
    .map(([component, reason]) => `${component}: ${reason}`)
    .join(", ");
  const canApprove = actionPlan?.state === "ready" && data.approval.state === "pending";

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

      <div className="grid gap-px bg-neutral-800/70 md:grid-cols-3">
        <Stage
          icon={<Route />}
          label="Обращение"
          value={selectedName}
          meta={[decision.reason_codes.join(", "), degradationDetails].filter(Boolean).join(" · ")}
        />
        <Stage icon={<GitBranch />} label="Workflow" value={workflow.workflow_key} meta={workflow.state} />
        <Stage
          icon={<Wrench />}
          label="Disposition"
          value={workflow.disposition}
          meta={actionPlan ? `${actionPlan.actions.length} действий` : "без технической команды"}
        />
      </div>

      {workflow.missing_facts.length > 0 && (
        <div className="m-4 flex gap-2 rounded-md border border-amber-800/50 bg-amber-950/20 p-3 text-xs text-amber-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.5} />
          <span>Нужно уточнить: {workflow.missing_facts.join(", ")}</span>
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
        </div>
      )}

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-neutral-800/80 bg-[#101218] px-4 py-3">
        <div className="flex items-center gap-1.5 text-[11px] text-neutral-500">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} />
          Исполнение только после approval и preflight
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
