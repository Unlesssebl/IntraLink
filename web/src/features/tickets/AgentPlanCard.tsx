import React from "react";
import { AlertTriangle, Route } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { autopilotApi } from "@/features/autopilot/api";
import { Button, useToast } from "@/shared/ui";
import { useAgentPlan } from "./queries";
import { OnboardingWorkflowPanel } from "./OnboardingWorkflowPanel";

export interface AgentPlanCardProps {
  ticketId: number;
  applicantName?: string | null;
  onExecutionStarted?: (commandId: string) => void;
}

export const AgentPlanCard: React.FC<AgentPlanCardProps> = ({ ticketId, applicantName, onExecutionStarted }) => {
  const { data, isLoading, error, refetch } = useAgentPlan(ticketId);
  const [busy, setBusy] = React.useState(false);
  const [analysisError, setAnalysisError] = React.useState<string | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();

  const analyze = async (force: boolean) => {
    if (busy) return;
    setBusy(true);
    setAnalysisError(null);
    try {
      const result = await (force ? autopilotApi.reanalyze(ticketId) : autopilotApi.analyze(ticketId));
      queryClient.setQueryData(["autopilot", "automation", ticketId], result);
      toast.success(force ? "Анализ заявки обновлён" : "Анализ заявки завершён");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Не удалось запустить анализ";
      setAnalysisError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  };

  if (isLoading) return <div className="h-36 animate-pulse rounded-xl border border-neutral-800 bg-[#101217]" />;
  if (error || !data) {
    return (
      <section className="rounded-xl border border-neutral-800 bg-[#101217] p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-neutral-100">{busy ? "Выполняется анализ заявки" : "Анализ ещё не запускался"}</p>
            <p className="mt-1 text-xs text-neutral-500">{busy ? "Получаем заявку, определяем сценарий и проверяем готовность. Обычно это занимает до 15 секунд." : "Нажмите кнопку, чтобы определить сценарий, необходимые данные и доступные действия."}</p>
          </div>
          <Button type="button" loading={busy} disabled={busy} onClick={() => analyze(false)} icon={<Route className="h-3.5 w-3.5" />}>{busy ? "Анализируем…" : "Запустить анализ"}</Button>
        </div>
        {analysisError && <div className="mt-3 flex items-start gap-2 rounded border border-rose-800/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-300"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>Анализ не выполнен: {analysisError}</span></div>}
      </section>
    );
  }

  if (data.workflow_plan.workflow_key === "employee_onboarding_workflow") {
    return <OnboardingWorkflowPanel ticketId={ticketId} applicantName={applicantName} data={data} refetch={refetch} onExecutionStarted={onExecutionStarted} />;
  }

  return (
    <section className="rounded-xl border border-neutral-800 bg-[#101217] p-4">
      <p className="text-sm font-medium text-neutral-100">{data.case_decision.primary_case_type || "Тип обращения не определён"}</p>
      <p className="mt-1 text-xs text-neutral-500">Workflow: {data.workflow_plan.workflow_key}. Для этого сценария используется отдельный интерфейс.</p>
      <Button type="button" className="mt-3" size="sm" variant="secondary" loading={busy} onClick={() => analyze(true)}>Повторить анализ</Button>
    </section>
  );
};
