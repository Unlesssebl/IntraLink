import React, { useState, useEffect } from "react";
import {
  Inbox,
  BookOpen,
  BarChart3,
  Search,
  Activity,
  CheckCircle2,
  Terminal,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/shared/ui";
import { TriageQueue } from "@/features/triage/TriageQueue";
import { TicketInspector } from "@/features/tickets/TicketInspector";
import { KBSearch } from "@/features/knowledge-base/KBSearch";
import { LoadReport } from "@/features/reports/LoadReport";

type Tab = "triage" | "kb" | "reports";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("triage");
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null);
  const [backendStatus, setBackendStatus] = useState<"online" | "checking" | "offline">("checking");
  const [searchTicketQuery, setSearchTicketQuery] = useState("");

  const checkHealth = async () => {
    try {
      const res = await fetch("/api/v2/health");
      if (res.ok) {
        setBackendStatus("online");
      } else {
        setBackendStatus("offline");
      }
    } catch {
      // In local dev without running api container, consider checking
      setBackendStatus("offline");
    }
  };

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleQuickSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = searchTicketQuery.trim().replace("#", "");
    const id = parseInt(trimmed, 10);
    if (!isNaN(id) && id > 0) {
      setSelectedTicketId(id);
      setSearchTicketQuery("");
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#090a0f] text-slate-100 select-none">
      {/* Sidebar */}
      <aside className="w-64 bg-[#0d1017] border-r border-[#1e2330] flex flex-col shrink-0">
        {/* Logo Header */}
        <div className="p-4 border-b border-[#1e2330] flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="h-7 w-7 rounded-md bg-indigo-600 flex items-center justify-center font-bold text-white shadow-sm shadow-indigo-500/20">
              <Terminal className="w-4 h-4" />
            </div>
            <div>
              <div className="font-semibold text-xs tracking-tight text-white flex items-center gap-1.5">
                IntraLink <span className="text-[10px] text-indigo-400 font-mono">v2.0</span>
              </div>
              <div className="text-[10px] text-slate-500">Vertical Slice Architecture</div>
            </div>
          </div>
        </div>

        {/* Navigation Items */}
        <nav className="flex-1 p-3 space-y-1">
          <button
            onClick={() => setActiveTab("triage")}
            className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
              activeTab === "triage"
                ? "bg-indigo-600/15 text-indigo-300 border border-indigo-500/30"
                : "text-slate-400 hover:text-slate-200 hover:bg-[#141822]"
            }`}
          >
            <Inbox className="w-4 h-4" />
            <span>Очередь 1-й линии</span>
          </button>

          <button
            onClick={() => setActiveTab("kb")}
            className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
              activeTab === "kb"
                ? "bg-indigo-600/15 text-indigo-300 border border-indigo-500/30"
                : "text-slate-400 hover:text-slate-200 hover:bg-[#141822]"
            }`}
          >
            <BookOpen className="w-4 h-4" />
            <span>База знаний (RAG)</span>
          </button>

          <button
            onClick={() => setActiveTab("reports")}
            className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
              activeTab === "reports"
                ? "bg-indigo-600/15 text-indigo-300 border border-indigo-500/30"
                : "text-slate-400 hover:text-slate-200 hover:bg-[#141822]"
            }`}
          >
            <BarChart3 className="w-4 h-4" />
            <span>Аналитика нагрузки</span>
          </button>
        </nav>

        {/* System & Backend Status Footer */}
        <div className="p-3 border-t border-[#1e2330] bg-[#0b0e14] space-y-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-slate-500 flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5" />
              API Gateway:
            </span>
            <Badge
              variant={backendStatus === "online" ? "success" : "neutral"}
              dot
              pulse={backendStatus === "checking"}
            >
              {backendStatus === "online" ? "v2 Online" : "Ready / Standby"}
            </Badge>
          </div>

          <div className="flex items-center gap-2 pt-1 border-t border-[#1a1f2b] text-[11px] text-slate-400">
            <div className="w-5 h-5 rounded-full bg-[#1e2433] flex items-center justify-center font-bold text-[10px] text-slate-300">
              Б
            </div>
            <div className="truncate">
              <div className="font-medium text-slate-300 leading-tight">Беликов Ален</div>
              <div className="text-[10px] text-slate-500 leading-tight">Инженер Helpdesk</div>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Topbar */}
        <header className="h-12 border-b border-[#1e2330] bg-[#0c0f16] px-6 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span>IntraLink</span>
            <span>/</span>
            <span className="text-slate-200 font-medium">
              {activeTab === "triage" && "Диспетчер очереди (Filter 984)"}
              {activeTab === "kb" && "Семантический RAG поиск"}
              {activeTab === "reports" && "Сводный отчет нагрузки"}
            </span>
          </div>

          {/* Quick jump to Ticket ID */}
          <form onSubmit={handleQuickSearch} className="relative w-64">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              value={searchTicketQuery}
              onChange={(e) => setSearchTicketQuery(e.target.value)}
              placeholder="Открыть заявку по #ID..."
              className="w-full bg-[#131722] border border-[#232938] rounded-md pl-8 pr-3 py-1 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
            />
          </form>
        </header>

        {/* Viewport */}
        <main className="flex-1 overflow-y-auto p-6">
          {activeTab === "triage" && (
            <TriageQueue
              onSelectTicket={(id) => setSelectedTicketId(id)}
              selectedTicketId={selectedTicketId}
            />
          )}

          {activeTab === "kb" && <KBSearch />}

          {activeTab === "reports" && <LoadReport />}
        </main>
      </div>

      {/* Slide-over Ticket Inspector */}
      {selectedTicketId && (
        <TicketInspector
          ticketId={selectedTicketId}
          onClose={() => setSelectedTicketId(null)}
          onTicketUpdated={() => {
            // Can trigger refetch if needed
          }}
        />
      )}
    </div>
  );
}
