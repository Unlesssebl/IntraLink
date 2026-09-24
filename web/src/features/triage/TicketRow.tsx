import React from "react";
import { User, Sparkles } from "lucide-react";
import { StatusDot } from "@/shared/ui";
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

export const TicketRow = React.memo<TicketRowProps>(function TicketRow({
  ticket,
  isSelected,
  onSelect,
  aiResult,
}) {
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
      className={`px-3 py-2.5 rounded border transition-all cursor-pointer group text-xs ${
        isSelected
          ? "bg-white/[0.08] border-white/20 text-neutral-100 shadow-xs"
          : "bg-neutral-900/50 border-neutral-800/80 hover:bg-neutral-800/50 hover:border-neutral-700/80 text-neutral-300"
      }`}
    >
      {/* Top line: ID, StatusDot, Title, Service Badge, Time */}
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <StatusDot statusId={ticket.status_id} statusName={ticket.status_name} size="sm" />
          <span className="font-mono font-medium text-neutral-400 shrink-0 text-[11px] group-hover:text-neutral-200">
            #{ticket.id}
          </span>
          <span className="font-medium text-neutral-200 truncate leading-tight group-hover:text-white">
            {ticket.name || "(Без темы)"}
          </span>
        </div>

        <span className="font-mono text-[10px] text-neutral-500 shrink-0">
          {formatTime(ticket.created)}
        </span>
      </div>

      {/* Middle line: Applicant, Host PC, Service Badge */}
      <div className="flex items-center justify-between gap-2 text-[11px] text-neutral-400">
        <div className="flex items-center gap-3 min-w-0">
          <span className="flex items-center gap-1 truncate text-neutral-300">
            <User className="w-3 h-3 text-neutral-500 shrink-0" />
            <span className="truncate">{ticket.applicant_name || "Не указан"}</span>
          </span>

          {ticket.pc_name && (
            <div className="flex items-center gap-1 shrink-0">
              <HostBadge host={ticket.pc_name} />
            </div>
          )}
        </div>

        {ticket.service_name && (
          <span
            className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-neutral-800/80 border border-neutral-700/70 text-neutral-400 max-w-[140px] truncate shrink-0"
            title={ticket.service_name}
          >
            {ticket.service_name}
          </span>
        )}
      </div>

      {/* Optional AI Flag (e.g. Duplicate) */}
      {aiResult && (
        <div className="mt-1.5 pt-1.5 border-t border-neutral-800/80 flex items-center justify-between text-[10px]">
          <span className="flex items-center gap-1 text-neutral-300 font-medium truncate">
            <Sparkles className="w-3 h-3 text-neutral-400 shrink-0" />
            {aiResult.is_duplicate ? (
              <span className="text-rose-400 font-semibold">
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
});
