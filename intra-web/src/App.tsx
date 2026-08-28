import { useState, useEffect, useCallback } from 'react';
import type { Ticket, Page, ToastMessage } from './data/mock';
import { mockTickets } from './data/mock';
import LoginPage from './pages/LoginPage';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import QueuePage from './pages/QueuePage';
import AutomationPage from './pages/AutomationPage';
import SettingsPage from './pages/SettingsPage';
import CommandPalette from './components/CommandPalette';
import ToastContainer from './components/Toast';
import { AuthProvider, useAuth } from './lib/auth';
import { fetchQueue } from './lib/tasks';

function MainApp() {
  const { isLoggedIn, user, loading: authLoading, logout } = useAuth();
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [currentPage, setCurrentPage] = useState<Page>('queue');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [cmdPaletteOpen, setCmdPaletteOpen] = useState(false);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loadingTickets, setLoadingTickets] = useState(false);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [operatorStatus, setOperatorStatus] = useState<'online' | 'away' | 'offline'>('online');

  // Apply dark class to html element
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }, [theme]);

  // Load tickets from real API when logged in
  const loadQueue = useCallback(async () => {
    if (!isLoggedIn) return;
    setLoadingTickets(true);
    try {
      const data = await fetchQueue(984, 100);
      if (data.tickets && data.tickets.length > 0) {
        setTickets(data.tickets);
      } else {
        // Fallback to mock data if empty during testing
        setTickets(mockTickets);
      }
    } catch (err) {
      console.warn('Не удалось загрузить реальные заявки, используем демо-данные:', err);
      setTickets(mockTickets);
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
    setToasts(prev => [...prev, { id: `toast-${Date.now()}`, ...t }]);
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
        <div className="text-sm">Загрузка системы...</div>
      </div>
    );
  }

  if (!isLoggedIn) {
    return <LoginPage />;
  }

  return (
    <div className="h-full flex overflow-hidden bg-neutral-50 dark:bg-neutral-950 text-neutral-900 dark:text-neutral-100">
      {/* Sidebar */}
      <Sidebar
        currentPage={currentPage}
        onNavigate={page => {
          setCurrentPage(page);
          setSelectedTicketId(null);
        }}
        theme={theme}
        onToggleTheme={() => setTheme(t => (t === 'light' ? 'dark' : 'light'))}
        sidebarOpen={sidebarOpen}
        operatorStatus={operatorStatus}
        onStatusChange={setOperatorStatus}
        tickets={tickets}
        onOpenSearch={() => setCmdPaletteOpen(true)}
        username={user?.username}
        onLogout={logout}
      />

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Topbar
          currentPage={currentPage}
          selectedTicket={selectedTicket}
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen(o => !o)}
          onOpenCmdPalette={() => setCmdPaletteOpen(true)}
        />

        <main className="flex-1 overflow-hidden">
          {currentPage === 'queue' && (
            <QueuePage
              tickets={tickets}
              selectedTicketId={selectedTicketId}
              onSelectTicket={setSelectedTicketId}
              onUpdateTicket={updateTicket}
              onToast={addToast}
            />
          )}
          {currentPage === 'automation' && <AutomationPage />}
          {currentPage === 'settings' && (
            <SettingsPage
              theme={theme}
              onToggleTheme={() => setTheme(t => (t === 'light' ? 'dark' : 'light'))}
            />
          )}
        </main>
      </div>

      {/* Command palette */}
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
            setCurrentPage(page as Page);
            setCmdPaletteOpen(false);
          }}
        />
      )}

      {/* Toasts */}
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
