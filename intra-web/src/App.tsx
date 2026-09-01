import { useState, useEffect, useCallback } from 'react';
import type { Ticket, Page, ToastMessage } from './data/mock';
import type { SidebarMode, ServiceSelection } from './components/Sidebar';
import LoginPage from './pages/LoginPage';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import QueuePage from './pages/QueuePage';
import CommandPalette from './components/CommandPalette';
import ToastContainer from './components/Toast';
import { AuthProvider, useAuth } from './lib/auth';
import { fetchQueue } from './lib/tasks';

function MainApp() {
  const { isLoggedIn, user, loading: authLoading, logout } = useAuth();
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [currentPage, setCurrentPage] = useState<Page>('queue');
  const [sidebarMode, setSidebarMode] = useState<SidebarMode>(() => {
    const saved = localStorage.getItem('intralink_sidebar_mode');
    return (saved === 'compact' || saved === 'hidden' || saved === 'full') ? saved : 'full';
  });
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [cmdPaletteOpen, setCmdPaletteOpen] = useState(false);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [rootServices, setRootServices] = useState<Array<{ id: number; name: string }>>([]);
  const [subservicesByRoot, setSubservicesByRoot] = useState<Record<number, Array<{ id: number; name: string; parent_id?: number }>>>({});
  const [selectedService, setSelectedService] = useState<ServiceSelection>({
    rootId: null,
    serviceId: null,
    name: null,
  });
  const [loadingTickets, setLoadingTickets] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const handleSetSidebarMode = useCallback((mode: SidebarMode) => {
    setSidebarMode(mode);
    localStorage.setItem('intralink_sidebar_mode', mode);
  }, []);

  const handleCycleSidebarMode = useCallback(() => {
    setSidebarMode(prev => {
      let next: SidebarMode = 'compact';
      if (prev === 'full') next = 'compact';
      else if (prev === 'compact') next = 'hidden';
      else next = 'full';
      localStorage.setItem('intralink_sidebar_mode', next);
      return next;
    });
  }, []);

  // Apply dark class to html element
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }, [theme]);

  // Load live tickets and services from real API when logged in
  const loadQueue = useCallback(async () => {
    if (!isLoggedIn) return;
    setLoadingTickets(true);
    setQueueError(null);
    try {
      const data = await fetchQueue(984, 100);
      setTickets(data.tickets || []);
      setRootServices(data.rootServices || []);
      setSubservicesByRoot(data.subservicesByRoot || {});
    } catch (err: any) {
      console.error('Ошибка загрузки очереди заявок:', err);
      setQueueError(err.message || 'Не удалось загрузить очередь заявок');
      setTickets([]);
    } finally {
      setLoadingTickets(false);
    }
  }, [isLoggedIn]);

  useEffect(() => {
    if (isLoggedIn) {
      loadQueue();
    }
  }, [isLoggedIn, loadQueue]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        setCmdPaletteOpen(true);
      }
      if (e.key === 'Escape') {
        if (cmdPaletteOpen) setCmdPaletteOpen(false);
        else if (selectedTicketId) setSelectedTicketId(null);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [cmdPaletteOpen, selectedTicketId]);

  const addToast = useCallback((t: Omit<ToastMessage, 'id'>) => {
    setToasts(prev => [...prev, { id: `toast-${Date.now()}-${Math.random()}`, ...t }]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const updateTicket = useCallback((id: string, changes: Partial<Ticket>) => {
    setTickets(prev => prev.map(t => (t.id === id ? { ...t, ...changes } : t)));
  }, []);

  const selectedTicket = tickets.find(t => t.id === selectedTicketId) ?? null;

  if (authLoading) {
    return (
      <div className="h-full flex items-center justify-center bg-neutral-50 dark:bg-neutral-950 text-neutral-500">
        <div className="flex items-center gap-2 text-sm">
          <svg className="animate-spin h-4 w-4 text-neutral-600 dark:text-neutral-400" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
          </svg>
          <span>Загрузка системы...</span>
        </div>
      </div>
    );
  }

  if (!isLoggedIn) {
    return <LoginPage />;
  }

  return (
    <div className="h-full flex overflow-hidden bg-neutral-50 dark:bg-neutral-950 text-neutral-900 dark:text-neutral-100">
      {/* Sidebar with Real Service Catalog */}
      <Sidebar
        currentPage={currentPage}
        onNavigate={page => {
          setCurrentPage(page);
          setSelectedTicketId(null);
        }}
        theme={theme}
        onToggleTheme={() => setTheme(t => (t === 'light' ? 'dark' : 'light'))}
        sidebarMode={sidebarMode}
        onSetSidebarMode={handleSetSidebarMode}
        tickets={tickets}
        rootServices={rootServices}
        subservicesByRoot={subservicesByRoot}
        selectedService={selectedService}
        onSelectService={sel => {
          setSelectedService(sel);
          setSelectedTicketId(null);
        }}
        username={user?.username}
        onLogout={logout}
      />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Topbar
          currentPage={currentPage}
          selectedTicket={selectedTicket}
          sidebarMode={sidebarMode}
          onCycleSidebarMode={handleCycleSidebarMode}
          onOpenCmdPalette={() => setCmdPaletteOpen(true)}
          onRefresh={loadQueue}
          selectedService={selectedService}
          onResetService={() => setSelectedService({ rootId: null, serviceId: null, name: null })}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
        />

        <main className="flex-1 overflow-hidden relative">
          {loadingTickets && (
            <div className="absolute inset-0 bg-white/60 dark:bg-neutral-950/60 z-20 flex items-center justify-center backdrop-blur-2xs">
              <div className="flex items-center gap-2 text-sm text-neutral-600 dark:text-neutral-400 bg-white dark:bg-neutral-900 px-4 py-2 rounded-lg border border-neutral-200 dark:border-neutral-800 shadow-md">
                <svg className="animate-spin h-4 w-4 text-blue-500" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                </svg>
                <span>Обновление очереди...</span>
              </div>
            </div>
          )}

          {queueError && (
            <div className="m-4 p-4 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 flex items-center justify-between">
              <div className="flex items-center gap-2.5 text-sm text-red-800 dark:text-red-200">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="shrink-0">
                  <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5"/>
                  <path d="M8 4.5v4.5M8 11.5v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                <span>{queueError}</span>
              </div>
              <button
                onClick={loadQueue}
                className="px-3 py-1 bg-red-800 dark:bg-red-200 text-white dark:text-red-950 rounded text-xs font-semibold hover:bg-red-700 transition-colors cursor-pointer"
              >
                Повторить
              </button>
            </div>
          )}

          <QueuePage
            tickets={tickets}
            selectedTicketId={selectedTicketId}
            onSelectTicket={setSelectedTicketId}
            onUpdateTicket={updateTicket}
            onRefresh={loadQueue}
            onToast={addToast}
            selectedService={selectedService}
            onResetService={() => setSelectedService({ rootId: null, serviceId: null, name: null })}
            searchQuery={searchQuery}
          />
        </main>
      </div>

      {/* Command Palette */}
      {cmdPaletteOpen && (
        <CommandPalette
          tickets={tickets}
          onClose={() => setCmdPaletteOpen(false)}
          onSelectTicket={id => {
            setSelectedTicketId(id);
            setCurrentPage('queue');
            setCmdPaletteOpen(false);
          }}
          onNavigate={page => {
            setCurrentPage(page);
            setCmdPaletteOpen(false);
          }}
        />
      )}

      {/* Toast Notifications */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <MainApp />
    </AuthProvider>
  );
}
