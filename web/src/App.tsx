import React, { useState, useEffect } from "react";
import {
  Inbox,
  BookOpen,
  BarChart3,
  Search,
  Activity,
  Terminal,
  LogIn,
  LogOut,
  User,
  KeyRound,
} from "lucide-react";
import { Badge, Button, ErrorBoundary, Input, Modal } from "@/shared/ui";
import { TriageQueue } from "@/features/triage/TriageQueue";
import { TicketInspector } from "@/features/tickets/TicketInspector";
import { KBSearch } from "@/features/knowledge-base/KBSearch";
import { LoadReport } from "@/features/reports/LoadReport";
import {
  authApi,
  getStoredAuth,
  getStoredUserLogin,
  setStoredAuth,
  clearStoredAuth,
} from "@/shared/api";

import { useQueryClient } from "@tanstack/react-query";
import { ticketKeys } from "@/features/tickets/queries";

type Tab = "triage" | "kb" | "reports";

export default function App() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>("triage");
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null);
  const [isInspectorFullscreen, setIsInspectorFullscreen] = useState(false);
  const [backendStatus, setBackendStatus] = useState<"online" | "checking" | "offline">("checking");
  const [searchTicketQuery, setSearchTicketQuery] = useState("");

  // Auth state
  const [currentUser, setCurrentUser] = useState<string | null>(getStoredUserLogin());
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [loginInput, setLoginInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const checkHealth = async () => {
    try {
      const res = await fetch("/api/v2/health");
      if (res.ok) {
        setBackendStatus("online");
      } else {
        setBackendStatus("offline");
      }
    } catch {
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
      setActiveTab("triage");
      setSearchTicketQuery("");
    }
  };

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!loginInput.trim() || !passwordInput.trim()) {
      setAuthError("Введите логин и пароль");
      return;
    }

    try {
      setLoggingIn(true);
      setAuthError(null);
      const res = await authApi.login(loginInput.trim(), passwordInput.trim());
      setStoredAuth(res.auth_b64, res.login, res.user_id);
      setCurrentUser(res.login);
      setAuthModalOpen(false);
      setPasswordInput("");
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    } catch (err: any) {
      setAuthError(err?.message || "Ошибка авторизации");
    } finally {
      setLoggingIn(false);
    }
  };

  const handleLogout = () => {
    clearStoredAuth();
    setCurrentUser(null);
    queryClient.invalidateQueries({ queryKey: ticketKeys.all });
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#08090a] text-neutral-100 select-none font-sans">
      {/* Sidebar */}
      <aside className="w-56 bg-[#0c0d0e] border-r border-neutral-800/80 flex flex-col shrink-0">
        {/* Logo Header */}
        <div className="p-3.5 border-b border-neutral-800/80 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="h-6 w-6 rounded bg-neutral-100 flex items-center justify-center font-bold text-neutral-950 shadow-xs border border-neutral-300">
              <Terminal className="w-3.5 h-3.5" />
            </div>
            <div>
              <div className="font-semibold text-xs tracking-tight text-white flex items-center gap-1.5">
                IntraLink <span className="text-[10px] text-neutral-400 font-mono">v2.0</span>
              </div>
              <div className="text-[9px] text-neutral-500 font-mono">Operator Studio</div>
            </div>
          </div>
        </div>

        {/* Navigation Items */}
        <nav className="flex-1 p-2 space-y-1">
          <button
            onClick={() => setActiveTab("triage")}
            className={`w-full flex items-center gap-2 px-2.5 py-2 rounded text-xs font-medium transition-colors ${
              activeTab === "triage"
                ? "bg-neutral-800 text-white border border-neutral-700 shadow-xs"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
            }`}
          >
            <Inbox className="w-4 h-4" />
            <span>Очередь 1-й линии</span>
          </button>

          <button
            onClick={() => setActiveTab("kb")}
            className={`w-full flex items-center gap-2 px-2.5 py-2 rounded text-xs font-medium transition-colors ${
              activeTab === "kb"
                ? "bg-neutral-800 text-white border border-neutral-700 shadow-xs"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
            }`}
          >
            <BookOpen className="w-4 h-4" />
            <span>База знаний (RAG)</span>
          </button>

          <button
            onClick={() => setActiveTab("reports")}
            className={`w-full flex items-center gap-2 px-2.5 py-2 rounded text-xs font-medium transition-colors ${
              activeTab === "reports"
                ? "bg-neutral-800 text-white border border-neutral-700 shadow-xs"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
            }`}
          >
            <BarChart3 className="w-4 h-4" />
            <span>Аналитика нагрузки</span>
          </button>
        </nav>

        {/* System & Backend Status Footer */}
        <div className="p-2.5 border-t border-neutral-800/80 bg-[#0e1013] space-y-2">
          <div className="flex items-center justify-between text-[10px]">
            <span className="text-neutral-500 flex items-center gap-1">
              <Activity className="w-3 h-3" />
              API v2:
            </span>
            <Badge
              variant={backendStatus === "online" ? "success" : "neutral"}
              dot
              pulse={backendStatus === "checking"}
            >
              {backendStatus === "online" ? "Online" : "Standby"}
            </Badge>
          </div>

          <div className="flex items-center justify-between pt-1.5 border-t border-neutral-800/80 text-[11px]">
            <div className="flex items-center gap-1.5 min-w-0">
              <div className="w-5 h-5 rounded-full bg-neutral-800 flex items-center justify-center font-bold text-[10px] text-neutral-300 shrink-0">
                {currentUser ? currentUser[0].toUpperCase() : "?"}
              </div>
              <div className="truncate">
                <div className="font-medium text-neutral-200 text-xs truncate">
                  {currentUser || "Гость"}
                </div>
              </div>
            </div>
            {currentUser ? (
              <button
                onClick={handleLogout}
                title="Выйти"
                className="text-neutral-400 hover:text-rose-400 p-1 rounded hover:bg-neutral-800/60"
              >
                <LogOut className="w-3.5 h-3.5" />
              </button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setAuthModalOpen(true)}
              >
                Вход
              </Button>
            )}
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Topbar */}
        <header className="h-11 border-b border-neutral-800/80 bg-[#0e1013] px-4 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2 text-xs text-neutral-400">
            <span>IntraLink</span>
            <span>/</span>
            <span className="text-neutral-200 font-medium">
              {activeTab === "triage" && "Операторская панель очереди"}
              {activeTab === "kb" && "База знаний (RAG)"}
              {activeTab === "reports" && "Сводный отчет нагрузки"}
            </span>
          </div>

          <div className="flex items-center gap-3">
            {/* Quick jump to Ticket ID */}
            <form onSubmit={handleQuickSearch} className="relative w-56">
              <Search className="w-3.5 h-3.5 text-neutral-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchTicketQuery}
                onChange={(e) => setSearchTicketQuery(e.target.value)}
                placeholder="Поиск по #ID..."
                className="w-full bg-[#121316] border border-neutral-800 rounded pl-8 pr-3 py-1 text-xs text-neutral-200 placeholder:text-neutral-500 focus:outline-none focus:ring-1 focus:ring-neutral-400 focus:border-neutral-500 font-mono"
              />
            </form>

            {!currentUser && (
              <Button
                size="sm"
                variant="primary"
                icon={<LogIn className="w-3.5 h-3.5" />}
                onClick={() => setAuthModalOpen(true)}
              >
                Войти в IntraService
              </Button>
            )}
          </div>
        </header>

        {/* Viewport Content */}
        <div className={`flex-1 flex overflow-hidden ${activeTab === "triage" ? "" : "hidden"}`}>
          {/* Master Column: Triage Queue */}
          {!isInspectorFullscreen && (
            <div className="w-[380px] lg:w-[410px] shrink-0 h-full flex flex-col">
              <TriageQueue
                onSelectTicket={(id) => setSelectedTicketId(id)}
                selectedTicketId={selectedTicketId}
              />
            </div>
          )}

          {/* Detail Column: Modular Inspector */}
          <div className="flex-1 h-full flex flex-col min-w-0 bg-[#08090a]">
            <TicketInspector
              ticketId={selectedTicketId}
              onClose={() => setSelectedTicketId(null)}
              isFullscreen={isInspectorFullscreen}
              onToggleFullscreen={() =>
                setIsInspectorFullscreen(!isInspectorFullscreen)
              }
            />
          </div>
        </div>

        <main className={`flex-1 overflow-y-auto p-6 ${activeTab === "kb" ? "" : "hidden"}`}>
          <ErrorBoundary fallbackTitle="Ошибка в модуле Базы знаний">
            <KBSearch />
          </ErrorBoundary>
        </main>

        <main className={`flex-1 overflow-y-auto p-6 ${activeTab === "reports" ? "" : "hidden"}`}>
          <ErrorBoundary fallbackTitle="Ошибка в модуле Аналитики и отчетов">
            <LoadReport />
          </ErrorBoundary>
        </main>
      </div>

      {/* Login Modal */}
      <Modal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        title="Авторизация в IntraService"
        description="Введите логин и пароль вашей учетной записи Helpdesk"
        maxWidth="sm"
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setAuthModalOpen(false)}
            >
              Отмена
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={loggingIn}
              onClick={handleLoginSubmit}
            >
              Войти
            </Button>
          </>
        }
      >
        <form onSubmit={handleLoginSubmit} className="space-y-3">
          <Input
            label="Логин IntraService"
            placeholder="например: belikov.a"
            value={loginInput}
            onChange={(e) => setLoginInput(e.target.value)}
            icon={<User className="w-3.5 h-3.5" />}
            autoFocus
          />
          <Input
            label="Пароль"
            type="password"
            placeholder="••••••••"
            value={passwordInput}
            onChange={(e) => setPasswordInput(e.target.value)}
            icon={<KeyRound className="w-3.5 h-3.5" />}
          />
          {authError && (
            <div className="p-2.5 bg-rose-950/40 border border-rose-800/60 rounded text-[11px] text-rose-300">
              {authError}
            </div>
          )}
        </form>
      </Modal>
    </div>
  );
}
