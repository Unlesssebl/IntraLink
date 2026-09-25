import React, { useState, useEffect, useMemo } from "react";
import {
  Inbox,
  RefreshCw,
  Search,
  CheckCheck,
  AlertTriangle,
  Sparkles,
} from "lucide-react";
import { Button, Badge, KbdBadge, useToast } from "@/shared/ui";
import { TicketRow } from "./TicketRow";
import { useTicketQueue } from "@/features/tickets/queries";
import { isStatusInWork, isStatusResolved, isStatusCancelled } from "@/shared/statuses";
import { triageApi, TriageAnalysisResponse } from "@/shared/api";

export interface TriageQueueProps {
  onSelectTicket: (ticketId: number) => void;
  selectedTicketId: number | null;
}

type FilterCategory = "validation" | "dialogue" | "resolved" | "cancelled" | "all";

export const TriageQueue: React.FC<TriageQueueProps> = ({
  onSelectTicket,
  selectedTicketId,
}) => {
  const toast = useToast();
  const {
    data: tickets = [],
    isLoading,
    isFetching,
    error,
    refetch,
  } = useTicketQueue(984);

  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<FilterCategory>("validation");

  // AI Classification cache: ticketId -> TriageAnalysisResponse
  const [triageResults, setTriageResults] = useState<Record<number, TriageAnalysisResponse>>({});
  const [analyzingTicketId, setAnalyzingTicketId] = useState<number | null>(null);
  const [batchAnalyzing, setBatchAnalyzing] = useState(false);

  // Counters for pipeline tabs
  const counts = useMemo(() => {
    return {
      validation: tickets.filter((t) => (t.status_id === 1 || isStatusInWork(t.status_id)) && t.status_id !== 6).length,
      dialogue: tickets.filter((t) => t.status_id === 6).length,
      resolved: tickets.filter((t) => isStatusResolved(t.status_id)).length,
      cancelled: tickets.filter((t) => isStatusCancelled(t.status_id)).length,
      all: tickets.length,
    };
  }, [tickets]);

  // Filtered tickets memo
  const filteredTickets = useMemo(() => {
    return tickets.filter((t) => {
      // 1. Text search
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim();
        const matchesId = String(t.id).includes(q.replace("#", ""));
        const matchesName = (t.name || "").toLowerCase().includes(q);
        const matchesApplicant = (t.applicant_name || "").toLowerCase().includes(q);
        const matchesService = (t.service_name || "").toLowerCase().includes(q);
        const matchesHost = (t.pc_name || "").toLowerCase().includes(q);
        if (!matchesId && !matchesName && !matchesApplicant && !matchesService && !matchesHost) {
          return false;
        }
      }

      // 2. Pipeline state category filter
      if (activeFilter === "validation") {
        return (t.status_id === 1 || isStatusInWork(t.status_id)) && t.status_id !== 6;
      }
      if (activeFilter === "dialogue") {
        return t.status_id === 6;
      }
      if (activeFilter === "resolved") {
        return isStatusResolved(t.status_id);
      }
      if (activeFilter === "cancelled") {
        return isStatusCancelled(t.status_id);
      }

      return true;
    });
  }, [tickets, searchQuery, activeFilter]);

  // Auto-shift focus when selected ticket completes or disappears
  useEffect(() => {
    if (filteredTickets.length > 0) {
      const exists = filteredTickets.some((t) => t.id === selectedTicketId);
      if (!exists) {
        onSelectTicket(filteredTickets[0].id);
      }
    }
  }, [filteredTickets, selectedTicketId, onSelectTicket]);

  // Keyboard navigation: J/K or Up/Down arrows to switch tickets
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;

      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        if (filteredTickets.length === 0) return;
        const currentIndex = filteredTickets.findIndex((t) => t.id === selectedTicketId);
        const nextIndex = currentIndex < filteredTickets.length - 1 ? currentIndex + 1 : 0;
        onSelectTicket(filteredTickets[nextIndex].id);
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        if (filteredTickets.length === 0) return;
        const currentIndex = filteredTickets.findIndex((t) => t.id === selectedTicketId);
        const prevIndex = currentIndex > 0 ? currentIndex - 1 : filteredTickets.length - 1;
        onSelectTicket(filteredTickets[prevIndex].id);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [filteredTickets, selectedTicketId, onSelectTicket]);

  const handleAnalyzeSingle = async (e: React.MouseEvent, ticketId: number) => {
    e.stopPropagation();
    try {
      setAnalyzingTicketId(ticketId);
      const res = await triageApi.analyze(ticketId);
      setTriageResults((prev) => ({ ...prev, [ticketId]: res }));
      toast.success(`Анализ тикета #${ticketId} выполнен`);
    } catch (err: any) {
      toast.error(`Ошибка анализа AI: ${err?.message || "Сбой запроса"}`);
    } finally {
      setAnalyzingTicketId(null);
    }
  };

  const handleBatchAnalyze = async () => {
    try {
      setBatchAnalyzing(true);
      const res = await triageApi.batchAnalyze(984, 25);
      const map: Record<number, TriageAnalysisResponse> = {};
      for (const item of res.decisions) {
        map[item.ticket_id] = item;
      }
      setTriageResults((prev) => ({ ...prev, ...map }));
      toast.success(`Проанализировано ${res.total_analyzed} заявок`);
    } catch (err: any) {
      toast.error(`Ошибка пакетного анализа: ${err?.message || "Сбой запроса"}`);
    } finally {
      setBatchAnalyzing(false);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0c0d0e] border-r border-neutral-800/80">
      {/* Top Header */}
      <div className="p-3 border-b border-neutral-800/80 space-y-2.5 shrink-0 bg-[#0e1013]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Inbox className="w-4 h-4 text-neutral-400" />
            <span className="text-xs font-semibold text-neutral-100">
              Очередь 1-й линии
            </span>
            <Badge variant="neutral">{filteredTickets.length}</Badge>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="hidden sm:inline-flex items-center gap-1 text-[10px] text-neutral-500 mr-1 font-mono">
              <KbdBadge shortcut="J" /> <KbdBadge shortcut="K" />
            </span>
            <Button
              size="sm"
              variant="secondary"
              loading={batchAnalyzing}
              icon={<Sparkles className="w-3.5 h-3.5 text-purple-400" />}
              onClick={handleBatchAnalyze}
              title="Пакетный AI-анализ очереди"
            />
            <Button
              size="sm"
              variant="secondary"
              loading={isFetching}
              icon={<RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />}
              onClick={() => refetch()}
              title="Обновить список"
            />
          </div>
        </div>

        {/* Live Search input */}
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-neutral-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Фильтр по номеру, теме, заявителю..."
            className="w-full bg-[#121316] border border-neutral-800 rounded pl-8 pr-3 py-1.5 text-xs text-neutral-100 placeholder:text-neutral-500 focus:outline-none focus:ring-1 focus:ring-neutral-400 focus:border-neutral-500"
          />
        </div>

        {/* Pipeline state category filter tabs */}
        <div className="flex items-center gap-1 overflow-x-auto pb-0.5 text-[11px] no-scrollbar">
          <button
            onClick={() => setActiveFilter("validation")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap flex items-center gap-1.5 ${
              activeFilter === "validation"
                ? "bg-amber-950/60 text-amber-300 font-medium border border-amber-800/80 shadow-xs"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
            <span>Валидация ({counts.validation})</span>
          </button>
          <button
            onClick={() => setActiveFilter("dialogue")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap flex items-center gap-1.5 ${
              activeFilter === "dialogue"
                ? "bg-blue-950/60 text-blue-300 font-medium border border-blue-800/80 shadow-xs"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 shrink-0" />
            <span>Диалог ({counts.dialogue})</span>
          </button>
          <button
            onClick={() => setActiveFilter("resolved")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap flex items-center gap-1.5 ${
              activeFilter === "resolved"
                ? "bg-emerald-950/60 text-emerald-300 font-medium border border-emerald-800/80 shadow-xs"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
            <span>Решено ({counts.resolved})</span>
          </button>
          <button
            onClick={() => setActiveFilter("cancelled")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap flex items-center gap-1.5 ${
              activeFilter === "cancelled"
                ? "bg-rose-950/60 text-rose-300 font-medium border border-rose-800/80 shadow-xs"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-rose-400 shrink-0" />
            <span>Отклонено ({counts.cancelled})</span>
          </button>
          <button
            onClick={() => setActiveFilter("all")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap ${
              activeFilter === "all"
                ? "bg-neutral-800 text-neutral-100 font-medium border border-neutral-700 shadow-xs"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
            }`}
          >
            Все ({counts.all})
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="p-3 m-3 bg-rose-950/40 border border-rose-800/60 rounded text-xs text-rose-300 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
          <span>{(error as any)?.message || "Не удалось загрузить очередь заявок"}</span>
        </div>
      )}

      {/* Ticket List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {filteredTickets.map((t) => (
          <TicketRow
            key={t.id}
            ticket={t}
            isSelected={selectedTicketId === t.id}
            onSelect={onSelectTicket}
            aiResult={triageResults[t.id]}
            onAnalyze={handleAnalyzeSingle}
            isAnalyzing={analyzingTicketId === t.id}
          />
        ))}

        {!isLoading && filteredTickets.length === 0 && (
          <div className="text-center py-12 px-4 space-y-2">
            <CheckCheck className="w-7 h-7 text-emerald-500 mx-auto" />
            <h4 className="text-xs font-semibold text-neutral-200">
              {searchQuery ? "Ничего не найдено" : "Очередь 1-й линии пуста"}
            </h4>
            <p className="text-[11px] text-neutral-500">
              {searchQuery
                ? "Попробуйте изменить запрос поиска"
                : "Все поступившие заявки обработаны. Отличная работа!"}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
