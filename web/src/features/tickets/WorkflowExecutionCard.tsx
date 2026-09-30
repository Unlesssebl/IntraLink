import React from "react";
import {
  AlertCircle,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Circle,
  FolderLock,
  FolderTree,
  Loader2,
  Pencil,
  Printer,
  RefreshCw,
  Route,
  ShieldCheck,
  Sparkles,
  UserRoundPlus,
  Wifi,
  Wrench,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { autopilotApi } from "@/features/autopilot/api";
import { SuggestedCorrectionsBanner } from "@/features/autopilot/components";
import type { HumanActionRequest, TicketAutomation } from "@/features/autopilot/types";
import { Badge, Button, useToast } from "@/shared/ui";

function WorkflowIcon({ icon, className }: { icon?: string; className?: string }) {
  switch (icon) {
    case "user":
      return <UserRoundPlus className={className} />;
    case "printer":
      return <Printer className={className} />;
    case "network":
      return <Wifi className={className} />;
    case "folder":
      return <FolderLock className={className} />;
    case "wrench":
      return <Wrench className={className} />;
    default:
      return <Route className={className} />;
  }
}

const FACT_LABELS: Record<string, string> = {
  // Identity / Onboarding
  last_name: "Фамилия",
  first_name: "Имя",
  middle_name: "Отчество",
  department: "Подразделение",
  title: "Должность",
  phone: "Телефон",
  company: "Организация",
  target_user: "Целевой пользователь",
  // Hardware & Printing
  pc_name: "Имя компьютера (ПК)",
  printer_address: "Сетевой адрес / очередь принтера",
  printer_model: "Модель принтера",
  connection_type: "Тип подключения",
  // Network & Shared Resources
  folder_path: "Сетевой путь (UNC)",
  access_level: "Уровень доступа",
};

const STAGE_LABELS: Record<string, string> = {
  facts: "Параметры",
  ou: "Подразделение (OU)",
  hitl: "Уточнение параметров",
  preflight: "Проверки",
  approval: "Одобрение",
  execution: "Выполнение",
  delivery: "Выдача реквизитов",
};

const BLOCKER_LABELS: Record<string, string> = {
  required_facts_missing: "Не получены обязательные параметры операции",
  human_action_required: "Требуется решение оператора по параметрам выполнения",
  clarification_publish_failed: "Вопрос заявителю не опубликован",
  preflight_failed: "Предварительные проверки перед выполнением не пройдены",
  ad_onboarding_execution_disabled: "Создание пользователей AD временно отключено администратором",
  capability_execution_disabled: "Автоматическое выполнение возможностей сценария отключено настройками безопасности",
  worker_unavailable: "Отсутствует активный воркер для выполнения операции",
  no_compatible_worker: "Нет совместимого активного воркера",
};

const CHECK_LABELS: Record<string, string> = {
  identity_preflight: "Подключение к Active Directory",
  required_identity_fields: "Обязательные данные сотрудника",
  ad_available: "Подключение к Active Directory",
  target_ou: "Целевое подразделение (OU)",
  target_ou_exists: "Целевое подразделение существует в AD",
  distinguished_name_absent: "Объект с таким DN отсутствует",
  dn_absent: "Объект с таким DN отсутствует",
  login_preview: "Предварительный логин рассчитан",
  login_preview_available: "Предварительный логин рассчитан",
  intraservice_available: "IntraService доступен",
  credential_delivery_configured: "Политика защищённой доставки настроена",
  pc_reachability: "Доступность рабочего места",
  target_host_online: "Рабочее место в сети",
  printer_port_accessible: "Сетевой порт принтера доступен",
  driver_available: "Драйвер принтера доступен",
  permissions: "Права на удалённое выполнение",
  winrm_available: "Служба WinRM/WMI доступна",
};

export interface WorkflowExecutionCardProps {
  ticketId: number;
  applicantName?: string | null;
  data: TicketAutomation;
  refetch: () => Promise<unknown>;
  onExecutionStarted?: (commandId: string) => void;
}

function sanitizeTechnicalJson(data: unknown): unknown {
  if (data === null || data === undefined) return data;
  if (typeof data !== "object") return data;
  if (Array.isArray(data)) return data.map(sanitizeTechnicalJson);

  const clean: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
    const lower = key.toLowerCase();
    if (
      lower.includes("password") ||
      lower.includes("secret") ||
      lower.includes("auth_b64") ||
      lower.includes("token")
    ) {
      clean[key] = "[REDACTED_SECRET]";
    } else {
      clean[key] = sanitizeTechnicalJson(value);
    }
  }
  return clean;
}

function factValue(data: TicketAutomation, key: string): string {
  const provenance = data.workflow_plan.fact_provenance?.[key] || [];
  const latest = provenance[provenance.length - 1];
  return String(latest?.value || data.case_frame.entities[key] || "");
}

function employeeFullName(data: TicketAutomation): string {
  return (
    ["last_name", "first_name", "middle_name"]
      .map((k) => factValue(data, k))
      .filter(Boolean)
      .join(" ") || "Не указан"
  );
}

export const WorkflowExecutionCard: React.FC<WorkflowExecutionCardProps> = ({
  ticketId,
  applicantName,
  data,
  refetch,
  onExecutionStarted,
}) => {
  const [busy, setBusy] = React.useState<string | null>(null);
  const [factsOpen, setFactsOpen] = React.useState(false);
  const [technicalOpen, setTechnicalOpen] = React.useState(false);
  const [correctionsOpen, setCorrectionsOpen] = React.useState(false);
  const [selectedCandidates, setSelectedCandidates] = React.useState<Record<string, string>>({});
  const [caseType, setCaseType] = React.useState(
    data.case_decision.primary_case_type || "employee_onboarding"
  );
  const [serviceId, setServiceId] = React.useState(
    String(data.service_compatibility.target_service_id || "")
  );
  const [factDraft, setFactDraft] = React.useState<Record<string, string>>(
    Object.fromEntries(Object.keys(FACT_LABELS).map((key) => [key, factValue(data, key)]))
  );

  const toast = useToast();
  const queryClient = useQueryClient();
  const readiness = data.readiness;
  const actions = new Set(readiness?.actions || []);

  const pendingHumanActions: HumanActionRequest[] = (data.human_actions || []).filter(
    (ha) => ha.state === "pending"
  );

  const run = async (key: string, operation: () => Promise<unknown>, success: string) => {
    setBusy(key);
    try {
      await operation();
      await queryClient.invalidateQueries({ queryKey: ["autopilot", "automation", ticketId] });
      await refetch();
      toast.success(success);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Операция не выполнена");
    } finally {
      setBusy(null);
    }
  };

  const saveFacts = () =>
    run(
      "facts",
      () =>
        autopilotApi.correctFacts(
          ticketId,
          data,
          Object.fromEntries(Object.entries(factDraft).filter(([, val]) => val.trim()))
        ),
      "Параметры сохранены, проверки запущены"
    ).then(() => setFactsOpen(false));

  const resolveHumanAction = (action: HumanActionRequest) => {
    const candidateId = selectedCandidates[action.id] || action.candidates[0]?.candidate_id;
    if (!candidateId) {
      toast.error("Выберите вариант из списка");
      return;
    }
    return run(
      `hitl-${action.id}`,
      () =>
        autopilotApi.resolveHumanAction(
          ticketId,
          action.id,
          candidateId,
          data.snapshot_hash,
          data.action_plan?.plan_hash
        ),
      "Выбор подтверждён, параметры плана обновлены"
    );
  };

  const approve = () =>
    run(
      "approve",
      async () => {
        const result = await autopilotApi.approve(ticketId, data);
        if (result.command_id) onExecutionStarted?.(result.command_id);
      },
      "Выполнение плана одобрено"
    );

  const reject = () =>
    run(
      "reject",
      () => autopilotApi.reject(ticketId, data, "Оператор отклонил автоматическое выполнение"),
      "Автоматизация отклонена; статус заявки сохранён"
    );

  const retryClarification = () => {
    const clarificationId = readiness?.clarification?.id;
    if (!clarificationId) return Promise.resolve();
    return run(
      "retry_clarification",
      () => autopilotApi.retryClarification(ticketId, data, clarificationId),
      "Вопрос заявителю повторно отправлен"
    );
  };

  const mainBlocker = readiness?.blockers[0];
  const targetOu = data.action_plan?.actions[0]?.params?.target_ou_dn;
  const previewLogin =
    data.preflight[0]?.details?.preview_sam_account_name ||
    data.preflight[0]?.details?.sam_account_name ||
    data.preflight[0]?.details?.login;

  const isCompleted = readiness?.state === "completed";
  const isReady = readiness?.state === "ready_for_approval";
  const isBlocked = readiness?.state === "blocked";

  return (
    <section className="overflow-hidden rounded-xl border border-neutral-800 bg-[#0d0f13] shadow-2xl">
      {/* Header */}
      <header className="flex items-start justify-between gap-4 border-b border-neutral-800 px-5 py-4">
        <div className="flex min-w-0 gap-3">
          <div className="mt-0.5 rounded-lg border border-violet-500/20 bg-violet-500/10 p-2 text-violet-300">
            <WorkflowIcon icon={data.context_header?.icon} className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold tracking-tight text-neutral-100">
                {data.context_header?.title || data.workflow_plan.workflow_key}
              </h3>
              {data.context_header?.category_badge && (
                <Badge variant="neutral">{data.context_header.category_badge}</Badge>
              )}
            </div>
            <p className="mt-0.5 text-xs text-neutral-400">
              Workflow: <span className="font-mono text-neutral-300">{data.workflow_plan.workflow_key}</span> · регламент автоматизации
            </p>
          </div>
        </div>
        <Badge
          variant={isCompleted ? "success" : isReady ? "warning" : isBlocked ? "danger" : "neutral"}
          dot
        >
          {isCompleted
            ? "Выполнено"
            : isReady
            ? "Готово к одобрению"
            : isBlocked
            ? "Требуется действие"
            : "В процессе"}
        </Badge>
      </header>

      {/* Metadata Overview Grid */}
      <div className="grid gap-px bg-neutral-800 md:grid-cols-2">
        <div className="bg-[#101217] px-5 py-3.5">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
            Заявитель
          </p>
          <p className="mt-1 text-sm font-medium text-neutral-100">
            {applicantName || "Не определён"}
          </p>
          <p className="mt-0.5 text-xs text-neutral-500">
            Инициатор тикета
          </p>
        </div>
        <div className="bg-[#101217] px-5 py-3.5">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
            {data.context_header?.subject_label || "Параметры"}
          </p>
          <p className="mt-1 text-sm font-medium text-neutral-100">
            {data.context_header?.subject_value || employeeFullName(data)}
          </p>
          <p className="mt-0.5 text-xs text-neutral-400">
            {data.context_header?.subject_caption ||
              [factValue(data, "department"), factValue(data, "title")].filter(Boolean).join(" · ") ||
              "Данные ещё не заполнены"}
          </p>
        </div>
      </div>

      {/* Workflow Stages Timeline */}
      <div className="border-b border-neutral-800/80 px-5 py-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {(readiness?.stages || []).map((stage, index) => {
            const isStageCompleted = stage.state === "completed";
            const isStageActive = stage.state === "active" || stage.state === "running";
            const isStageBlocked = stage.state === "blocked";
            return (
              <div
                key={stage.key}
                className={`rounded-lg border px-3 py-2.5 transition-colors ${
                  isStageCompleted
                    ? "border-emerald-900/60 bg-emerald-950/15"
                    : isStageActive
                    ? "border-violet-700/50 bg-violet-950/20"
                    : isStageBlocked
                    ? "border-amber-800/60 bg-amber-950/20"
                    : "border-neutral-800/80 bg-[#101217]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    {isStageCompleted ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : isStageActive ? (
                      <Loader2 className="h-3 w-3 animate-spin text-violet-300" />
                    ) : isStageBlocked ? (
                      <AlertCircle className="h-3 w-3 text-amber-400" />
                    ) : (
                      <Circle className="h-2.5 w-2.5 text-neutral-600" />
                    )}
                    <span className="font-mono text-[9px] text-neutral-500">0{index + 1}</span>
                  </div>
                </div>
                <p className="mt-1.5 text-xs font-medium text-neutral-200">
                  {STAGE_LABELS[stage.key] || stage.key}
                </p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Suggested Corrections Banner (Applicant clarifications detected) */}
      {data.suggested_corrections && data.suggested_corrections.length > 0 && (
        <div className="m-5 mb-0">
          <SuggestedCorrectionsBanner
            ticketId={ticketId}
            automation={data}
            corrections={data.suggested_corrections}
            onApplied={refetch}
          />
        </div>
      )}

      {/* HITL Human Action Slots (e.g. OU Selection) */}
      {pendingHumanActions.map((action) => {
        const currentCandidateId =
          selectedCandidates[action.id] || action.candidates[0]?.candidate_id;
        return (
          <div
            key={action.id}
            className="m-5 rounded-lg border border-amber-600/30 bg-amber-950/20 p-4 shadow-sm"
          >
            <div className="flex items-start gap-3">
              <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-amber-300">
                <FolderTree className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <h4 className="text-sm font-semibold text-amber-100">
                    {action.target_param.includes("ou")
                      ? "Выбор подразделения (OU)"
                      : "Требуется решение оператора"}
                  </h4>
                  <Badge variant="warning">HITL Action</Badge>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-amber-200/80">{action.prompt}</p>

                <div className="mt-3 space-y-2">
                  {action.candidates.map((candidate) => {
                    const isSelected = currentCandidateId === candidate.candidate_id;
                    return (
                      <label
                        key={candidate.candidate_id}
                        className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                          isSelected
                            ? "border-violet-500 bg-violet-950/30 text-white"
                            : "border-neutral-800 bg-[#101217] text-neutral-300 hover:border-neutral-700"
                        }`}
                      >
                        <input
                          type="radio"
                          name={`hitl-${action.id}`}
                          value={candidate.candidate_id}
                          checked={isSelected}
                          onChange={() =>
                            setSelectedCandidates((prev) => ({
                              ...prev,
                              [action.id]: candidate.candidate_id,
                            }))
                          }
                          className="mt-0.5 text-violet-500 focus:ring-violet-500"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-semibold">{candidate.label}</span>
                            <span className="font-mono text-[10px] text-emerald-400">
                              {Math.round(candidate.score * 100)}% совпадение
                            </span>
                          </div>
                          {candidate.evidence.length > 0 && (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {candidate.evidence.map((ev, evIdx) => (
                                <span
                                  key={evIdx}
                                  className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[9px] text-neutral-400"
                                >
                                  {ev}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>

                <div className="mt-3 flex justify-end">
                  <Button
                    size="sm"
                    loading={busy === `hitl-${action.id}`}
                    onClick={() => resolveHumanAction(action)}
                    icon={<Check className="h-3.5 w-3.5" />}
                  >
                    Подтвердить выбор
                  </Button>
                </div>
              </div>
            </div>
          </div>
        );
      })}

      {/* Main Blocker Notice (if any) */}
      {mainBlocker && pendingHumanActions.length === 0 && (
        <div className="m-5 rounded-lg border border-amber-800/60 bg-amber-950/15 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-amber-100">
                {BLOCKER_LABELS[mainBlocker.code] || mainBlocker.detail || mainBlocker.code}
              </p>
              {mainBlocker.fact_keys.length > 0 && (
                <p className="mt-1 text-xs text-amber-200/70">
                  Не хватает:{" "}
                  {mainBlocker.fact_keys.map((k) => FACT_LABELS[k] || k).join(", ")}.
                </p>
              )}
              {readiness?.clarification?.state === "failed" && (
                <p className="mt-1 text-xs text-amber-200/70">
                  Вопрос заявителю не опубликован · попыток: {readiness.clarification.publish_attempts}.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Expected Outcome Box */}
      <div className="mx-5 mb-4 rounded-lg border border-neutral-800 bg-[#101217] p-4">
        <div className="flex items-center gap-2 text-xs font-semibold text-neutral-200">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
          Ожидаемый регламент выполнения
        </div>
        {data.execution_summary && data.execution_summary.length > 0 ? (
          <div className="mt-2.5 space-y-1.5">
            {data.execution_summary.map((step, idx) => (
              <div key={idx} className="flex items-start gap-2 text-xs text-neutral-300">
                <span className="font-mono text-[10px] text-neutral-500">{idx + 1}.</span>
                <span className="leading-relaxed">{step}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-xs leading-relaxed text-neutral-400">
            После одобрения будет выполнена автоматизированная операция согласно плану выполнения.
          </p>
        )}
        {Boolean(previewLogin) && (
          <p className="mt-2.5 border-t border-neutral-800/80 pt-2 text-xs text-neutral-400">
            Рассчитанный логин:{" "}
            <span className="font-mono font-medium text-neutral-200">{String(previewLogin)}</span>
          </p>
        )}
      </div>

      {/* Preflight Checks Section */}
      <div className="mx-5 mb-4 rounded-lg border border-neutral-800 bg-[#101217] p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-neutral-200">
            <Sparkles className="h-3.5 w-3.5 text-violet-400" />
            Предварительные проверки (Preflight)
          </div>
          <Badge
            variant={
              readiness?.preflight.state === "passed"
                ? "success"
                : readiness?.preflight.state === "failed"
                ? "danger"
                : "neutral"
            }
            dot
          >
            {readiness?.preflight.state === "passed"
              ? "Все проверки пройдены"
              : readiness?.preflight.state === "failed"
              ? "Требуется внимание"
              : "Не запущены"}
          </Badge>
        </div>
        {readiness?.preflight.reason_code === "blocked_by_missing_facts" ? (
          <p className="mt-2 text-xs text-neutral-500">
            Сначала укажите недостающие параметры операции. После этого проверки выполнятся автоматически.
          </p>
        ) : data.preflight.length > 0 ? (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {data.preflight.flatMap((item) =>
              item.checks.map((check: unknown) => {
                const checkObj = typeof check === "object" && check !== null ? check as Record<string, unknown> : null;
                const checkKey = String(checkObj?.key || check);
                const checkStatus = String(checkObj?.status || item.status);
                const isPassed = checkStatus === "passed" || checkStatus === "not_applicable";
                const isActionRequired = checkStatus === "action_required";
                return (
                  <div
                    key={`${item.action_id}-${checkKey}`}
                    className="flex items-center gap-2 rounded border border-neutral-800/80 bg-[#0b0d11] px-2.5 py-1.5 text-xs text-neutral-300"
                  >
                    {isPassed ? (
                      <Check className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
                    ) : isActionRequired ? (
                      <AlertCircle className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                    ) : (
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-400" />
                    )}
                    <span className="truncate">{CHECK_LABELS[checkKey] || checkKey}</span>
                  </div>
                );
              })
            )}
          </div>
        ) : (
          <p className="mt-2 text-xs text-neutral-500">Проверки ещё не сформированы.</p>
        )}
      </div>

      {/* Facts Inline Editor */}
      {factsOpen && (() => {
        const workflowKey = data.workflow_plan.workflow_key;
        const defaultKeysByWorkflow: Record<string, string[]> = {
          employee_onboarding_workflow: ["last_name", "first_name", "middle_name", "department", "title", "phone", "company"],
          printer_connection_workflow: ["pc_name", "printer_address", "printer_model", "connection_type"],
          shared_folder_access_workflow: ["pc_name", "folder_path", "access_level", "target_user"],
        };

        const candidateKeys = Array.from(
          new Set([
            ...(readiness?.blockers.flatMap((b) => b.fact_keys) || []),
            ...(data.workflow_plan.missing_facts || []),
            ...Object.keys(data.case_frame.entities || {}),
            ...Object.keys(data.workflow_plan.fact_provenance || {}),
          ])
        ).filter(Boolean);

        const fieldsToShow = candidateKeys.length > 0
          ? Array.from(new Set([...candidateKeys, ...(defaultKeysByWorkflow[workflowKey] || [])]))
          : defaultKeysByWorkflow[workflowKey] || Object.keys(FACT_LABELS);

        const requiredFactKeys = new Set([
          ...(readiness?.blockers.flatMap((b) => b.fact_keys) || []),
          ...(data.workflow_plan.missing_facts || []),
          ...(workflowKey === "employee_onboarding_workflow" ? ["last_name", "first_name", "department", "title"] : []),
          ...(workflowKey === "printer_connection_workflow" ? ["pc_name", "printer_address"] : []),
        ]);

        return (
          <div className="border-y border-neutral-800 bg-[#101217] px-5 py-4">
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
                Корректировка параметров
              </h4>
              <span className="text-[11px] text-neutral-500">* обязательные поля</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {fieldsToShow.map((key) => {
                const label = FACT_LABELS[key] || key;
                const isReq = requiredFactKeys.has(key);
                return (
                  <label key={key} className="block text-xs font-medium text-neutral-400">
                    {label} {isReq && <span className="text-rose-400">*</span>}
                    <input
                      value={factDraft[key] || ""}
                      onChange={(e) =>
                        setFactDraft((curr) => ({ ...curr, [key]: e.target.value }))
                      }
                      className="mt-1.5 w-full rounded border border-neutral-700 bg-[#0b0d11] px-3 py-2 text-xs text-neutral-100 outline-none transition focus:border-violet-500"
                    />
                  </label>
                );
              })}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setFactsOpen(false)}>
                Отмена
              </Button>
              <Button size="sm" loading={busy === "facts"} onClick={saveFacts}>
                Сохранить параметры
              </Button>
            </div>
          </div>
        );
      })()}

      {/* Action Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-t border-neutral-800 bg-[#0b0d11] px-5 py-3.5">
        {(actions.has("edit_facts") || !data.action_plan) && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setFactsOpen(true)}
            icon={<Pencil className="h-3.5 w-3.5" />}
          >
            {Object.keys(data.case_frame.entities || {}).length > 0 ? "Изменить параметры" : "Заполнить параметры"}
          </Button>
        )}
        {actions.has("retry_clarification") && (
          <Button
            size="sm"
            loading={busy === "retry_clarification"}
            onClick={retryClarification}
            icon={<RefreshCw className="h-3.5 w-3.5" />}
          >
            Повторить запрос
          </Button>
        )}
        {actions.has("reanalyze") && (
          <Button
            size="sm"
            variant="secondary"
            loading={busy === "reanalyze"}
            onClick={() =>
              run("reanalyze", () => autopilotApi.reanalyze(ticketId), "Анализ заявки обновлён")
            }
            icon={<RefreshCw className="h-3.5 w-3.5" />}
          >
            Повторить анализ
          </Button>
        )}
        {actions.has("approve") && (
          <Button
            size="sm"
            loading={busy === "approve"}
            onClick={approve}
            icon={<Check className="h-3.5 w-3.5" />}
          >
            {data.workflow_plan.workflow_key === "employee_onboarding_workflow"
              ? "Одобрить создание в AD"
              : "Одобрить выполнение"}
          </Button>
        )}
        {actions.has("reject_automation") && (
          <Button
            size="sm"
            variant="ghost"
            loading={busy === "reject"}
            onClick={reject}
          >
            Отклонить автоматизацию
          </Button>
        )}

        <button
          type="button"
          onClick={() => setCorrectionsOpen((v) => !v)}
          className="ml-auto text-xs text-neutral-500 hover:text-neutral-300"
        >
          {correctionsOpen ? "Скрыть ручную переклассификацию" : "Переклассифицировать"}
        </button>
      </div>

      {/* Manual Corrections Drawer */}
      {correctionsOpen && (
        <div className="grid gap-3 border-t border-neutral-800 bg-[#080a0d] px-5 py-4 sm:grid-cols-2">
          <label className="text-xs text-neutral-400">
            Тип обращения
            <input
              value={caseType}
              onChange={(e) => setCaseType(e.target.value)}
              className="mt-1.5 w-full rounded border border-neutral-800 bg-[#101217] px-3 py-2 text-xs text-neutral-200"
            />
            <Button
              size="sm"
              variant="ghost"
              className="mt-2"
              loading={busy === "case"}
              onClick={() =>
                run("case", () => autopilotApi.correctCase(ticketId, data, caseType), "Тип обращения исправлен")
              }
            >
              Сохранить тип
            </Button>
          </label>
          <label className="text-xs text-neutral-400">
            ID целевого сервиса
            <input
              type="number"
              value={serviceId}
              onChange={(e) => setServiceId(e.target.value)}
              className="mt-1.5 w-full rounded border border-neutral-800 bg-[#101217] px-3 py-2 text-xs text-neutral-200"
            />
            <Button
              size="sm"
              variant="ghost"
              className="mt-2"
              loading={busy === "service"}
              onClick={() =>
                run(
                  "service",
                  () => autopilotApi.correctTargetService(ticketId, data, Number(serviceId)),
                  "Целевой сервис исправлен"
                )
              }
            >
              Сохранить сервис
            </Button>
          </label>
        </div>
      )}

      {/* Technical Diagnostics Accordion */}
      <button
        type="button"
        onClick={() => setTechnicalOpen((v) => !v)}
        className="flex w-full items-center justify-between border-t border-neutral-800 bg-[#080a0d] px-5 py-2.5 text-left text-xs text-neutral-500 hover:text-neutral-300"
      >
        <span>Техническая диагностика плана</span>
        {technicalOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>

      {technicalOpen && (
        <div className="border-t border-neutral-800 bg-[#050608] p-5 font-mono text-[10px] leading-relaxed text-neutral-400">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <p>snapshot: {data.snapshot_hash}</p>
              <p>
                binding: {data.service_compatibility.binding_key || "—"}@
                {data.service_compatibility.binding_version || "—"}
              </p>
              <p>catalog: {data.service_compatibility.catalog_hash || "—"}</p>
              <p>
                workflow: {data.workflow_plan.workflow_key}@{data.workflow_plan.workflow_version}
              </p>
            </div>
            <div>
              <p>case: {data.case_decision.primary_case_type || "unknown"}</p>
              <p>authorization: {data.service_compatibility.authorization_state}</p>
              <p>reason codes: {data.workflow_plan.reason_codes.join(", ") || "—"}</p>
              <p>timings: {JSON.stringify(data.service_compatibility.analysis_timings_ms)}</p>
            </div>
          </div>
          <details className="mt-3">
            <summary className="cursor-pointer text-neutral-300 hover:text-white">
              Безопасный JSON плана и проверок (секреты исключены)
            </summary>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded border border-neutral-900 bg-black/40 p-3 text-[10px]">
              {JSON.stringify(
                sanitizeTechnicalJson({
                  action_plan: data.action_plan,
                  preflight: data.preflight,
                  human_actions: data.human_actions,
                  clarification: readiness?.clarification,
                }),
                null,
                2
              )}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
};
