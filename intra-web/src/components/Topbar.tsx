import type { Page, Ticket } from '../data/mock';
import type { ServiceSelection } from './Sidebar';

interface Props {
  currentPage: Page;
  selectedTicket: Ticket | null;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  onOpenCmdPalette: () => void;
  onRefresh?: () => void;
  selectedService: ServiceSelection;
  onResetService: () => void;
}

export default function Topbar({
  selectedTicket,
  sidebarOpen,
  onToggleSidebar,
  onOpenCmdPalette,
  onRefresh,
  selectedService,
  onResetService,
}: Props) {
  return (
    <header className="h-11 flex items-center gap-3 px-4 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 shrink-0">
      {/* Sidebar toggle */}
      <button
        onClick={onToggleSidebar}
        className="w-6 h-6 flex items-center justify-center rounded text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
        title={sidebarOpen ? 'Скрыть панель' : 'Показать панель'}
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M2 3.5h10M2 7h10M2 10.5h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
        </svg>
      </button>

      {/* Breadcrumbs */}
      <nav className="flex items-center gap-1.5 text-[13px] min-w-0">
        <span className="text-neutral-400 dark:text-neutral-500 font-medium">IntraLink</span>
        <span className="text-neutral-300 dark:text-neutral-700">/</span>
        
        {selectedService.name ? (
          <div className="flex items-center gap-1.5 bg-blue-50 dark:bg-blue-950/60 border border-blue-200 dark:border-blue-800 text-blue-900 dark:text-blue-200 px-2 py-0.5 rounded text-[12px] font-medium">
            <span className="truncate max-w-[200px]">{selectedService.name}</span>
            <button
              onClick={onResetService}
              className="text-blue-500 hover:text-blue-800 dark:hover:text-blue-100 cursor-pointer"
              title="Сбросить фильтр сервиса"
            >
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
              </svg>
            </button>
          </div>
        ) : (
          <span className="text-neutral-800 dark:text-neutral-200 font-semibold truncate">
            Все сервисы очереди
          </span>
        )}

        {selectedTicket && (
          <>
            <span className="text-neutral-300 dark:text-neutral-700">/</span>
            <span className="text-neutral-500 dark:text-neutral-400 font-mono text-[12px] truncate max-w-[220px]">
              #{selectedTicket.rawId} · {selectedTicket.title}
            </span>
          </>
        )}
      </nav>

      {/* Right Controls */}
      <div className="ml-auto flex items-center gap-2">
        {onRefresh && (
          <button
            onClick={onRefresh}
            className="w-7 h-7 flex items-center justify-center rounded text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
            title="Обновить очередь"
          >
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M11 2.5a5.5 5.5 0 11-9.5 5.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
              <path d="M11 2.5v3H8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        )}

        <button
          onClick={onOpenCmdPalette}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded border border-neutral-200 dark:border-neutral-800 text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300 text-[11px] transition-colors cursor-pointer"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <circle cx="5" cy="5" r="3.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M8 8l2.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          <span className="font-mono">Ctrl+K</span>
        </button>
      </div>
    </header>
  );
}
