import React from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Circle,
  Loader2,
  Pencil,
  RefreshCw,
  ShieldCheck,
  UserRoundPlus,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { autopilotApi } from "@/features/autopilot/api";
import type { TicketAutomation } from "@/features/autopilot/types";
import { Badge, Button, useToast } from "@/shared/ui";

const FACT_LABELS: Record<string, string> = {
  last_name: "Фамилия",
  first_name: "Имя",
  middle_name: "Отчество",
  department: "Подразделение",
  title: "Должность",
  phone: "Телефон",
  company: "Организация",
};

const STAGE_LABELS: Record<string, string> = {
  facts: "Данные сотрудника",
  ou: "Выбор OU",
  preflight: "Проверки",
  approval: "Одобрение",
  execution: "Создание AD",
  delivery: "Доставка реквизитов",
};

const BLOCKER_LABELS: Record<string, string> = {
  required_facts_missing: "Не получены обязательные данные нового сотрудника",
  clarification_publish_failed: "Вопрос заявителю не опубликован",
  preflight_failed: "Предварительные проверки не пройдены",
  ad_onboarding_execution_disabled: "Создание пользователей AD пока отключено администратором",
};

const CHECK_LABELS: Record<string, string> = {
  required_identity_fields: "Обязательные данные сотрудника",
  ad_available: "Подключение к Active Directory",
  target_ou: "Целевой OU",
  target_ou_exists: "Целевой OU существует",
  dn_absent: "Объект с таким DN отсутствует",
  distinguished_name_absent: "Объект с таким DN отсутствует",
  login_preview: "Предварительный логин рассчитан",
  login_preview_available: "Предварительный логин рассчитан",
  intraservice_available: "IntraService доступен",
  credential_fields_1488_1489_configured: "Защищённые поля 1488/1489 настроены",
};

interface Props {
  ticketId: number;
  applicantName?: string | null;
  data: TicketAutomation;
  refetch: () => Promise<unknown>;
  onExecutionStarted?: (commandId: string) => void;
}

function factValue(data: TicketAutomation, key: string): string {
  const provenance = data.workflow_plan.fact_provenance?.[key] || [];
  const latest = provenance[provenance.length - 1];
  return String(latest?.value || data.case_frame.entities[key] || "");
}

function employeeName(data: TicketAutomation): string {
  return ["last_name", "first_name", "middle_name"]
    .map((key) => factValue(data, key))
    .filter(Boolean)
    .join(" ") || "Не указан";
}

export const OnboardingWorkflowPanel: React.FC<Props> = ({
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
  const [caseType, setCaseType] = React.useState(data.case_decision.primary_case_type || "employee_onboarding");
  const [serviceId, setServiceId] = React.useState(String(data.service_compatibility.target_service_id || ""));
  const [factDraft, setFactDraft] = React.useState<Record<string, string>>(
    Object.fromEntries(Object.keys(FACT_LABELS).map((key) => [key, factValue(data, key)]))
  );
  const toast = useToast();
  const queryClient = useQueryClient();
  const readiness = data.readiness;
  const actions = new Set(readiness?.actions || []);

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

  const saveFacts = () => run(
    "facts",
    () => autopilotApi.correctOnboardingFacts(
      ticketId,
      data,
      Object.fromEntries(Object.entries(factDraft).filter(([, value]) => value.trim()))
    ),
    "Данные сотрудника сохранены, проверки запущены"
  ).then(() => setFactsOpen(false));

  const approve = () => run("approve", async () => {
    const result = await autopilotApi.approve(ticketId, data);
    if (result.command_id) onExecutionStarted?.(result.command_id);
  }, "Создание учётной записи одобрено");

  const reject = () => run(
    "reject",
    () => autopilotApi.reject(ticketId, data, "Оператор отклонил автоматическое выполнение"),
    "Автоматизация отклонена; статус заявки не изменён"
  );

  const retry = () => {
    const clarificationId = readiness?.clarification?.id;
    if (!clarificationId) return Promise.resolve();
    return run(
      "retry",
      () => autopilotApi.retryClarification(ticketId, data, clarificationId),
      "Вопрос заявителю опубликован"
    );
  };

  const mainBlocker = readiness?.blockers[0];
  const targetOu = data.action_plan?.actions[0]?.params?.target_ou_dn;
  const previewLogin = data.preflight[0]?.details?.preview_sam_account_name
    || data.preflight[0]?.details?.sam_account_name
    || data.preflight[0]?.details?.login;

  return (
    <section className="overflow-hidden rounded-xl border border-neutral-800 bg-[#0d0f13]">
      <header className="flex items-start justify-between gap-4 border-b border-neutral-800 px-5 py-4">
        <div className="flex min-w-0 gap-3">
          <div className="mt-0.5 rounded-lg border border-violet-500/20 bg-violet-500/10 p-2 text-violet-300">
            <UserRoundPlus className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-neutral-100">Создание учётной записи AD</h3>
            <p className="mt-1 text-xs text-neutral-500">Отдельный workflow · только create_ad_user · ручное одобрение обязательно</p>
          </div>
        </div>
        <Badge variant={readiness?.state === "completed" ? "success" : readiness?.state === "ready_for_approval" ? "warning" : "neutral"} dot>
          {readiness?.state === "ready_for_approval" ? "Готово к одобрению" : readiness?.state === "completed" ? "Завершено" : "Требует внимания"}
        </Badge>
      </header>

      <div className="grid gap-px bg-neutral-800 md:grid-cols-2">
        <div className="bg-[#101217] px-5 py-4">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-neutral-500">Заявитель</p>
          <p className="mt-2 text-sm font-medium text-neutral-100">{applicantName || "Не определён"}</p>
          <p className="mt-1 text-xs text-neutral-500">Автор заявки; не используется как новый сотрудник</p>
        </div>
        <div className="bg-[#101217] px-5 py-4">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-neutral-500">Новый сотрудник</p>
          <p className="mt-2 text-sm font-medium text-neutral-100">{employeeName(data)}</p>
          <p className="mt-1 text-xs text-neutral-500">
            {[factValue(data, "department"), factValue(data, "title")].filter(Boolean).join(" · ") || "Данные ещё не заполнены"}
          </p>
        </div>
      </div>

      <div className="px-5 py-4">
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-6">
          {(readiness?.stages || []).map((stage, index) => (
            <div key={stage.key} className={`rounded-lg border px-3 py-2.5 ${stage.state === "completed" ? "border-emerald-900/60 bg-emerald-950/15" : stage.state === "active" || stage.state === "running" ? "border-violet-700/50 bg-violet-950/20" : stage.state === "blocked" ? "border-amber-800/50 bg-amber-950/15" : "border-neutral-800 bg-[#101217]"}`}>
              <div className="flex items-center gap-1.5">
                {stage.state === "completed" ? <Check className="h-3 w-3 text-emerald-400" /> : stage.state === "running" ? <Loader2 className="h-3 w-3 animate-spin text-violet-300" /> : <Circle className="h-2.5 w-2.5 text-neutral-600" />}
                <span className="font-mono text-[9px] text-neutral-600">0{index + 1}</span>
              </div>
              <p className="mt-2 text-[11px] font-medium text-neutral-300">{STAGE_LABELS[stage.key] || stage.key}</p>
            </div>
          ))}
        </div>
      </div>

      {mainBlocker && (
        <div className="mx-5 mb-4 rounded-lg border border-amber-800/50 bg-amber-950/15 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-amber-100">{BLOCKER_LABELS[mainBlocker.code] || mainBlocker.detail || mainBlocker.code}</p>
              {mainBlocker.fact_keys.length > 0 && (
                <p className="mt-1 text-xs text-amber-200/60">Не хватает: {mainBlocker.fact_keys.map((key) => FACT_LABELS[key] || key).join(", ")}.</p>
              )}
              {readiness?.clarification?.state === "failed" && (
                <p className="mt-1 text-xs text-amber-200/60">Вопрос заявителю не опубликован · попыток: {readiness.clarification.publish_attempts}.</p>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="mx-5 mb-4 rounded-lg border border-neutral-800 bg-[#101217] p-4">
        <div className="flex items-center gap-2 text-xs font-medium text-neutral-200"><ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />Ожидаемый результат</div>
        <p className="mt-2 text-xs leading-5 text-neutral-400">
          После одобрения будет создан один пользователь в AD{targetOu ? <> в <span className="font-mono text-neutral-300">{String(targetOu)}</span></> : " в выбранном OU"}. Логин и одноразовый пароль будут записаны в защищённые поля 1488/1489; пароль не попадёт в комментарии или журнал.
        </p>
        {Boolean(previewLogin) && <p className="mt-2 text-xs text-neutral-500">Предварительный логин: <span className="font-mono text-neutral-200">{String(previewLogin)}</span></p>}
      </div>

      <div className="mx-5 mb-4 rounded-lg border border-neutral-800 bg-[#101217] p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-medium text-neutral-200">Предварительные проверки</p>
          <Badge variant={readiness?.preflight.state === "passed" ? "success" : readiness?.preflight.state === "failed" ? "danger" : "neutral"} dot>
            {readiness?.preflight.state === "passed" ? "Пройдены" : readiness?.preflight.state === "failed" ? "Есть ошибки" : "Не запущены"}
          </Badge>
        </div>
        {readiness?.preflight.reason_code === "blocked_by_missing_facts" ? (
          <p className="mt-2 text-xs text-neutral-500">Сначала заполните обязательные данные нового сотрудника. После этого план и preflight будут созданы автоматически.</p>
        ) : data.preflight.length > 0 ? (
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {data.preflight.flatMap((item) => item.checks.map((check) => (
              <div key={`${item.action_id}-${check}`} className="flex items-center gap-2 text-xs text-neutral-400">
                {item.status === "passed" || item.status === "not_applicable" ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <AlertTriangle className="h-3.5 w-3.5 text-rose-400" />}
                {CHECK_LABELS[check] || check}
              </div>
            )))}
          </div>
        ) : <p className="mt-2 text-xs text-neutral-500">Результат ещё не сформирован.</p>}
      </div>

      {factsOpen && (
        <div className="border-y border-neutral-800 bg-[#101217] px-5 py-4">
          <div className="grid gap-3 md:grid-cols-2">
            {Object.entries(FACT_LABELS).map(([key, label]) => (
              <label key={key} className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
                {label}{["last_name", "first_name", "department", "title"].includes(key) ? " *" : ""}
                <input value={factDraft[key] || ""} onChange={(event) => setFactDraft((current) => ({ ...current, [key]: event.target.value }))} className="mt-1.5 w-full rounded border border-neutral-700 bg-[#0b0d11] px-3 py-2 text-xs font-normal normal-case tracking-normal text-neutral-100 outline-none focus:border-violet-500" />
              </label>
            ))}
          </div>
          <div className="mt-4 flex justify-end gap-2"><Button size="sm" variant="ghost" onClick={() => setFactsOpen(false)}>Отмена</Button><Button size="sm" loading={busy === "facts"} onClick={saveFacts}>Сохранить данные</Button></div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-neutral-800 px-5 py-3.5">
        {(actions.has("edit_facts") || !data.action_plan) && <Button size="sm" variant="secondary" onClick={() => setFactsOpen(true)} icon={<Pencil className="h-3.5 w-3.5" />}>Заполнить данные</Button>}
        {actions.has("retry_clarification") && <Button size="sm" loading={busy === "retry"} onClick={retry} icon={<RefreshCw className="h-3.5 w-3.5" />}>Повторить запрос</Button>}
        {actions.has("reanalyze") && <Button size="sm" loading={busy === "reanalyze"} onClick={() => run("reanalyze", () => autopilotApi.reanalyze(ticketId), "Проверки повторены")} icon={<RefreshCw className="h-3.5 w-3.5" />}>Повторить проверки</Button>}
        {actions.has("approve") && <Button size="sm" loading={busy === "approve"} onClick={approve} icon={<Check className="h-3.5 w-3.5" />}>Одобрить создание</Button>}
        {actions.has("reject_automation") && <Button size="sm" variant="ghost" loading={busy === "reject"} onClick={reject}>Отклонить автоматизацию</Button>}
        <button type="button" onClick={() => setCorrectionsOpen((value) => !value)} className="ml-auto text-[11px] text-neutral-500 hover:text-neutral-300">Исправить тип или сервис</button>
      </div>

      {correctionsOpen && (
        <div className="grid gap-3 border-t border-neutral-800 bg-[#0b0d11] px-5 py-4 md:grid-cols-2">
          <label className="text-[10px] uppercase tracking-wider text-neutral-500">Тип обращения<input value={caseType} onChange={(event) => setCaseType(event.target.value)} className="mt-1.5 w-full rounded border border-neutral-800 bg-[#101217] px-3 py-2 text-xs normal-case text-neutral-200" /><Button size="sm" variant="ghost" loading={busy === "case"} onClick={() => run("case", () => autopilotApi.correctCase(ticketId, data, caseType), "Тип обращения исправлен")}>Сохранить тип</Button></label>
          <label className="text-[10px] uppercase tracking-wider text-neutral-500">ID целевого сервиса<input type="number" value={serviceId} onChange={(event) => setServiceId(event.target.value)} className="mt-1.5 w-full rounded border border-neutral-800 bg-[#101217] px-3 py-2 text-xs normal-case text-neutral-200" /><Button size="sm" variant="ghost" loading={busy === "service"} onClick={() => run("service", () => autopilotApi.correctTargetService(ticketId, data, Number(serviceId)), "Целевой сервис исправлен")}>Сохранить сервис</Button></label>
        </div>
      )}

      <button type="button" onClick={() => setTechnicalOpen((value) => !value)} className="flex w-full items-center justify-between border-t border-neutral-800 bg-[#0b0d11] px-5 py-3 text-left text-xs text-neutral-500 hover:text-neutral-300"><span>Техническая диагностика</span>{technicalOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}</button>
      {technicalOpen && (
        <div className="border-t border-neutral-800 bg-[#080a0d] p-5 font-mono text-[10px] leading-5 text-neutral-500">
          <div className="grid gap-3 md:grid-cols-2"><div>snapshot: {data.snapshot_hash}<br />binding: {data.service_compatibility.binding_key || "—"}@{data.service_compatibility.binding_version || "—"}<br />catalog: {data.service_compatibility.catalog_hash || "—"}<br />workflow: {data.workflow_plan.workflow_key}@{data.workflow_plan.workflow_version}</div><div>case: {data.case_decision.primary_case_type || "unknown"}<br />authorization: {data.service_compatibility.authorization_state}<br />reason codes: {data.workflow_plan.reason_codes.join(", ") || "—"}<br />timings: {JSON.stringify(data.service_compatibility.analysis_timings_ms)}</div></div>
          <details className="mt-3"><summary className="cursor-pointer text-neutral-400">Сырой preflight и идентификаторы</summary><pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded border border-neutral-900 bg-black/20 p-3">{JSON.stringify({ action_plan_id: data.action_plan?.id, clarification_id: readiness?.clarification?.id, preflight: data.preflight, candidates: data.service_compatibility.target_candidates }, null, 2)}</pre></details>
        </div>
      )}
    </section>
  );
};
