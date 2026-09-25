import React, { useState } from "react";
import { User, Sparkles, Loader2, Check, MessageSquare } from "lucide-react";
import { StatusDot, useToast } from "@/shared/ui";
import { HostBadge } from "@/features/diagnostics/HostBadge";
import { TicketListItem, TriageAnalysisResponse, triageApi } from "@/shared/api";
import { useQueryClient } from "@tanstack/react-query";
import { ticketKeys } from "@/features/tickets/queries";

export interface TicketRowProps {
  ticket: TicketListItem;
  isSelected: boolean;
  onSelect: (ticketId: number) => void;
  aiResult?: TriageAnalysisResponse;
  onAnalyze?: (e: React.MouseEvent, ticketId: number) => void;
  isAnalyzing?: boolean;
}

export const TicketRow = React.memo<TicketRowProps>(function TicketRow({
  ticket,
  isSelected,
  onSelect,
  aiResult,
  onAnalyze,
  isAnalyzing = false,
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [isApplying, setIsApplying] = useState(false);
  const [isApplied, setIsApplied] = useState(false);

  // Format relative or local time
  const formatTime = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  };

  const handleSparkleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onAnalyze && !isAnalyzing) {
      onAnalyze(e, ticket.id);
    }
  };

  const handleApplyClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!aiResult?.audit_id || isApplying || isApplied) return;
    try {
      setIsApplying(true);
      await triageApi.apply(aiResult.audit_id);
      setIsApplied(true);
      toast.success(`Решение AI применено к заявке #${ticket.id}`);
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    } catch (err: any) {
      toast.error(`Ошибка применения: ${err?.message || "Не удалось применить решение"}`);
    } finally {
      setIsApplying(false);
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
      {/* Top line: ID, StatusDot, Title, Analyze button, Time */}
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

        <div className="flex items-center gap-1.5 shrink-0">
          {onAnalyze && (
            <button
              type="button"
              onClick={handleSparkleClick}
              disabled={isAnalyzing}
              className={`p-1 rounded hover:bg-neutral-800 transition-colors ${
                aiResult
                  ? "text-purple-400 hover:text-purple-300"
                  : "text-neutral-500 hover:text-neutral-300"
              }`}
              title={aiResult ? "Повторный AI-анализ" : "Запустить AI-анализ тикета"}
            >
              {isAnalyzing ? (
                <Loader2 className="w-3 h-3 animate-spin text-purple-400" />
              ) : (
                <Sparkles className="w-3 h-3" />
              )}
            </button>
          )}
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

      {ticket.status_id === 6 && (
        <div className="mt-1.5 pt-1.5 border-t border-neutral-800/80 flex items-center gap-1.5 text-[10px] text-blue-400">
          <MessageSquare className="w-3 h-3 shrink-0" />
          <span className="font-medium">Ожидает ответа заявителя (Автономный диалог)</span>
        </div>
      )}

      {/* AI Decision Flag (Duplicate, Routing, Confidence) */}
      {aiResult && aiResult.decision && (
        <div className="mt-1.5 pt-1.5 border-t border-neutral-800/80 flex items-center justify-between text-[10px] gap-2">
          <div className="flex items-center gap-1.5 min-w-0">
            <span
              className="flex items-center gap-1.5 text-neutral-300 font-medium truncate"
              title={aiResult.decision.reason}
            >
              <Sparkles className="w-3 h-3 text-purple-400 shrink-0" />
              {aiResult.decision.is_duplicate ? (
                <span className="text-rose-400 font-semibold truncate">
                  Дубликат {aiResult.decision.master_ticket_id ? `#${aiResult.decision.master_ticket_id}` : ""}
                </span>
              ) : (
                <span className="truncate">
                  {aiResult.decision.action} ({Math.round(aiResult.decision.confidence * 100)}%)
                </span>
              )}
            </span>

            {aiResult.decision.target_service_name && (
              <span
                className="text-neutral-400 font-mono text-[9px] truncate max-w-[110px]"
                title={`Рекомендуемый сервис: ${aiResult.decision.target_service_name}`}
              >
                ➔ {aiResult.decision.target_service_name}
              </span>
            )}
          </div>

          {(aiResult.decision.suggested_status_id || aiResult.decision.suggested_comment) && (
            <button
              type="button"
              onClick={handleApplyClick}
              disabled={isApplying || isApplied}
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors flex items-center gap-1 shrink-0 ${
                isApplied
                  ? "bg-emerald-950/60 text-emerald-400 border border-emerald-800/60"
                  : "bg-purple-950/60 text-purple-300 border border-purple-800/70 hover:bg-purple-900/80 hover:text-white"
              }`}
              title="Применить рекомендацию AI с фиксацией в аудите"
            >
              {isApplying ? (
                <Loader2 className="w-2.5 h-2.5 animate-spin" />
              ) : isApplied ? (
                <>
                  <Check className="w-2.5 h-2.5" />
                  <span>Принято</span>
                </>
              ) : (
                <span>Принять</span>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  );
});
