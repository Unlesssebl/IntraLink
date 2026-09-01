import type { Page, Ticket } from '../data/mock';
import { savedFilters } from '../data/mock';

interface Props {
  currentPage: Page;
  onNavigate: (page: Page) => void;
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
  sidebarOpen: boolean;
  operatorStatus: 'online' | 'away' | 'offline';
  onStatusChange: (s: 'online' | 'away' | 'offline') => void;
  tickets: Ticket[];
  onOpenSearch: () => void;
  username?: string;
  onLogout?: () => void;
}

const navItems: { id: Page; label: string; icon: React.ReactNode }[] = [
  {
    id: 'queue',
    label: 'Очередь заявок',
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <rect x="1" y="2" width="13" height="2" rx="1" fill="currentColor"/>
        <rect x="1" y="6.5" width="13" height="2" rx="1" fill="currentColor"/>
        <rect x="1" y="11" width="9" height="2" rx="1" fill="currentColor"/>
      </svg>
    ),
  },
  {
    id: 'automation',
    label: 'Центр автоматизации',
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M7.5 1.5L9.5 5.5H13.5L10.5 8L11.5 12.5L7.5 10L3.5 12.5L4.5 8L1.5 5.5H5.5L7.5 1.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
      </svg>
    ),
  },
  {
    id: 'settings',
    label: 'Настройки',
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <circle cx="7.5" cy="7.5" r="2" stroke="currentColor" strokeWidth="1.3"/>
        <path d="M7.5 1v1.5M7.5 12.5V14M1 7.5h1.5M12.5 7.5H14M2.93 2.93l1.06 1.06M11.01 11.01l1.06 1.06M2.93 12.07l1.06-1.06M11.01 3.99l1.06-1.06" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      </svg>
    ),
  },
];

const statusDot = {
  online: 'bg-green-500',
  away: 'bg-amber-500',
  offline: 'bg-neutral-400',
};

const statusLabel = {
  online: 'Онлайн',
  away: 'Не за компьютером',
  offline: 'Не в сети',
};

function getInitials(name?: string): string {
  if (!name) return 'ОП';
  const parts = name.trim().split(/[\s._\\/]+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

export default function Sidebar({
  currentPage, onNavigate, theme, onToggleTheme,
  sidebarOpen, operatorStatus, onStatusChange, tickets, onOpenSearch,
  username, onLogout,
}: Props) {
  const counts: Record<Page, number> = {
    queue: tickets.filter(t => t.status !== 'resolved').length,
    automation: 0,
    settings: 0,
  };

  const newCount = tickets.filter(t => t.status === 'new').length;

  if (!sidebarOpen) return null;

  return (
    <aside className="w-[224px] shrink-0 h-full flex flex-col bg-neutral-100 dark:bg-neutral-900 border-r border-neutral-200 dark:border-neutral-800">
      {/* Workspace header */}
      <div className="px-3 pt-4 pb-2">
        <div className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-neutral-200 dark:hover:bg-neutral-800 cursor-pointer transition-colors">
          <div className="w-5 h-5 bg-neutral-900 dark:bg-neutral-100 rounded flex items-center justify-center shrink-0">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <rect x="0.5" y="0.5" width="3.5" height="3.5" rx="0.5" fill="currentColor" className="text-neutral-100 dark:text-neutral-900"/>
              <rect x="6" y="0.5" width="3.5" height="3.5" rx="0.5" fill="currentColor" className="text-neutral-100 dark:text-neutral-900"/>
              <rect x="0.5" y="6" width="3.5" height="3.5" rx="0.5" fill="currentColor" className="text-neutral-100 dark:text-neutral-900"/>
              <rect x="6" y="6" width="3.5" height="3.5" rx="0.5" fill="currentColor" className="text-neutral-400"/>
            </svg>
          </div>
          <span className="text-[13px] font-semibold text-neutral-800 dark:text-neutral-200 tracking-tight truncate">
            IntraLink Helpdesk
          </span>
        </div>
      </div>

      {/* Search */}
      <div className="px-3 pb-2">
        <button
          onClick={onOpenSearch}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-neutral-500 dark:text-neutral-400 hover:bg-neutral-200 dark:hover:bg-neutral-800 transition-colors text-xs cursor-pointer"
        >
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <circle cx="5.5" cy="5.5" r="3.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M9 9l2.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          <span>Поиск</span>
          <span className="ml-auto font-mono text-[10px] bg-neutral-200 dark:bg-neutral-800 px-1 py-0.5 rounded">
            ⌃K
          </span>
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 overflow-y-auto">
        <div className="space-y-0.5">
          {navItems.map(item => (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-[13px] transition-colors text-left cursor-pointer ${
                currentPage === item.id
                  ? 'bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-medium shadow-sm'
                  : 'text-neutral-600 dark:text-neutral-400 hover:bg-neutral-200 dark:hover:bg-neutral-800 hover:text-neutral-900 dark:hover:text-neutral-100'
              }`}
            >
              <span className="shrink-0">{item.icon}</span>
              <span className="flex-1 truncate">{item.label}</span>
              {item.id === 'queue' && counts.queue > 0 && (
                <span className="text-[11px] bg-neutral-200 dark:bg-neutral-700 text-neutral-600 dark:text-neutral-300 px-1.5 rounded-full font-medium">
                  {counts.queue}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Saved filters */}
        <div className="mt-5">
          <p className="px-2 mb-1 text-[10px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">
            Фильтры очереди
          </p>
          <div className="space-y-0.5">
            {savedFilters.map(f => (
              <button
                key={f.id}
                onClick={() => onNavigate('queue')}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-[12px] text-neutral-600 dark:text-neutral-400 hover:bg-neutral-200 dark:hover:bg-neutral-800 hover:text-neutral-900 dark:hover:text-neutral-100 transition-colors text-left cursor-pointer"
              >
                <span className="shrink-0 text-neutral-400">
                  {f.type === 'critical' && (
                    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                      <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.2"/>
                      <path d="M6 3.5v3M6 8v.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                    </svg>
                  )}
                  {f.type === 'my' && (
                    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                      <circle cx="6" cy="4" r="2.2" stroke="currentColor" strokeWidth="1.2"/>
                      <path d="M2.5 10c0-1.8 1.5-3 3.5-3s3.5 1.2 3.5 3" stroke="currentColor" strokeWidth="1.2"/>
                    </svg>
                  )}
                  {f.type === 'sla' && (
                    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                      <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.2"/>
                      <path d="M6 3.5V6l1.8 1.2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                    </svg>
                  )}
                </span>
                <span className="truncate text-[12px]">{f.name}</span>
              </button>
            ))}
          </div>
        </div>

        {/* New tickets alert */}
        {newCount > 0 && (
          <div className="mt-4 mx-0">
            <div className="bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-900 rounded px-3 py-2">
              <p className="text-[11px] text-blue-700 dark:text-blue-300 font-medium">
                {newCount} новых {newCount === 1 ? 'заявка' : 'заявок'} без исполнителя
              </p>
            </div>
          </div>
        )}
      </nav>

      {/* Bottom: operator status + theme + logout */}
      <div className="border-t border-neutral-200 dark:border-neutral-800 p-2 space-y-1">
        {/* Status selector */}
        <div>
          <div className="flex items-center gap-2 px-2 py-1.5 rounded group">
            <div className="relative shrink-0">
              <div className="w-6 h-6 bg-neutral-300 dark:bg-neutral-700 rounded-full flex items-center justify-center text-[10px] font-semibold text-neutral-700 dark:text-neutral-300">
                {getInitials(username)}
              </div>
              <div className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-neutral-100 dark:border-neutral-900 ${statusDot[operatorStatus]}`} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[12px] font-medium text-neutral-800 dark:text-neutral-200 truncate">
                {username || 'Администратор'}
              </p>
              <p className="text-[10px] text-neutral-400 dark:text-neutral-500">
                {statusLabel[operatorStatus]}
              </p>
            </div>
          </div>
          <div className="flex gap-1 mt-1 px-1">
            {(['online', 'away', 'offline'] as const).map(s => (
              <button
                key={s}
                onClick={() => onStatusChange(s)}
                className={`flex-1 text-[10px] py-0.5 rounded transition-colors cursor-pointer ${
                  operatorStatus === s
                    ? 'bg-neutral-200 dark:bg-neutral-700 text-neutral-700 dark:text-neutral-300 font-medium'
                    : 'text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300'
                }`}
              >
                {s === 'online' ? 'Онлайн' : s === 'away' ? 'Отошёл' : 'Оффлайн'}
              </button>
            ))}
          </div>
        </div>

        {/* Theme toggle */}
        <button
          onClick={onToggleTheme}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-[12px] text-neutral-500 dark:text-neutral-400 hover:bg-neutral-200 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
        >
          {theme === 'light' ? (
            <>
              <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                <circle cx="6.5" cy="6.5" r="2.5" stroke="currentColor" strokeWidth="1.3"/>
                <path d="M6.5 1v1.5M6.5 10.5V12M1 6.5h1.5M10.5 6.5H12M2.64 2.64l1.06 1.06M9.3 9.3l1.06 1.06M2.64 10.36l1.06-1.06M9.3 3.7l1.06-1.06" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
              </svg>
              <span>Светлая тема</span>
            </>
          ) : (
            <>
              <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                <path d="M11 7.5A5 5 0 015.5 2a5 5 0 100 9 5 5 0 005.5-3.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
              </svg>
              <span>Тёмная тема</span>
            </>
          )}
        </button>

        {/* Logout */}
        {onLogout && (
          <button
            onClick={onLogout}
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-[12px] text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors cursor-pointer"
          >
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M5 2H2.5A1.5 1.5 0 001 3.5v6A1.5 1.5 0 002.5 11H5M8.5 9.5L11.5 6.5 8.5 3.5M11.5 6.5H4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            <span>Выйти</span>
          </button>
        )}
      </div>
    </aside>
  );
}
