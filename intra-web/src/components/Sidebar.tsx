import { useState } from 'react';
import type { Page, Ticket } from '../data/mock';
import tempoLogo from '../assets/tempo_logo.png';

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
  onNavigate,
  theme,
  onToggleTheme,
  sidebarMode,
  tickets,
  rootServices,
  subservicesByRoot,
  selectedService,
  onSelectService,
  username,
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

  if (sidebarMode === 'hidden') return null;  // COMPACT MODE: Minimalist Linear-style 01..16 icon buttons
  if (sidebarMode === 'compact') {
    return (
      <aside className="w-[56px] shrink-0 h-full flex flex-col bg-[#dae2eb] dark:bg-neutral-900 border-r border-[#c5d0dc] dark:border-neutral-800 select-none py-3 items-center justify-between">
        <div className="flex flex-col items-center gap-1.5 w-full px-1.5">
          <button
            onClick={() => {
              onNavigate('queue');
              onSelectService({ rootId: null, serviceId: null, name: null });
            }}
            className={`relative w-9 h-9 rounded-lg flex items-center justify-center transition-colors cursor-pointer ${selectedService.rootId === null && selectedService.serviceId === null
                ? 'bg-blue-600 text-white font-semibold'
                : 'bg-black/5 dark:bg-white/5 text-neutral-800 dark:text-neutral-200 hover:bg-black/10 dark:hover:bg-white/10'
              }`}
            title={`Все заявки (${openCount} активных)`}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <rect x="2" y="3" width="12" height="2" rx="1" fill="currentColor" />
              <rect x="2" y="7" width="12" height="2" rx="1" fill="currentColor" />
              <rect x="2" y="11" width="8" height="2" rx="1" fill="currentColor" />
            </svg>
            {openCount > 0 && (
              <span className="absolute -top-1 -right-1 min-w-[16px] h-3.5 px-1 rounded-full bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-[8.5px] font-sans font-bold flex items-center justify-center">
                {openCount > 99 ? '99+' : openCount}
              </span>
            )}
          </button>

          <div className="w-6 h-px bg-black/10 dark:bg-white/10 my-1" />

          {/* Quick numbers 01..16 for services */}
          <div className="flex-1 w-full overflow-y-auto px-0.5 flex flex-col items-center gap-1 scrollbar-none">
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
                  className={`relative w-9 h-9 rounded-lg flex items-center justify-center transition-all cursor-pointer font-sans text-[12px] font-medium ${isSelected
                      ? 'bg-blue-600 text-white font-semibold'
                      : 'bg-black/5 dark:bg-white/5 text-neutral-800 dark:text-neutral-200 hover:bg-black/10 dark:hover:bg-white/10'
                    }`}
                  title={`${root.name} (${count} открытых заявок)`}
                >
                  <span>{numStr}</span>
                  {count > 0 && (
                    <span
                      className={`absolute -top-1 -right-1 min-w-[14px] h-3.5 px-0.5 rounded-full text-[8.5px] font-sans font-bold flex items-center justify-center ${isSelected
                          ? 'bg-white text-blue-700'
                          : 'bg-neutral-400 dark:bg-neutral-700 text-white'
                        }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex flex-col items-center gap-2 pt-2 border-t border-black/10 dark:border-neutral-800 w-full px-1.5">
          <button
            onClick={onToggleTheme}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-neutral-600 dark:text-neutral-400 hover:bg-black/10 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
            title={theme === 'light' ? 'Переключить на темную тему' : 'Переключить на светлую тему'}
          >
            {theme === 'light' ? (
              <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
                <circle cx="6.5" cy="6.5" r="2.5" stroke="currentColor" strokeWidth="1.3" />
                <path d="M6.5 1v1.5M6.5 10.5V12M1 6.5h1.5M10.5 6.5H12" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
                <path d="M11 7.5A5 5 0 015.5 2a5 5 0 100 9 5 5 0 005.5-3.5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
              </svg>
            )}
          </button>

          <div
            className="w-7 h-7 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded-full flex items-center justify-center text-[10.5px] font-bold shrink-0 cursor-default"
            title={`${username || 'Беликов Ален'} (Онлайн)`}
          >
            {getInitials(username)}
          </div>
        </div>
      </aside>
    );
  }

  // FULL MODE: Darker sleek background without borders on items, Tempo logo strictly following services
  return (
    <aside className="w-[260px] shrink-0 h-full flex flex-col bg-[#dae2eb] dark:bg-neutral-900 border-r border-[#c5d0dc] dark:border-neutral-800 select-none">
      <div className="px-3.5 pt-3.5 pb-2.5 flex items-center justify-between border-b border-[#c5d0dc] dark:border-neutral-800">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-6 h-6 bg-neutral-900 dark:bg-neutral-100 rounded-md flex items-center justify-center shrink-0">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <rect x="1" y="1" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900" />
              <rect x="7" y="1" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900" />
              <rect x="1" y="7" width="4" height="4" rx="0.5" fill="currentColor" className="text-white dark:text-neutral-900" />
              <rect x="7" y="7" width="4" height="4" rx="0.5" fill="currentColor" className="text-neutral-400" />
            </svg>
          </div>
          <div className="min-w-0">
            <span className="text-[13.5px] font-bold text-neutral-900 dark:text-neutral-100 tracking-tight block truncate">
              IntraLink Helpdesk
            </span>
            <span className="text-[11px] text-neutral-600 dark:text-neutral-400 block font-sans">
              Техническая поддержка
            </span>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-2.5 overflow-y-auto space-y-3 pt-3">
        <div>
          <button
            onClick={() => {
              onNavigate('queue');
              onSelectService({ rootId: null, serviceId: null, name: null });
            }}
            className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-md text-[13px] transition-colors text-left cursor-pointer ${selectedService.rootId === null && selectedService.serviceId === null
                ? 'bg-black/10 dark:bg-white/10 text-neutral-900 dark:text-neutral-100 font-bold'
                : 'text-neutral-700 dark:text-neutral-300 hover:bg-black/5 dark:hover:bg-white/5 hover:text-neutral-900 dark:hover:text-neutral-100'
              }`}
          >
            <div className="flex items-center gap-2 min-w-0">
              <svg width="15" height="15" viewBox="0 0 15 15" fill="none" className="shrink-0 text-neutral-600 dark:text-neutral-400">
                <rect x="1" y="2" width="13" height="2" rx="1" fill="currentColor" />
                <rect x="1" y="6.5" width="13" height="2" rx="1" fill="currentColor" />
                <rect x="1" y="11" width="9" height="2" rx="1" fill="currentColor" />
              </svg>
              <span className="truncate">Все заявки</span>
            </div>
            {openCount > 0 && (
              <span className="text-[12px] font-sans font-normal tabular-nums text-neutral-500 dark:text-neutral-400 ml-2">
                {openCount}
              </span>
            )}
          </button>
        </div>

        <div>
          <div className="flex items-center justify-between px-2 mb-1.5">
            <p className="text-[10.5px] font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
              Сервисы каталога ({rootServices.length})
            </p>
            {selectedService.name && (
              <button
                onClick={() => onSelectService({ rootId: null, serviceId: null, name: null })}
                className="text-[10.5px] text-blue-600 dark:text-blue-400 hover:underline cursor-pointer font-medium"
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
              const cleanName = formatServiceName(root.name);

              return (
                <div key={`root-${root.id}`} className="space-y-0.5">
                  <div
                    onClick={() => {
                      onNavigate('queue');
                      onSelectService({ rootId: root.id, serviceId: null, name: root.name });
                    }}
                    className={`group flex items-center justify-between px-2 py-1.5 rounded-md text-[13px] transition-colors cursor-pointer ${isSelectedRoot
                        ? 'bg-black/10 dark:bg-white/10 text-neutral-900 dark:text-neutral-100 font-bold'
                        : 'text-neutral-700 dark:text-neutral-300 hover:bg-black/5 dark:hover:bg-white/5 hover:text-neutral-900 dark:hover:text-neutral-100'
                      }`}
                  >
                    <div className="flex items-center gap-1.5 min-w-0 flex-1">
                      {hasSubs ? (
                        <button
                          onClick={e => toggleExpand(root.id, e)}
                          className="w-4 h-4 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200 transition-colors"
                        >
                          <svg
                            width="8"
                            height="8"
                            viewBox="0 0 9 9"
                            fill="none"
                            className={`transition-transform duration-150 ${isExpanded ? 'rotate-90' : ''}`}
                          >
                            <path d="M3 1.5l3.5 3-3.5 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </button>
                      ) : (
                        <span className="w-4" />
                      )}
                      <span className="truncate font-normal" title={root.name}>{cleanName}</span>
                    </div>

                    {count > 0 ? (
                      <span className={`ml-1.5 text-[12px] font-sans font-normal tabular-nums ${isSelectedRoot
                          ? 'text-neutral-900 dark:text-neutral-100 font-semibold'
                          : 'text-neutral-500 dark:text-neutral-400 group-hover:text-neutral-800 dark:group-hover:text-neutral-200'
                        }`}>
                        {count}
                      </span>
                    ) : null}
                  </div>

                  {hasSubs && isExpanded && (
                    <div className="ml-5 pl-2 border-l border-black/10 dark:border-neutral-800 space-y-0.5 py-0.5">
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
                            className={`flex items-center justify-between px-2 py-1 rounded text-[12px] transition-colors cursor-pointer ${isSelectedSub
                                ? 'bg-black/10 dark:bg-white/10 text-neutral-900 dark:text-neutral-100 font-bold'
                                : 'text-neutral-600 dark:text-neutral-400 hover:bg-black/5 dark:hover:bg-white/5 hover:text-neutral-900 dark:hover:text-neutral-100'
                              }`}
                          >
                            <span className="truncate font-normal" title={sub.name}>{sub.name}</span>
                            {subCount > 0 ? (
                              <span className={`ml-1 text-[11px] font-sans font-normal tabular-nums ${isSelectedSub
                                  ? 'text-neutral-900 dark:text-neutral-100 font-semibold'
                                  : 'text-neutral-500 dark:text-neutral-400'
                                }`}>
                                {subCount}
                              </span>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Black Tempo Logo placed immediately below the services list in the center */}
          <div className="pt-5 pb-2 px-3 flex items-center justify-center w-full opacity-85 hover:opacity-100 transition-opacity">
            <img
              src={tempoLogo}
              alt="Группа компаний ТЭМПО"
              className="h-10 w-auto max-w-[200px] object-contain brightness-0 dark:invert"
            />
          </div>
        </div>
      </nav>

      <div className="p-3 border-t border-[#c5d0dc] dark:border-neutral-800 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <div
            className="w-7 h-7 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 cursor-default"
            title={`${username || 'Беликов Ален'} (Онлайн)`}
          >
            {getInitials(username)}
          </div>
          <div className="min-w-0">
            <span className="text-[13px] font-semibold text-neutral-900 dark:text-neutral-100 block truncate">
              {username || 'Беликов Ален'}
            </span>
            <span className="text-[11px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1 font-sans">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
              В сети
            </span>
          </div>
        </div>

        <button
          onClick={onToggleTheme}
          className="w-7 h-7 rounded-md flex items-center justify-center text-neutral-600 hover:text-neutral-900 dark:hover:text-neutral-200 hover:bg-black/10 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
          title={theme === 'light' ? 'Переключить на темную тему' : 'Переключить на светлую тему'}
        >
          {theme === 'light' ? (
            <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
              <circle cx="6.5" cy="6.5" r="2.5" stroke="currentColor" strokeWidth="1.3" />
              <path d="M6.5 1v1.5M6.5 10.5V12M1 6.5h1.5M10.5 6.5H12" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
              <path d="M11 7.5A5 5 0 015.5 2a5 5 0 100 9 5 5 0 005.5-3.5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
            </svg>
          )}
        </button>
      </div>
    </aside>
  );
}
