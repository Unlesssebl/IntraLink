import React from "react";
import { AlertTriangle, ArrowRight, Check, GitBranch, LockKeyhole, UserPlus } from "lucide-react";

import type { TicketAutomation } from "@/features/autopilot/types";
import { Badge } from "@/shared/ui";
import {
  detailText,
  FACT_NAMES,
  latestFact,
  PREFLIGHT_ERROR_NAMES,
  type PreflightItem,
} from "./presentation";

export function DecisionOutcome({
  data,
  currentPreflight,
  preflightPassed,
  canApprove,
}: {
  data: TicketAutomation;
  currentPreflight: PreflightItem[];
  preflightPassed: boolean;
  canApprove: boolean;
}) {
  const workflow = data.workflow_plan;
  const actionPlan = data.action_plan;
  const onboardingAction = actionPlan?.actions.find((action) => action.capability_key === "create_ad_user");
  const onboardingPreflight = onboardingAction
    ? currentPreflight.find((item) => item.action_id === onboardingAction.id)
    : undefined;
  const details = onboardingPreflight?.details;
  const targetOu = detailText(details, "target_ou");
  const previewLogin = detailText(details, "preview_sam_account_name");
  const previewUpn = detailText(details, "preview_upn");
  const fullName = ["last_name", "first_name", "middle_name"]
    .map((key) => String(onboardingAction?.params[key] || latestFact(data, key) || "").trim())
    .filter(Boolean)
    .join(" ");
  const commandRunning = data.execution.some((item) => ["pending", "claimed", "running"].includes(item.status));
  const commandSucceeded = data.execution.some((item) => item.status === "succeeded" || item.outcome === "succeeded");
  const executionFinished = commandSucceeded || actionPlan?.state === "succeeded" || workflow.state === "completed";
  const blockers: string[] = [];

  if (workflow.missing_facts.length) {
    blockers.push(`Нужен ответ заявителя: ${workflow.missing_facts.map((key) => FACT_NAMES[key] || key).join(", ")}`);
  }
  if (Object.keys(workflow.fact_conflicts || {}).length) {
    blockers.push("Есть противоречивые данные сотрудника — требуется решение оператора");
  }
  if (!executionFinished && data.service_compatibility.authorization_state !== "authorized") {
    blockers.push("Активная привязка сервиса не разрешает выполнение");
  }
  if (actionPlan?.state === "ready" && !preflightPassed) {
    const error = onboardingPreflight?.error;
    blockers.push(error ? PREFLIGHT_ERROR_NAMES[error] || error : "Проверка перед выполнением не пройдена или истекла");
  }
  const isPolicyBlocked = Boolean(data.execution_policy && !data.execution_policy.allowed);
  if (!executionFinished && !commandRunning && isPolicyBlocked) {
    if (data.execution_policy?.reasons?.length) {
      const reasonLabels: Record<string, string> = {
        missing_executor: "Не зарегистрирован исполнитель",
        missing_service_binding: "Нет активной привязки сервиса",
        unsupported_capability_in_binding: "Действие не разрешено привязкой сервиса",
        invalid_service_binding: "Привязка сервиса не прошла валидацию",
        policy_not_found: "Политика выполнения не найдена",
        policy_disabled: "Выполнение отключено администратором",
        deployment_force_disabled: "Мутирующие операции принудительно отключены на сервере",
        circuit_breaker_open: "Предохранитель (circuit breaker) открыт",
        no_compatible_worker: "Нет совместимого активного воркера",
        worker_unavailable: "Отсутствует активный воркер для выполнения операции",
        worker_incompatible: "Несовпадение версии воркера",
        stale_worker_heartbeat: "Связь с воркером потеряна (heartbeat timeout)",
        worker_engine_version_mismatch: "Несовпадение версии движка на воркере",
        worker_deployment_force_disabled: "Воркер работает в режиме запрета мутаций",
        preflight_not_passed: "Предварительные проверки не пройдены",
        preflight_expired: "Срок действия предварительных проверок истек",
        snapshot_hash_mismatch: "Данные тикета изменились",
        params_hash_mismatch: "Параметры действия изменились",
      };
      data.execution_policy.reasons.forEach((code) => {
        blockers.push(reasonLabels[code] || `Политика: ${code}`);
      });
    } else {
      blockers.push("Выполнение действия заблокировано политикой безопасности");
    }
  }

  let title = "Решение требует проверки оператора";
  let description = "Автоматических изменений не будет, пока условия выполнения не станут однозначными.";
  let badge = "Нужна проверка";
  let tone = "warning" as "success" | "warning" | "danger" | "info";

  if (workflow.state === "awaiting_facts") {
    title = "Ожидается ответ заявителя";
    description = "Движок запросил недостающие данные. После нового публичного ответа заявка будет проанализирована автоматически.";
    badge = "Ожидание данных";
  } else if (workflow.state === "needs_review" || actionPlan?.state === "needs_review" || actionPlan?.state === "failed") {
    title = "Автоматическое выполнение остановлено";
    description = "Заявка останется у оператора; повторное создание пользователя не запускается автоматически.";
    badge = "Нужна проверка";
    tone = "danger";
  } else if (executionFinished) {
    title = "Учётная запись создана и проверена";
    description = "AD подтвердил объект, реквизиты записаны в защищённые поля заявки, завершение можно считать успешным.";
    badge = "Выполнено";
    tone = "success";
  } else if (commandRunning || ["approved", "running"].includes(actionPlan?.state || "")) {
    title = "Создание учётной записи выполняется";
    description = "Повторное подтверждение не требуется. Движок проверит AD, доставку реквизитов и только затем завершит заявку.";
    badge = "Выполняется";
    tone = "info";
  } else if (canApprove && onboardingAction) {
    title = "План готов к одобрению";
    description = "Все обязательные данные и технические проверки подтверждены. До нажатия кнопки AD не изменяется.";
    badge = "Можно одобрить";
    tone = "success";
  } else if (onboardingAction && isPolicyBlocked && preflightPassed) {
    title = "План корректен, но выполнение заблокировано политикой";
    description = "Выбранный OU и логин проверены, однако запуск заблокирован политикой безопасности или отсутствием воркера.";
    badge = "Заблокировано политикой";
    tone = "warning";
  } else if (data.redirect_plan) {
    title = "Предложено перенаправление заявки";
    description = `После подтверждения заявка будет направлена в «${data.redirect_plan.target_service_path}».`;
    badge = "Ожидает одобрения";
    tone = "info";
  }

  const toneClasses = {
    success: "border-emerald-800/50 bg-emerald-950/15",
    warning: "border-amber-800/50 bg-amber-950/15",
    danger: "border-rose-800/50 bg-rose-950/15",
    info: "border-blue-800/50 bg-blue-950/15",
  }[tone];

  return (
    <section className={`m-4 overflow-hidden rounded-lg border ${toneClasses}`} aria-labelledby="decision-outcome-title">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.06] px-4 py-3.5">
        <div className="flex min-w-0 gap-3">
          <div className="mt-0.5 rounded-md border border-white/10 bg-black/20 p-2">
            {tone === "success"
              ? <Check className="h-4 w-4 text-emerald-400" strokeWidth={1.5} />
              : tone === "danger"
                ? <AlertTriangle className="h-4 w-4 text-rose-400" strokeWidth={1.5} />
                : <GitBranch className="h-4 w-4 text-neutral-300" strokeWidth={1.5} />}
          </div>
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-neutral-500">Итог решения</p>
            <h4 id="decision-outcome-title" className="mt-1 text-sm font-semibold text-neutral-100">{title}</h4>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-neutral-400">{description}</p>
          </div>
        </div>
        <Badge variant={tone} dot>{badge}</Badge>
      </div>

      {onboardingAction ? (
        <div className="grid gap-px bg-white/[0.06] lg:grid-cols-[1.05fr_1fr_1.15fr]">
          <OutcomeColumn
            label="Сейчас"
            icon={<LockKeyhole />}
            title={canApprove ? "Ожидается решение оператора" : title}
            lines={[
              "До одобрения объект AD не создаётся",
              fullName ? `Сотрудник: ${fullName}` : "ФИО ещё не подтверждено",
              targetOu ? `Контейнер: ${targetOu}` : "Контейнер OU ещё не подтверждён",
            ]}
          />
          <OutcomeColumn
            label="После одобрения"
            icon={<ArrowRight />}
            title="Одна контролируемая попытка"
            lines={[
              `Создать пользователя${previewLogin ? ` с логином ${previewLogin}` : " после проверки свободного логина"}`,
              "Установить временный пароль и потребовать его смену",
              "Повторно прочитать объект AD и проверить результат",
            ]}
          />
          <OutcomeColumn
            label="Результат"
            icon={<UserPlus />}
            title={previewUpn || previewLogin || "Подтверждённая учётная запись"}
            lines={[
              "Логин — в поле 1488, пароль — только в защищённом поле 1489",
              "Заявителю опубликовано сообщение о готовности",
              "Заявка завершена только после всех проверок",
            ]}
          />
        </div>
      ) : data.redirect_plan ? (
        <div className="grid gap-px bg-white/[0.06] md:grid-cols-2">
          <OutcomeColumn label="После одобрения" icon={<ArrowRight />} title={data.redirect_plan.target_service_path} lines={[data.redirect_plan.rendered_public_comment]} />
          <OutcomeColumn label="Без одобрения" icon={<LockKeyhole />} title="Маршрут не меняется" lines={["Движок сохраняет предложение как план", "Оператор может выбрать ручную обработку"]} />
        </div>
      ) : null}

      {blockers.length > 0 && (
        <div className="border-t border-white/[0.06] px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">Что мешает выполнить сейчас</p>
          <ul className="mt-2 grid gap-1.5 text-[11px] text-neutral-300 md:grid-cols-2">
            {[...new Set(blockers)].map((blocker) => (
              <li key={blocker} className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" strokeWidth={1.5} />
                {blocker}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function OutcomeColumn({ label, icon, title, lines }: {
  label: string;
  icon: React.ReactElement<{ className?: string; strokeWidth?: number }>;
  title: string;
  lines: string[];
}) {
  return (
    <div className="bg-[#101319] p-4">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
        {React.cloneElement(icon, { className: "h-3.5 w-3.5", strokeWidth: 1.5 })}
        {label}
      </div>
      <p className="mt-2 text-xs font-medium text-neutral-100">{title}</p>
      <ul className="mt-2 space-y-1.5 text-[11px] leading-4 text-neutral-400">
        {lines.map((line) => (
          <li key={line} className="flex items-start gap-2">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-neutral-600" />
            <span className="break-words">{line}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
