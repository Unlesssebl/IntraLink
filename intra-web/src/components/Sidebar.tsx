import { useState } from 'react';
import type { Page, Ticket } from '../data/mock';

export interface ServiceSelection {
  rootId: number | null;
  serviceId: number | null;
  name: string | null;
}

interface RootServiceItem {
  id: number;
  name: string;
}

interface SubServiceItem {
  id: number;
  name: string;
  parent_id?: number;
}

interface Props {
  currentPage: Page;
  onNavigate: (page: Page) => void;
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
  sidebarOpen: boolean;
  tickets: Ticket[];
  rootServices: RootServiceItem[];
  subservicesByRoot: Record<number, SubServiceItem[]>;
  selectedService: ServiceSelection;
  onSelectService: (sel: ServiceSelection) => void;
  onOpenSearch: () => void;
  username?: string;
  onLogout?: () => void;
}

function getInitials(name?: string): string {
  if (!name) return 'БА';
  const parts = name.trim().split(/[\s._\\/]+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

export default function Sidebar({
  currentPage,
  onNavigate,
  theme,
  onToggleTheme,
  sidebarOpen,
  tickets,
  rootServices,
  subservicesByRoot,
  selectedService,
  onSelectService,
  onOpenSearch,
  username,
  onLogout,
}: Props) {
  const [expandedRoots, setExpandedRoots] = useState<Set<number>>(new Set());

  const openCount = tickets.filter(t => t.status !== 'resolved').length;
  const newCount = tickets.filter(t => t.status === 'new').length;

  const toggleExpand = (rootId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedRoots(prev => {
      const next = new Set(prev);
      if (next.has(rootId)) next.delete(rootId);
      else next.add(rootId);
      return next;
    });
  };

  // Helper to count tickets in root service
  const getRootCount = (rootId: number) => {
    return tickets.filter(
      t => t.status !== 'resolved' && (t.rootServiceId === rootId || t.serviceId === rootId)
    ).length;
  };

  // Helper to count tickets in subservice
  const getSubCount = (subId: number) => {
    return tickets.filter(t => t.status !== 'resolved' && t.serviceId === subId).length;
  };

  if (!sidebarOpen) return null;

  return (
    <aside className="w-[260px] shrink-0 h-full flex flex-col bg-neutral-100 dark:bg-neutral-900 border-r border-neutral-200 dark:border-neutral-800 select-none">
      {/* Workspace Header */}
      <div className="px-3 pt-3.5 pb-2">
        <div className="flex items-center gap-2.5 px-2 py-1.5 rounded">
          <div className="w-6 h-6 bg-neutral-900 dark:bg-neutral-100 rounded flex items-center justify-center shrink-0">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <rect x="1" y="1" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900"/>
              <rect x="7" y="1" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900"/>
              <rect x="1" y="7" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900"/>
              <rect x="7" y="7" width="4" height="4" rx="0.5" fill="currentColor" className="text-neutral-400"/>
            </svg>
          </div>
          <div className="min-w-0">
            <span className="text-[13px] font-semibold text-neutral-900 dark:text-neutral-100 tracking-tight block truncate">
              IntraLink Helpdesk
            </span>
            <span className="text-[10px] text-neutral-500 dark:text-neutral-400 block font-sans">
              1-я линия техподдержки
            </span>
          </div>
        </div>
      </div>

      {/* Quick Search trigger */}
      <div className="px-3 pb-2">
        <button
          onClick={onOpenSearch}
          className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded bg-white dark:bg-neutral-800/80 border border-neutral-200 dark:border-neutral-700/60 text-neutral-500 dark:text-neutral-400 hover:text-neutral-800 dark:hover:text-neutral-200 transition-colors text-xs cursor-pointer shadow-xs"
        >
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <circle cx="5.5" cy="5.5" r="3.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M9 9l2.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          <span>Быстрый поиск</span>
          <span className="ml-auto font-sans text-[10px] bg-neutral-100 dark:bg-neutral-700 text-neutral-500 dark:text-neutral-400 px-1.5 py-0.5 rounded border border-neutral-200/60 dark:border-neutral-600/60">
            Ctrl+K
          </span>
        </button>
      </div>

      {/* Main Navigation & Service Catalog Tree */}
      <nav className="flex-1 px-2.5 overflow-y-auto space-y-3 pt-1">
        {/* All queue tickets */}
        <div>
          <button
            onClick={() => {
              onNavigate('queue');
              onSelectService({ rootId: null, serviceId: null, name: null });
            }}
            className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded text-[12px] transition-colors text-left cursor-pointer ${
              selectedService.rootId === null && selectedService.serviceId === null
                ? 'bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-semibold shadow-xs'
                : 'text-neutral-600 dark:text-neutral-400 hover:bg-neutral-200/70 dark:hover:bg-neutral-800 hover:text-neutral-900 dark:hover:text-neutral-100'
            }`}
          >
            <div className="flex items-center gap-2 min-w-0">
              <svg width="14" height="14" viewBox="0 0 15 15" fill="none" className="shrink-0">
                <rect x="1" y="2" width="13" height="2" rx="1" fill="currentColor"/>
                <rect x="1" y="6.5" width="13" height="2" rx="1" fill="currentColor"/>
                <rect x="1" y="11" width="9" height="2" rx="1" fill="currentColor"/>
              </svg>
              <span className="truncate font-medium">Все заявки</span>
            </div>
            {openCount > 0 && (
              <span className="text-[11px] font-sans font-medium tabular-nums text-neutral-600 dark:text-neutral-400 bg-neutral-200/80 dark:bg-neutral-800 px-1.5 py-0.2 rounded ml-2">
                {openCount}
              </span>
            )}
          </button>
        </div>

        {/* Real Service Catalog Tree */}
        <div>
          <div className="flex items-center justify-between px-2 mb-1.5">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
              Сервисы каталога ({rootServices.length})
            </p>
            {selectedService.name && (
              <button
                onClick={() => onSelectService({ rootId: null, serviceId: null, name: null })}
                className="text-[10px] text-blue-600 dark:text-blue-400 hover:underline cursor-pointer font-medium"
              >
                Сброс
              </button>
            )}
          </div>

          <div className="space-y-0.5">
            {rootServices.map(root => {
              const subservices = subservicesByRoot[root.id] || [];
              const hasSubs = subservices.length > 0;
              const isExpanded = expandedRoots.has(root.id);
              const count = getRootCount(root.id);
              const isSelectedRoot = selectedService.rootId === root.id && selectedService.serviceId === null;

              return (
                <div key={`root-${root.id}`} className="space-y-0.5">
                  {/* Root Service Item */}
                  <div
                    onClick={() => {
                      onNavigate('queue');
                      onSelectService({ rootId: root.id, serviceId: null, name: root.name });
                    }}
                    className={`group flex items-center justify-between px-2 py-1.5 rounded text-[12px] transition-colors cursor-pointer ${
                      isSelectedRoot
                        ? 'bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-semibold shadow-xs'
                        : 'text-neutral-700 dark:text-neutral-300 hover:bg-neutral-200/70 dark:hover:bg-neutral-800 hover:text-neutral-900 dark:hover:text-neutral-100'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 min-w-0 flex-1">
                      {hasSubs ? (
                        <button
                          onClick={e => toggleExpand(root.id, e)}
                          className="w-4 h-4 flex items-center justify-center rounded hover:bg-neutral-300 dark:hover:bg-neutral-700 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors"
                        >
                          <svg
                            width="9"
                            height="9"
                            viewBox="0 0 9 9"
                            fill="none"
                            className={`transition-transform duration-150 ${isExpanded ? 'rotate-90' : ''}`}
                          >
                            <path d="M3 1.5l3.5 3-3.5 3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        </button>
                      ) : (
                        <span className="w-4" />
                      )}
                      <span className="truncate" title={root.name}>{root.name}</span>
                    </div>

                    {count > 0 ? (
                      <span className="ml-1.5 text-[11px] font-sans font-medium tabular-nums text-neutral-600 dark:text-neutral-400 bg-neutral-200/80 dark:bg-neutral-800 px-1.5 py-0.2 rounded">
                        {count}
                      </span>
                    ) : null}
                  </div>

                  {/* Subservices Tree */}
                  {hasSubs && isExpanded && (
                    <div className="pl-5 space-y-0.5 border-l border-neutral-200 dark:border-neutral-800 ml-3 my-0.5">
                      {subservices.map(sub => {
                        const subCount = getSubCount(sub.id);
                        const isSelectedSub = selectedService.serviceId === sub.id;

                        return (
                          <div
                            key={`sub-${sub.id}`}
                            onClick={() => {
                              onNavigate('queue');
                              onSelectService({ rootId: root.id, serviceId: sub.id, name: sub.name });
                            }}
                            className={`flex items-center justify-between px-2 py-1 rounded text-[11px] transition-colors cursor-pointer ${
                              isSelectedSub
                                ? 'bg-blue-50 dark:bg-blue-950/60 text-blue-900 dark:text-blue-200 font-semibold'
                                : 'text-neutral-600 dark:text-neutral-400 hover:bg-neutral-200/60 dark:hover:bg-neutral-800/80 hover:text-neutral-900 dark:hover:text-neutral-200'
                            }`}
                          >
                            <span className="truncate" title={sub.name}>{sub.name}</span>
                            {subCount > 0 && (
                              <span className="ml-1 text-[10px] font-sans font-medium tabular-nums text-neutral-500 dark:text-neutral-400 bg-neutral-200/60 dark:bg-neutral-800 px-1.5 py-0.2 rounded">
                                {subCount}
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </nav>

      {/* Footer Profile & Logout */}
      <div className="border-t border-neutral-200 dark:border-neutral-800 p-2 space-y-1">
        {/* User Card */}
        <div className="flex items-center gap-2 px-2 py-1 rounded">
          <div className="w-6 h-6 bg-neutral-800 dark:bg-neutral-200 text-white dark:text-neutral-900 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0">
            {getInitials(username)}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[11px] font-semibold text-neutral-900 dark:text-neutral-100 truncate">
              {username || 'Беликов Ален'}
            </p>
            <p className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              Онлайн
            </p>
          </div>
        </div>

        {/* Theme toggle */}
        <button
          onClick={onToggleTheme}
          className="w-full flex items-center gap-2 px-2 py-1 rounded text-[11px] text-neutral-600 dark:text-neutral-400 hover:bg-neutral-200 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
        >
          {theme === 'light' ? (
            <>
              <svg width="12" height="12" viewBox="0 0 13 13" fill="none">
                <circle cx="6.5" cy="6.5" r="2.5" stroke="currentColor" strokeWidth="1.2"/>
                <path d="M6.5 1v1.5M6.5 10.5V12M1 6.5h1.5M10.5 6.5H12M2.64 2.64l1.06 1.06M9.3 9.3l1.06 1.06M2.64 10.36l1.06-1.06M9.3 3.7l1.06-1.06" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
              </svg>
              <span>Светлая тема</span>
            </>
          ) : (
            <>
              <svg width="12" height="12" viewBox="0 0 13 13" fill="none">
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
            className="w-full flex items-center gap-2 px-2 py-1 rounded text-[11px] text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors cursor-pointer font-medium"
          >
            <svg width="12" height="12" viewBox="0 0 13 13" fill="none">
              <path d="M5 2H2.5A1.5 1.5 0 001 3.5v6A1.5 1.5 0 002.5 11H5M8.5 9.5L11.5 6.5 8.5 3.5M11.5 6.5H4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            <span>Выйти</span>
          </button>
        )}
      </div>
    </aside>
  );
}
