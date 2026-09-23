import React from "react";
import { User, Monitor, Sparkles } from "lucide-react";
import { Badge, StatusDot } from "@/shared/ui";
import { HostBadge } from "@/features/diagnostics/HostBadge";
import { TicketListItem, TriageResult } from "@/shared/api";

export interface TicketRowProps {
  ticket: TicketListItem;
  isSelected: boolean;
  onSelect: (ticketId: number) => void;
  aiResult?: TriageResult;
  onClassify?: (e: React.MouseEvent, ticket: TicketListItem) => void;
  isClassifying?: boolean;
}

export const TicketRow: React.FC<TicketRowProps> = ({
  ticket,
  isSelected,
  onSelect,
  aiResult,
}) => {
  // Format relative or local time
  const formatTime = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  };

  return (
    <div
      onClick={() => onSelect(ticket.id)}
      className={`px-3 py-2.5 rounded-lg border transition-all cursor-pointer group text-xs ${
        isSelected
          ? "bg-[#181d2c] border-indigo-500/80 shadow-md ring-1 ring-indigo-500/40 text-slate-100"
          : "bg-[#11141c] border-[#1e2330] hover:border-[#2a3246] hover:bg-[#141824] text-slate-300"
      }`}
    >
      {/* Top line: ID, StatusDot, Title, Service Badge, Time */}
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <StatusDot statusId={ticket.status_id} statusName={ticket.status_name} size="sm" />
          <span className="font-mono font-bold text-indigo-400 shrink-0 text-[11px]">
            #{ticket.id}
          </span>
          <span className="font-medium text-slate-200 truncate leading-tight group-hover:text-white">
            {ticket.name || "(Без темы)"}
          </span>
        </div>

        <span className="font-mono text-[10px] text-slate-500 shrink-0">
          {formatTime(ticket.created)}
        </span>
      </div>

      {/* Middle line: Applicant, Host PC, Service Badge */}
      <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400">
        <div className="flex items-center gap-3 min-w-0">
          <span className="flex items-center gap-1 truncate text-slate-300">
            <User className="w-3 h-3 text-slate-500 shrink-0" />
            <span className="truncate">{ticket.applicant_name || "Не указан"}</span>
          </span>

          {ticket.pc_name && (
            <div className="flex items-center gap-1 shrink-0">
              <HostBadge host={ticket.pc_name} autoCheck={false} />
            </div>
          )}
        </div>

        {ticket.service_name && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-[#171b26] border border-[#232a3b] text-slate-400 max-w-[140px] truncate shrink-0"
            title={ticket.service_name}
          >
            {ticket.service_name}
          </span>
        )}
      </div>

      {/* Optional AI Flag (e.g. Duplicate) */}
      {aiResult && (
        <div className="mt-1.5 pt-1.5 border-t border-[#1e2332] flex items-center justify-between text-[10px]">
          <span className="flex items-center gap-1 text-indigo-400 font-medium truncate">
            <Sparkles className="w-3 h-3 shrink-0" />
            {aiResult.is_duplicate ? (
              <span className="text-rose-400 font-bold">
                Дубликат заявки #{aiResult.duplicate_of_task_id}
              </span>
            ) : (
              <span>
                {aiResult.category} ({Math.round(aiResult.confidence * 100)}%)
              </span>
            )}
          </span>
        </div>
      )}
    </div>
  );
};
