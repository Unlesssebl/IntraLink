import React from "react";
import { User, MessageSquare, Zap, Paperclip } from "lucide-react";
import { StatusDot } from "@/shared/ui";
import { HostBadge } from "@/features/diagnostics/HostBadge";
import { TicketListItem } from "@/shared/api";

export interface TicketRowProps {
  ticket: TicketListItem;
  isSelected: boolean;
  onSelect: (ticketId: number) => void;
  isSelectedForBatch?: boolean;
  onToggleBatchSelect?: (ticketId: number) => void;
}

export const TicketRow = React.memo<TicketRowProps>(function TicketRow({
  ticket,
  isSelected,
  onSelect,
  isSelectedForBatch = false,
  onToggleBatchSelect,
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
      {/* Top line: Selection Checkbox, StatusDot, ID, Title, Badges, Time */}
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          {onToggleBatchSelect && (
            <div
              className="flex items-center shrink-0"
              onClick={(e) => e.stopPropagation()}
            >
              <input
                type="checkbox"
                checked={isSelectedForBatch}
                onChange={() => onToggleBatchSelect(ticket.id)}
                className="w-3.5 h-3.5 rounded border-neutral-700 bg-neutral-900 text-blue-500 focus:ring-0 focus:ring-offset-0 cursor-pointer accent-blue-600"
                aria-label={`Выбрать тикет #${ticket.id}`}
              />
            </div>
          )}
          <StatusDot statusId={ticket.status_id} statusName={ticket.status_name} size="sm" />
          <span className="font-mono font-medium text-neutral-400 shrink-0 text-[11px] group-hover:text-neutral-200">
            #{ticket.id}
          </span>
          <span className="font-medium text-neutral-200 truncate leading-tight group-hover:text-white">
            {ticket.name || "(Без темы)"}
          </span>
          {ticket.is_tense && (
            <span
              className="inline-flex items-center gap-0.5 px-1 py-0.2 rounded text-[9px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40 shrink-0 animate-pulse"
              title={ticket.tense_reason || "Срочный / обеспокоенный тон"}
            >
              <Zap className="w-2.5 h-2.5 text-amber-400" />
              ⚡ Срочно
            </span>
          )}
          {ticket.has_attachments && (
            <span
              className="inline-flex items-center gap-0.5 px-1 py-0.2 rounded text-[9px] font-medium bg-blue-500/20 text-blue-300 border border-blue-500/40 shrink-0"
              title="Есть прикрепленные файлы"
            >
              <Paperclip className="w-2.5 h-2.5 text-blue-400" />
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <span className="font-mono text-[10px] text-neutral-500">
            {formatTime(ticket.created)}
          </span>
        </div>
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

      {/* Autonomous Background Classification Badge */}
      {ticket.scenario_name && (
        <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-neutral-400">
          <span className="px-1.5 py-0.5 rounded bg-purple-950/40 border border-purple-800/60 text-purple-300 font-medium truncate max-w-[240px]">
            {ticket.scenario_name}
          </span>
          {ticket.confidence !== undefined && ticket.confidence !== null && (
            <span className="font-mono text-[9px] text-neutral-500">
              {Math.round(ticket.confidence * 100)}%
            </span>
          )}
        </div>
      )}

      {/* Status 6: Applicant dialogue indicator */}
      {ticket.status_id === 6 && (
        <div className="mt-1.5 pt-1.5 border-t border-neutral-800/80 flex items-center gap-1.5 text-[10px] text-blue-400">
          <MessageSquare className="w-3 h-3 shrink-0" />
          <span className="font-medium">Ожидает ответа заявителя (Автономный диалог)</span>
        </div>
      )}
    </div>
  );
});
