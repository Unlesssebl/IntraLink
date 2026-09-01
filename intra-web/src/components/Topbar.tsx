import type { Page, Ticket } from '../data/mock';
import type { SidebarMode, ServiceSelection } from './Sidebar';

interface Props {
  currentPage: Page;
  selectedTicket: Ticket | null;
  sidebarMode: SidebarMode;
  onCycleSidebarMode: () => void;
  onOpenCmdPalette: () => void;
  onRefresh?: () => void;
  selectedService: ServiceSelection;
  onResetService: () => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
}

export default function Topbar({
  selectedTicket,
  sidebarMode,
  onCycleSidebarMode,
  onOpenCmdPalette,
  onRefresh,
  selectedService,
  onResetService,
  searchQuery,
  onSearchChange,
}: Props) {
  const getSidebarTitle = () => {
    if (sidebarMode === 'full') return 'Компактный вид (01..16)';
    if (sidebarMode === 'compact') return 'Скрыть боковую панель';
    return 'Развернуть боковую панель';
  };

  return (
    <header className="h-13 flex items-center justify-between gap-3 px-4 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 shrink-0">
      {/* Left: Sidebar cycle toggle & Breadcrumbs */}
      <div className="flex items-center gap-3 min-w-0">
        <button
          onClick={onCycleSidebarMode}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer border border-neutral-200/80 dark:border-neutral-800"
          title={getSidebarTitle()}
        >
          {sidebarMode === 'full' && (
            <svg width="16" height="16" viewBox="0 0 14 14" fill="none">
              <path d="M2 3.5h10M2 7h10M2 10.5h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          )}
          {sidebarMode === 'compact' && (
            <svg width="16" height="16" viewBox="0 0 14 14" fill="none">
              <rect x="2" y="2" width="4" height="10" rx="1" fill="currentColor"/>
              <path d="M8 3.5h4M8 7h4M8 10.5h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
            </svg>
          )}
          {sidebarMode === 'hidden' && (
            <svg width="16" height="16" viewBox="0 0 14 14" fill="none">
              <path d="M5 3.5l3.5 3.5-3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </button>

        {/* Breadcrumbs */}
        <nav className="flex items-center gap-2 text-[14px] min-w-0">
          <span className="text-neutral-400 dark:text-neutral-500 font-semibold">IntraLink</span>
          <span className="text-neutral-300 dark:text-neutral-700">/</span>
          
          {selectedService.name ? (
            <div className="flex items-center gap-1.5 bg-blue-50 dark:bg-blue-950/60 border border-blue-200 dark:border-blue-800 text-blue-900 dark:text-blue-200 px-2.5 py-1 rounded-md text-[13px] font-semibold">
              <span className="truncate max-w-[240px]">{selectedService.name}</span>
              <button
                onClick={onResetService}
                className="text-blue-500 hover:text-blue-800 dark:hover:text-blue-100 cursor-pointer ml-1"
                title="Сбросить фильтр сервиса"
              >
                <svg width="12" height="12" viewBox="0 0 11 11" fill="none">
                  <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
                </svg>
              </button>
            </div>
          ) : (
            <span className="text-neutral-900 dark:text-neutral-100 font-bold truncate">
              Все разделы очереди
            </span>
          )}

          {selectedTicket && (
            <>
              <span className="text-neutral-300 dark:text-neutral-700">/</span>
              <span className="text-neutral-500 dark:text-neutral-400 font-mono text-[13px] font-semibold truncate max-w-[260px]">
                #{selectedTicket.rawId} · {selectedTicket.title}
              </span>
            </>
          )}
        </nav>
      </div>

      {/* Center: Unified Single Search Bar (Marks #2) */}
      <div className="flex-1 max-w-md mx-4 hidden sm:block">
        <div className="relative flex items-center w-full">
          <div className="absolute left-3 text-neutral-400 pointer-events-none">
            <svg width="15" height="15" viewBox="0 0 12 12" fill="none">
              <circle cx="5.5" cy="5.5" r="3.5" stroke="currentColor" strokeWidth="1.4" />
              <path d="M9 9l2 2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
          </div>
          <input
            type="text"
            value={searchQuery}
            onChange={e => onSearchChange(e.target.value)}
            placeholder="Поиск по номеру, ПК, теме, заявителю..."
            className="w-full pl-9 pr-20 py-1.5 bg-neutral-100/80 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg text-[13.5px] text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:bg-white dark:focus:bg-neutral-950 transition-all"
          />
          {searchQuery ? (
            <button
              onClick={() => onSearchChange('')}
              className="absolute right-12 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 p-1 cursor-pointer"
              title="Очистить поиск"
            >
              <svg width="13" height="13" viewBox="0 0 11 11" fill="none">
                <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
            </button>
          ) : null}
          <button
            onClick={onOpenCmdPalette}
            className="absolute right-2 px-1.5 py-0.5 rounded bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 text-[11px] font-mono text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200 cursor-pointer shadow-2xs"
            title="Командная палитра"
          >
            ⌘K
          </button>
        </div>
      </div>

      {/* Right Controls */}
      <div className="flex items-center gap-2 shrink-0">
        {onRefresh && (
          <button
            onClick={onRefresh}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer border border-neutral-200/80 dark:border-neutral-800"
            title="Обновить очередь заявок"
          >
            <svg width="15" height="15" viewBox="0 0 13 13" fill="none">
              <path d="M11 2.5a5.5 5.5 0 11-9.5 5.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
              <path d="M11 2.5v3H8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        )}
      </div>
    </header>
  );
}

