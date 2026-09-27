import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Boxes, GitBranch, RefreshCw, Route } from "lucide-react";

import { Button, Badge } from "@/shared/ui";
import { autopilotApi } from "./api";

export const AutopilotConsole: React.FC = () => {
  const caseTypes = useQuery({ queryKey: ["automation", "case-types"], queryFn: autopilotApi.getCaseTypes });
  const workflows = useQuery({ queryKey: ["automation", "workflows"], queryFn: autopilotApi.getWorkflows });
  const capabilities = useQuery({ queryKey: ["automation", "capabilities"], queryFn: autopilotApi.getCapabilities });
  const commands = useQuery({
    queryKey: ["automation", "commands"],
    queryFn: () => autopilotApi.getCommands(50),
    refetchInterval: 5000,
  });

  const refresh = () => {
    caseTypes.refetch();
    workflows.refetch();
    capabilities.refetch();
    commands.refetch();
  };

  return (
    <div className="mx-auto max-w-[1440px] space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-violet-400" strokeWidth={1.5} />
            <h2 className="text-base font-semibold text-neutral-100">Движок автоматизации заявок</h2>
          </div>
          <p className="mt-1 text-xs text-neutral-500">
            CaseDecision → WorkflowPlan → ActionPlan → Capability execution
          </p>
        </div>
        <Button size="sm" variant="secondary" onClick={refresh} icon={<RefreshCw className="h-3.5 w-3.5" />}>
          Обновить
        </Button>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <Metric icon={<Route />} label="Типы обращений" value={caseTypes.data?.total ?? 0} />
        <Metric icon={<GitBranch />} label="Workflow" value={workflows.data?.total ?? 0} />
        <Metric icon={<Boxes />} label="Capabilities" value={capabilities.data?.total ?? 0} />
      </div>

      <section className="overflow-hidden rounded-lg border border-neutral-800/80 bg-[#0d0f14]">
        <header className="flex items-center justify-between border-b border-neutral-800/80 px-4 py-3">
          <div>
            <h3 className="text-sm font-medium text-neutral-100">Журнал технических действий</h3>
            <p className="mt-0.5 text-[11px] text-neutral-500">Одна команда соответствует одной capability</p>
          </div>
          <Badge variant="neutral">{commands.data?.length ?? 0}</Badge>
        </header>
        <div className="divide-y divide-neutral-800/70">
          {(commands.data || []).map((command) => (
            <div key={command.id} className="grid grid-cols-[100px_minmax(0,1fr)_140px_110px] items-center gap-3 px-4 py-2.5 text-xs">
              <span className="font-mono text-neutral-500">#{command.task_id ?? "system"}</span>
              <div className="min-w-0">
                <p className="truncate font-medium text-neutral-200">{command.capability_key || "system"}</p>
                <p className="truncate font-mono text-[10px] text-neutral-600">{command.action_id || command.id}</p>
              </div>
              <span className="truncate text-neutral-500">{command.initiator}</span>
              <Badge variant={command.status === "succeeded" ? "success" : command.status === "failed" ? "danger" : "warning"}>
                {command.status}
              </Badge>
            </div>
          ))}
          {!commands.data?.length && <div className="px-4 py-10 text-center text-xs text-neutral-600">Команд пока нет</div>}
        </div>
      </section>
    </div>
  );
};

function Metric({ icon, label, value }: { icon: React.ReactElement<{ className?: string; strokeWidth?: number }>; label: string; value: number }) {
  return (
    <div className="rounded-lg border border-neutral-800/80 bg-[#12151c] p-4">
      <div className="flex items-center gap-2 text-xs text-neutral-500">
        {React.cloneElement(icon, { className: "h-3.5 w-3.5", strokeWidth: 1.5 })}
        {label}
      </div>
      <p className="mt-3 font-mono text-2xl text-neutral-100">{value}</p>
    </div>
  );
}
