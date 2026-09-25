import React, { useState, useEffect, useMemo } from "react";
import {
  Inbox,
  RefreshCw,
  Search,
  CheckCheck,
  AlertTriangle,
  Zap,
} from "lucide-react";
import { Button, Badge, KbdBadge, useToast } from "@/shared/ui";
import { TicketRow } from "./TicketRow";
import { useTicketQueue, ticketKeys } from "@/features/tickets/queries";
import { isStatusInWork, isStatusResolved, isStatusCancelled } from "@/shared/statuses";
import { autopilotApi } from "@/features/autopilot/api";
import { useQueryClient } from "@tanstack/react-query";

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
  const queryClient = useQueryClient();
  const {
    data: tickets = [],
    isLoading,
    isFetching,
    error,
    refetch,
  } = useTicketQueue(984);

  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<FilterCategory>("validation");
  const [selectedTicketIds, setSelectedTicketIds] = useState<number[]>([]);
  const [isBatchAssigning, setIsBatchAssigning] = useState(false);

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

  // Check if all currently visible tickets are selected
  const isAllSelected = useMemo(() => {
    if (filteredTickets.length === 0) return false;
    return filteredTickets.every((t) => selectedTicketIds.includes(t.id));
  }, [filteredTickets, selectedTicketIds]);

  const handleToggleSelectAll = () => {
    if (isAllSelected) {
      // Unselect all currently filtered tickets
      const filteredIdSet = new Set(filteredTickets.map((t) => t.id));
      setSelectedTicketIds((prev) => prev.filter((id) => !filteredIdSet.has(id)));
    } else {
      // Select all currently filtered tickets
      const newIds = new Set([...selectedTicketIds, ...filteredTickets.map((t) => t.id)]);
      setSelectedTicketIds(Array.from(newIds));
    }
  };

  const handleToggleBatchSelect = (ticketId: number) => {
    setSelectedTicketIds((prev) =>
      prev.includes(ticketId) ? prev.filter((id) => id !== ticketId) : [...prev, ticketId]
    );
  };

  const handleClearSelection = () => {
    setSelectedTicketIds([]);
  };

  const handleSyncQueue = async () => {
    await queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    await refetch();
    toast.success("Очередь синхронизирована с сервером");
  };

  const handleBatchAssign = async () => {
    if (selectedTicketIds.length === 0 || isBatchAssigning) return;
    try {
      setIsBatchAssigning(true);
      const res = await autopilotApi.batchAssign(selectedTicketIds);
      if (res.assigned_count > 0) {
        toast.success(`Передано автопилоту: ${res.assigned_count} заявок`);
      }
      if (res.failed_ids && res.failed_ids.length > 0) {
        toast.error(`Не удалось назначить ${res.failed_ids.length} заявок`);
      }
      setSelectedTicketIds([]);
      await queryClient.invalidateQueries({ queryKey: ticketKeys.all });
      await refetch();
    } catch (err: any) {
      toast.error(`Ошибка пакетного назначения: ${err?.message || "Сбой запроса"}`);
    } finally {
      setIsBatchAssigning(false);
    }
  };

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

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0c0d0e] border-r border-neutral-800/80 relative">
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
              loading={isFetching}
              icon={<RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />}
              onClick={handleSyncQueue}
              title="Синхронизировать очередь"
            >
              Синхронизировать
            </Button>
          </div>
        </div>

        {/* Pipeline Heartbeat Banner */}
        <div className="flex items-center justify-between bg-[#121418] px-2.5 py-1.5 rounded border border-neutral-800/80 text-[11px] text-neutral-300">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
            <span className="font-medium text-emerald-400">Конвейер активен</span>
            <span className="text-neutral-500">•</span>
            <span className="text-neutral-400">Пульс каждые 30с</span>
            <span className="text-neutral-500">•</span>
            <span className="text-neutral-400">Очередь актуальна</span>
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

        {/* Batch Select All Toolbar */}
        <div className="flex items-center justify-between text-[11px] text-neutral-400 pt-0.5">
          <label className="flex items-center gap-2 cursor-pointer hover:text-neutral-200 transition-colors select-none">
            <input
              type="checkbox"
              checked={isAllSelected}
              onChange={handleToggleSelectAll}
              className="w-3.5 h-3.5 rounded border-neutral-700 bg-neutral-900 text-blue-500 focus:ring-0 focus:ring-offset-0 cursor-pointer accent-blue-600"
            />
            <span>Выбрать все ({filteredTickets.length})</span>
          </label>
          {selectedTicketIds.length > 0 && (
            <span className="text-blue-400 font-medium">
              Выбрано: {selectedTicketIds.length}
            </span>
          )}
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
      <div className="flex-1 overflow-y-auto p-2 space-y-1.5 pb-20">
        {filteredTickets.map((t) => (
          <TicketRow
            key={t.id}
            ticket={t}
            isSelected={selectedTicketId === t.id}
            onSelect={onSelectTicket}
            isSelectedForBatch={selectedTicketIds.includes(t.id)}
            onToggleBatchSelect={handleToggleBatchSelect}
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

      {/* Floating Action Bar for Batch Operations (Linear Style) */}
      {selectedTicketIds.length > 0 && (
        <div className="absolute bottom-4 left-4 right-4 z-30 animate-in fade-in slide-in-from-bottom-2 duration-200">
          <div className="bg-[#14161b]/95 backdrop-blur-md border border-neutral-700/80 shadow-2xl rounded-lg p-2.5 flex items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2 pl-1.5">
              <span className="w-2 h-2 rounded-full bg-blue-400" />
              <span className="text-neutral-200 font-medium">
                Выбрано заявок: <span className="font-mono font-bold text-white">{selectedTicketIds.length}</span>
              </span>
            </div>

            <div className="flex items-center gap-2">
              <Button
                variant="primary"
                size="sm"
                loading={isBatchAssigning}
                onClick={handleBatchAssign}
                icon={<Zap className="w-3.5 h-3.5 text-amber-400" />}
                className="bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs shadow-md"
              >
                ⚡ Передать автопилоту (alen_assistant)
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClearSelection}
                className="text-neutral-400 hover:text-white text-xs"
              >
                Снять выбор
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
