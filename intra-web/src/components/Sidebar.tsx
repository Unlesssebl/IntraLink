import { useState } from 'react';
import type { Page, Ticket } from '../data/mock';

export type SidebarMode = 'full' | 'compact' | 'hidden';

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
  sidebarMode: SidebarMode;
  onSetSidebarMode: (mode: SidebarMode) => void;
  tickets: Ticket[];
  rootServices: RootServiceItem[];
  subservicesByRoot: Record<number, SubServiceItem[]>;
  selectedService: ServiceSelection;
  onSelectService: (sel: ServiceSelection) => void;
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

function getServiceNumber(name: string, index: number): string {
  const match = name.match(/^(\d+)\./);
  if (match) {
    const num = parseInt(match[1], 10);
    return num < 10 ? `0${num}` : `${num}`;
  }
  const fallback = index + 1;
  return fallback < 10 ? `0${fallback}` : `${fallback}`;
}

function formatServiceName(name: string): string {
  return name.replace(/^\d+\.\s*/, '');
}

export default function Sidebar({
  currentPage,
  onNavigate,
  theme,
  onToggleTheme,
  sidebarMode,
  onSetSidebarMode,
  tickets,
  rootServices,
  subservicesByRoot,
  selectedService,
  onSelectService,
  username,
  onLogout,
}: Props) {
  const [expandedRoots, setExpandedRoots] = useState<Set<number>>(new Set());

  const openCount = tickets.filter(t => t.status !== 'resolved').length;

  const toggleExpand = (rootId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedRoots(prev => {
      const next = new Set(prev);
      if (next.has(rootId)) next.delete(rootId);
      else next.add(rootId);
      return next;
    });
  };

  const getRootCount = (rootId: number) => {
    return tickets.filter(
      t => t.status !== 'resolved' && (t.rootServiceId === rootId || t.serviceId === rootId)
    ).length;
  };

  const getSubCount = (subId: number) => {
    return tickets.filter(t => t.status !== 'resolved' && t.serviceId === subId).length;
  };

  if (sidebarMode === 'hidden') return null;

  if (sidebarMode === 'compact') {
    return (
      <aside className="w-[60px] shrink-0 h-full flex flex-col bg-neutral-100 dark:bg-neutral-900 border-r border-neutral-200 dark:border-neutral-800 select-none py-2 items-center justify-between">
        <div className="flex flex-col items-center gap-2 w-full px-2">
          <button
            onClick={() => onSetSidebarMode('full')}
            className="w-10 h-10 rounded-lg bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 flex items-center justify-center shadow-xs cursor-pointer hover:opacity-90 transition-opacity"
            title="Развернуть меню каталога"
          >
            <svg width="18" height="18" viewBox="0 0 14 14" fill="none">
              <path d="M2 3.5h10M2 7h10M2 10.5h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
            </svg>
          </button>

          <div className="w-8 h-px bg-neutral-200 dark:bg-neutral-800 my-1" />

          <button
            onClick={() => {
              onNavigate('queue');
              onSelectService({ rootId: null, serviceId: null, name: null });
            }}
            className={`relative w-10 h-10 rounded-lg flex items-center justify-center transition-colors cursor-pointer ${
              selectedService.rootId === null && selectedService.serviceId === null
                ? 'bg-blue-600 text-white shadow-sm font-bold'
                : 'bg-white dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-200 dark:hover:bg-neutral-700 border border-neutral-200 dark:border-neutral-700'
            }`}
            title={`Все заявки (${openCount} активных)`}
          >
            <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
              <rect x="2" y="3" width="12" height="2" rx="1" fill="currentColor"/>
              <rect x="2" y="7" width="12" height="2" rx="1" fill="currentColor"/>
              <rect x="2" y="11" width="8" height="2" rx="1" fill="currentColor"/>
            </svg>
            {openCount > 0 && (
              <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-[9px] font-sans font-bold flex items-center justify-center">
                {openCount > 99 ? '99+' : openCount}
              </span>
            )}
          </button>
        </div>

        <div className="flex-1 w-full overflow-y-auto px-2 py-2 flex flex-col items-center gap-1.5 scrollbar-none">
          {rootServices.map((root, idx) => {
            const numStr = getServiceNumber(root.name, idx);
            const count = getRootCount(root.id);
            const isSelected = selectedService.rootId === root.id;

            return (
              <button
                key={`compact-root-${root.id}`}
                onClick={() => {
                  onNavigate('queue');
                  onSelectService({ rootId: root.id, serviceId: null, name: root.name });
                }}
                className={`relative w-10 h-10 rounded-lg flex items-center justify-center transition-all cursor-pointer font-mono text-[13px] font-bold ${
                  isSelected
                    ? 'bg-blue-600 text-white ring-2 ring-blue-400 dark:ring-blue-500 shadow-sm'
                    : 'bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 hover:bg-neutral-200/90 dark:hover:bg-neutral-700 border border-neutral-200/80 dark:border-neutral-700/80'
                }`}
                title={`${root.name} (${count} открытых заявок)`}
              >
                <span>{numStr}</span>
                {count > 0 && (
                  <span
                    className={`absolute -top-1 -right-1 min-w-[15px] h-3.5 px-0.5 rounded-full text-[9px] font-sans font-bold flex items-center justify-center ${
                      isSelected
                        ? 'bg-white text-blue-700'
                        : 'bg-blue-600 text-white'
                    }`}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div className="flex flex-col items-center gap-2 pt-2 border-t border-neutral-200 dark:border-neutral-800 w-full px-2">
          <button
            onClick={onToggleTheme}
            className="w-9 h-9 rounded-lg flex items-center justify-center text-neutral-600 dark:text-neutral-400 hover:bg-neutral-200 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
            title={theme === 'light' ? 'Переключить на темную тему' : 'Переключить на светлую тему'}
          >
            {theme === 'light' ? (
              <svg width="15" height="15" viewBox="0 0 13 13" fill="none">
                <circle cx="6.5" cy="6.5" r="2.5" stroke="currentColor" strokeWidth="1.3"/>
                <path d="M6.5 1v1.5M6.5 10.5V12M1 6.5h1.5M10.5 6.5H12" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
              </svg>
            ) : (
              <svg width="15" height="15" viewBox="0 0 13 13" fill="none">
                <path d="M11 7.5A5 5 0 015.5 2a5 5 0 100 9 5 5 0 005.5-3.5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
              </svg>
            )}
          </button>

          <div
            className="w-8 h-8 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 cursor-default"
            title={`${username || 'Беликов Ален'} (Онлайн)`}
          >
            {getInitials(username)}
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-[270px] shrink-0 h-full flex flex-col bg-neutral-100 dark:bg-neutral-900 border-r border-neutral-200 dark:border-neutral-800 select-none">
      <div className="px-3.5 pt-3.5 pb-2.5 flex items-center justify-between border-b border-neutral-200/80 dark:border-neutral-800/80">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 bg-neutral-900 dark:bg-neutral-100 rounded-lg flex items-center justify-center shrink-0">
            <svg width="14" height="14" viewBox="0 0 12 12" fill="none">
              <rect x="1" y="1" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900"/>
              <rect x="7" y="1" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900"/>
              <rect x="1" y="7" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900"/>
              <rect x="7" y="7" width="4" height="4" rx="0.5" fill="currentColor" className="text-neutral-400"/>
            </svg>
          </div>
          <div className="min-w-0">
            <span className="text-[14px] font-bold text-neutral-900 dark:text-neutral-100 tracking-tight block truncate">
              IntraLink Helpdesk
            </span>
            <span className="text-[11px] text-neutral-500 dark:text-neutral-400 block font-sans">
              1-я линия поддержки
            </span>
          </div>
        </div>

        <button
          onClick={() => onSetSidebarMode('compact')}
          className="w-7 h-7 rounded flex items-center justify-center text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-200 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
          title="Компактный режим (01..16)"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M9 3.5l-3.5 3.5 3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
      </div>

      <nav className="flex-1 px-3 overflow-y-auto space-y-3 pt-3">
        <div>
          <button
            onClick={() => {
              onNavigate('queue');
              onSelectService({ rootId: null, serviceId: null, name: null });
            }}
            className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-[13.5px] transition-colors text-left cursor-pointer ${
              selectedService.rootId === null && selectedService.serviceId === null
                ? 'bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-semibold shadow-xs border border-neutral-200/60 dark:border-neutral-700/60'
                : 'text-neutral-700 dark:text-neutral-300 hover:bg-neutral-200/70 dark:hover:bg-neutral-800 hover:text-neutral-900 dark:hover:text-neutral-100'
            }`}
          >
            <div className="flex items-center gap-2.5 min-w-0">
              <svg width="16" height="16" viewBox="0 0 15 15" fill="none" className="shrink-0 text-blue-600 dark:text-blue-400">
                <rect x="1" y="2" width="13" height="2" rx="1" fill="currentColor"/>
                <rect x="1" y="6.5" width="13" height="2" rx="1" fill="currentColor"/>
                <rect x="1" y="11" width="9" height="2" rx="1" fill="currentColor"/>
              </svg>
              <span className="truncate font-semibold">Все заявки очереди</span>
            </div>
            {openCount > 0 && (
              <span className="text-[12px] font-sans font-semibold tabular-nums text-neutral-700 dark:text-neutral-300 bg-neutral-200/80 dark:bg-neutral-800 px-2 py-0.5 rounded-full ml-2">
                {openCount}
              </span>
            )}
          </button>
        </div>

        <div>
          <div className="flex items-center justify-between px-2 mb-1.5">
            <p className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
              Разделы каталога ({rootServices.length})
            </p>
            {selectedService.name && (
              <button
                onClick={() => onSelectService({ rootId: null, serviceId: null, name: null })}
                className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline cursor-pointer font-semibold"
              >
                Сбросить
              </button>
            )}
          </div>

          <div className="space-y-1">
            {rootServices.map((root, idx) => {
              const subservices = subservicesByRoot[root.id] || [];
              const hasSubs = subservices.length > 0;
              const isExpanded = expandedRoots.has(root.id);
              const count = getRootCount(root.id);
              const isSelectedRoot = selectedService.rootId === root.id && selectedService.serviceId === null;
              const numStr = getServiceNumber(root.name, idx);
              const cleanName = formatServiceName(root.name);

              return (
                <div key={`root-${root.id}`} className="space-y-0.5">
                  <div
                    onClick={() => {
                      onNavigate('queue');
                      onSelectService({ rootId: root.id, serviceId: null, name: root.name });
                    }}
                    className={`group flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[13px] transition-colors cursor-pointer ${
                      isSelectedRoot
                        ? 'bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-semibold shadow-xs border border-neutral-200/60 dark:border-neutral-700/60'
                        : 'text-neutral-800 dark:text-neutral-200 hover:bg-neutral-200/70 dark:hover:bg-neutral-800 hover:text-neutral-900 dark:hover:text-neutral-100'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
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
                            <path d="M3 1.5l3.5 3-3.5 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        </button>
                      ) : (
                        <span className="w-4" />
                      )}
                      <span className="font-mono text-[11px] font-bold text-neutral-400 dark:text-neutral-500 shrink-0">
                        {numStr}
                      </span>
                      <span className="truncate font-medium" title={root.name}>{cleanName}</span>
                    </div>

                    {count > 0 ? (
                      <span className={`ml-1.5 text-[11px] font-sans font-semibold tabular-nums px-1.5 py-0.2 rounded-md ${
                        isSelectedRoot
                          ? 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200'
                          : 'bg-neutral-200/80 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300'
                      }`}>
                        {count}
                      </span>
                    ) : null}
                  </div>

                  {hasSubs && isExpanded && (
                    <div className="pl-6 space-y-0.5 border-l-2 border-neutral-200 dark:border-neutral-800 ml-4 my-1">
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
                            className={`flex items-center justify-between px-2.5 py-1 rounded-md text-[12px] transition-colors cursor-pointer ${
                              isSelectedSub
                                ? 'bg-blue-50 dark:bg-blue-950/60 text-blue-900 dark:text-blue-200 font-semibold'
                                : 'text-neutral-600 dark:text-neutral-400 hover:bg-neutral-200/60 dark:hover:bg-neutral-800/80 hover:text-neutral-900 dark:hover:text-neutral-200'
                            }`}
                          >
                            <span className="truncate" title={sub.name}>{sub.name}</span>
                            {subCount > 0 && (
                              <span className="ml-1 text-[11px] font-sans font-semibold tabular-nums text-neutral-500 dark:text-neutral-400 bg-neutral-200/60 dark:bg-neutral-800 px-1.5 py-0.2 rounded">
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

      <div className="border-t border-neutral-200 dark:border-neutral-800 p-3 space-y-1.5 bg-neutral-50/50 dark:bg-neutral-950/30">
        <div className="flex items-center gap-2.5 px-2 py-1 rounded-lg">
          <div className="w-7 h-7 bg-neutral-800 dark:bg-neutral-200 text-white dark:text-neutral-900 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0">
            {getInitials(username)}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[12px] font-semibold text-neutral-900 dark:text-neutral-100 truncate">
              {username || 'Беликов Ален'}
            </p>
            <p className="text-[11px] text-emerald-600 dark:text-emerald-400 font-medium flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              Инженер 1-й линии
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1 pt-1">
          <button
            onClick={onToggleTheme}
            className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md text-[12px] font-medium text-neutral-700 dark:text-neutral-300 hover:bg-neutral-200 dark:hover:bg-neutral-800 transition-colors cursor-pointer border border-neutral-200/80 dark:border-neutral-700/80"
          >
            {theme === 'light' ? (
              <>
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                  <circle cx="6.5" cy="6.5" r="2.5" stroke="currentColor" strokeWidth="1.2"/>
                  <path d="M6.5 1v1.5M6.5 10.5V12M1 6.5h1.5M10.5 6.5H12" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                </svg>
                <span>Светлая</span>
              </>
            ) : (
              <>
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                  <path d="M11 7.5A5 5 0 015.5 2a5 5 0 100 9 5 5 0 005.5-3.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
                </svg>
                <span>Тёмная</span>
              </>
            )}
          </button>

          {onLogout && (
            <button
              onClick={onLogout}
              className="px-3 py-1.5 rounded-md text-[12px] text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors cursor-pointer font-medium border border-red-200 dark:border-red-900/60"
              title="Выйти из системы"
            >
              Выход
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
