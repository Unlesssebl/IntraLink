import React, { useState, useEffect, useMemo } from "react";
import {
  Inbox,
  RefreshCw,
  Search,
  CheckCheck,
  AlertTriangle,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { Button, Badge, KbdBadge } from "@/shared/ui";
import { TicketRow } from "./TicketRow";
import {
  ticketsApi,
  triageApi,
  TicketListItem,
  TriageResult,
} from "@/shared/api";

export interface TriageQueueProps {
  onSelectTicket: (ticketId: number) => void;
  selectedTicketId: number | null;
}

type FilterCategory = "all" | "new" | "in_progress" | "directum" | "1c";

export const TriageQueue: React.FC<TriageQueueProps> = ({
  onSelectTicket,
  selectedTicketId,
}) => {
  const [tickets, setTickets] = useState<TicketListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<FilterCategory>("all");

  // AI Classification cache: ticketId -> TriageResult
  const [triageResults, setTriageResults] = useState<Record<number, TriageResult>>({});
  const [classifyingId, setClassifyingId] = useState<number | null>(null);

  const fetchQueue = async () => {
    try {
      setLoading(true);
      setError(null);
      // Filter 984 = standard 1st line queue
      const list = await ticketsApi.list({ filter_id: 984, limit: 50 });
      setTickets(list);
      // Auto-select first ticket if none selected
      if (!selectedTicketId && list.length > 0) {
        onSelectTicket(list[0].id);
      }
    } catch (err: any) {
      setError(err?.message || "Не удалось загрузить очередь заявок");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchQueue();
  }, []);

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

      // 2. Category filter
      if (activeFilter === "new") {
        return t.status_id === 1 || (t.status_name || "").toLowerCase().includes("нов");
      }
      if (activeFilter === "in_progress") {
        return t.status_id === 2 || (t.status_name || "").toLowerCase().includes("работ");
      }
      if (activeFilter === "directum") {
        const sName = (t.service_name || "").toLowerCase();
        return sName.includes("directum") || [40, 41, 55, 232, 233, 234].includes(t.service_id || 0);
      }
      if (activeFilter === "1c") {
        const sName = (t.service_name || "").toLowerCase();
        return sName.includes("1с") || sName.includes("1c");
      }

      return true;
    });
  }, [tickets, searchQuery, activeFilter]);

  // Keyboard navigation: J/K or Up/Down arrows to switch tickets
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if typing in input or textarea
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

  const handleClassify = async (e: React.MouseEvent, t: TicketListItem) => {
    e.stopPropagation();
    try {
      setClassifyingId(t.id);
      const res = await triageApi.classify({
        task_id: t.id,
        name: t.name,
        description: t.name,
        applicant_name: t.applicant_name || undefined,
        pc_name: t.pc_name || undefined,
        service_name: t.service_name || undefined,
      });
      setTriageResults((prev) => ({ ...prev, [t.id]: res }));
    } catch (err: any) {
      alert(`Ошибка анализа AI: ${err?.message}`);
    } finally {
      setClassifyingId(null);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0c0f16] border-r border-[#1e2330]">
      {/* Top Header */}
      <div className="p-3 border-b border-[#1e2330] space-y-2.5 shrink-0 bg-[#0e111a]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Inbox className="w-4 h-4 text-indigo-400" />
            <span className="text-xs font-semibold text-slate-100">
              Очередь 1-й линии
            </span>
            <Badge variant="accent">{filteredTickets.length}</Badge>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="hidden sm:inline-flex items-center gap-1 text-[10px] text-slate-500 mr-1 font-mono">
              <KbdBadge shortcut="J" /> <KbdBadge shortcut="K" />
            </span>
            <Button
              size="sm"
              variant="secondary"
              loading={loading}
              icon={<RefreshCw className="w-3.5 h-3.5" />}
              onClick={fetchQueue}
              title="Обновить список"
            />
          </div>
        </div>

        {/* Live Search input */}
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Фильтр по номеру, теме, заявителю..."
            className="w-full bg-[#131722] border border-[#232a3b] rounded-md pl-8 pr-3 py-1 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        {/* Category filter tabs */}
        <div className="flex items-center gap-1 overflow-x-auto pb-0.5 text-[11px] no-scrollbar">
          <button
            onClick={() => setActiveFilter("all")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap ${
              activeFilter === "all"
                ? "bg-indigo-600/20 text-indigo-300 font-semibold border border-indigo-500/40"
                : "text-slate-400 hover:text-slate-200 hover:bg-[#181d2a]"
            }`}
          >
            Все ({tickets.length})
          </button>
          <button
            onClick={() => setActiveFilter("new")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap ${
              activeFilter === "new"
                ? "bg-indigo-600/20 text-indigo-300 font-semibold border border-indigo-500/40"
                : "text-slate-400 hover:text-slate-200 hover:bg-[#181d2a]"
            }`}
          >
            Новые
          </button>
          <button
            onClick={() => setActiveFilter("in_progress")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap ${
              activeFilter === "in_progress"
                ? "bg-indigo-600/20 text-indigo-300 font-semibold border border-indigo-500/40"
                : "text-slate-400 hover:text-slate-200 hover:bg-[#181d2a]"
            }`}
          >
            В работе
          </button>
          <button
            onClick={() => setActiveFilter("directum")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap ${
              activeFilter === "directum"
                ? "bg-indigo-600/20 text-indigo-300 font-semibold border border-indigo-500/40"
                : "text-slate-400 hover:text-slate-200 hover:bg-[#181d2a]"
            }`}
          >
            Directum
          </button>
          <button
            onClick={() => setActiveFilter("1c")}
            className={`px-2 py-0.5 rounded transition-colors whitespace-nowrap ${
              activeFilter === "1c"
                ? "bg-indigo-600/20 text-indigo-300 font-semibold border border-indigo-500/40"
                : "text-slate-400 hover:text-slate-200 hover:bg-[#181d2a]"
            }`}
          >
            1С
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="p-3 m-3 bg-red-950/40 border border-red-800/60 rounded-lg text-xs text-red-300 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{error}</span>
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
            onClassify={handleClassify}
            isClassifying={classifyingId === t.id}
          />
        ))}

        {!loading && filteredTickets.length === 0 && (
          <div className="text-center py-12 px-4 space-y-2">
            <CheckCheck className="w-7 h-7 text-emerald-400 mx-auto" />
            <h4 className="text-xs font-semibold text-slate-200">
              {searchQuery ? "Ничего не найдено" : "Очередь 1-й линии пуста"}
            </h4>
            <p className="text-[11px] text-slate-500">
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
