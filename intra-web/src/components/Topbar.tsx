import type { Page, Ticket } from '../data/mock';

const pageLabel: Record<Page, string> = {
  queue: 'Очередь заявок',
  automation: 'Центр автоматизации',
  settings: 'Настройки',
};

interface Props {
  currentPage: Page;
  selectedTicket: Ticket | null;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  onOpenCmdPalette: () => void;
  onNewTicket?: () => void;
}

export default function Topbar({ currentPage, selectedTicket, sidebarOpen, onToggleSidebar, onOpenCmdPalette, onNewTicket }: Props) {
  return (
    <header className="h-11 flex items-center gap-3 px-4 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 shrink-0">
      {/* Sidebar toggle */}
      <button
        onClick={onToggleSidebar}
        className="w-6 h-6 flex items-center justify-center rounded text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors"
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M2 3.5h10M2 7h10M2 10.5h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
        </svg>
      </button>

      {/* Breadcrumbs */}
      <nav className="flex items-center gap-1 text-[13px] min-w-0">
        <span className="text-neutral-400 dark:text-neutral-500">IntraLink</span>
        <span className="text-neutral-300 dark:text-neutral-700">/</span>
        <button
          onClick={() => {}}
          className="text-neutral-600 dark:text-neutral-300 hover:text-neutral-900 dark:hover:text-neutral-100 transition-colors font-medium truncate"
        >
          {pageLabel[currentPage]}
        </button>
        {selectedTicket && currentPage === 'queue' && (
          <>
            <span className="text-neutral-300 dark:text-neutral-700">/</span>
            <span className="text-neutral-500 dark:text-neutral-400 font-mono text-[12px] truncate max-w-[200px]">
              {selectedTicket.id}
            </span>
          </>
        )}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        {currentPage === 'queue' && (
          <button
            onClick={onNewTicket}
            className="flex items-center gap-1.5 px-2.5 py-1 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-[12px] font-medium rounded hover:bg-neutral-700 dark:hover:bg-neutral-300 transition-colors"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M6 2v8M2 6h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            Новая заявка
          </button>
        )}
        <button
          onClick={onOpenCmdPalette}
          className="flex items-center gap-1.5 px-2 py-1 rounded border border-neutral-200 dark:border-neutral-800 text-neutral-400 dark:text-neutral-500 hover:text-neutral-600 dark:hover:text-neutral-300 text-[11px] transition-colors"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <circle cx="5" cy="5" r="3.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M8 8l2.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          <span className="font-mono">⌃K</span>
        </button>

        <button className="w-7 h-7 flex items-center justify-center rounded text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors relative">
          <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
            <path d="M7.5 2a4 4 0 00-4 4v2l-1.5 2h11L11.5 8V6a4 4 0 00-4-4zM6 12a1.5 1.5 0 003 0" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
          </svg>
          <span className="absolute top-1 right-1 w-1.5 h-1.5 bg-blue-500 rounded-full" />
        </button>
      </div>
    </header>
  );
}
