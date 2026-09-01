import { useState, useCallback } from 'react';
import type { Ticket, Status, Priority } from '../data/mock';
import { statusConfig, priorityConfig, operators, categoryLabel } from '../data/mock';
import TicketInspector from '../components/TicketInspector';

interface Props {
  tickets: Ticket[];
  selectedTicketId: string | null;
  onSelectTicket: (id: string | null) => void;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

type ViewMode = 'table' | 'kanban';
type AiFilter = 'all' | 'ai_ready' | 'needs_action';
type DomainFilter = 'all' | 'duplicates' | 'redirects' | 'wifi' | 'repair';

function getSlaClass(deadline: Date) {
  const h = (deadline.getTime() - Date.now()) / 3600000;
  if (h < 0) return 'text-red-600 dark:text-red-400 font-semibold';
  if (h < 1) return 'text-red-500 dark:text-red-400';
  if (h < 3) return 'text-amber-600 dark:text-amber-400';
  return 'text-neutral-500 dark:text-neutral-400';
}

function formatSla(deadline: Date) {
  const ms = deadline.getTime() - Date.now();
  if (ms < 0) return 'Просрочена';
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  if (h > 24) return `${Math.floor(h / 24)}д`;
  if (h > 0) return `${h}ч ${m}м`;
  return `${m}м`;
}

export default function QueuePage({ tickets, selectedTicketId, onSelectTicket, onUpdateTicket, onToast }: Props) {
  const [view, setView] = useState<ViewMode>('table');
  const [aiFilter, setAiFilter] = useState<AiFilter>('all');
  const [domainFilter, setDomainFilter] = useState<DomainFilter>('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [inlineStatus, setInlineStatus] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<Status | null>(null);
  const [sortCol, setSortCol] = useState<'sla' | 'priority' | 'created' | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const selectedTicket = tickets.find(t => t.id === selectedTicketId) ?? null;

  const filtered = tickets.filter(t => {
    if (domainFilter === 'duplicates' && !t.isDuplicate && t.ruleType !== 'duplicate_task') return false;
    if (domainFilter === 'redirects' && !t.isRedirect && !t.ruleType?.startsWith('redirect')) return false;
    if (domainFilter === 'wifi' && t.ruleType !== 'wlan_access' && t.templateKey !== 'wifi_access') return false;
    if (domainFilter === 'repair' && t.ruleType !== 'hardware_repair') return false;
    if (aiFilter === 'ai_ready' && (t.aiConfidence === null || t.aiConfidence < 80)) return false;
    if (aiFilter === 'needs_action' && t.assigneeId !== null) return false;
    if (search) {
      const q = search.toLowerCase();
      if (!t.id.toLowerCase().includes(q) && !t.title.toLowerCase().includes(q) &&
          !t.requesterName.toLowerCase().includes(q) && !t.host.toLowerCase().includes(q)) return false;
    }
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    if (!sortCol) return 0;
    let va: number, vb: number;
    if (sortCol === 'sla') { va = a.slaDeadline.getTime(); vb = b.slaDeadline.getTime(); }
    else if (sortCol === 'created') { va = a.createdAt.getTime(); vb = b.createdAt.getTime(); }
    else {
      const order = ['critical', 'high', 'medium', 'low'];
      va = order.indexOf(a.priority); vb = order.indexOf(b.priority);
    }
    return sortDir === 'asc' ? va - vb : vb - va;
  });

  const toggleSort = (col: typeof sortCol) => {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortCol(col); setSortDir('asc'); }
  };

  const toggleSelect = (id: string, shift = false) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const batchAssign = () => {
    selected.forEach(id => onUpdateTicket(id, { assigneeId: 'op1' }));
    onToast({ type: 'success', message: `${selected.size} заявок назначено на Иванов А.В.` });
    setSelected(new Set());
  };

  const batchStatus = (s: Status) => {
    selected.forEach(id => onUpdateTicket(id, { status: s }));
    onToast({ type: 'success', message: `Статус изменён для ${selected.size} заявок` });
    setSelected(new Set());
  };

  const kanbanCols: { status: Status; label: string }[] = [
    { status: 'new', label: 'Новые' },
    { status: 'in_progress', label: 'В работе' },
    { status: 'waiting', label: 'Ожидание' },
    { status: 'resolved', label: 'Решено' },
  ];

  const handleDrop = useCallback((status: Status, ticketId: string) => {
    onUpdateTicket(ticketId, { status });
    setDragOver(null);
  }, [onUpdateTicket]);

  const SortIcon = ({ col }: { col: typeof sortCol }) => (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className={`ml-1 inline ${sortCol === col ? 'opacity-100' : 'opacity-0 group-hover:opacity-50'}`}>
      {sortDir === 'asc' || sortCol !== col
        ? <path d="M5 2l3 4H2l3-4z" fill="currentColor"/>
        : <path d="M5 8L2 4h6L5 8z" fill="currentColor"/>
      }
    </svg>
  );

  return (
    <div className="h-full flex overflow-hidden">
      {/* Main queue panel */}
      <div className={`flex flex-col min-w-0 ${selectedTicketId ? 'flex-1' : 'flex-1'}`}>
        {/* Toolbar */}
        <div className="shrink-0 flex items-center gap-2 px-4 py-2.5 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 flex-wrap">
          {/* View toggle */}
          <div className="flex items-center gap-0.5 bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded">
            {(['table', 'kanban'] as const).map(v => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-2.5 py-1 rounded text-[12px] font-medium transition-colors ${
                  view === v ? 'bg-white dark:bg-neutral-700 text-neutral-900 dark:text-neutral-100 shadow-sm' : 'text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300'
                }`}
              >
                {v === 'table' ? 'Таблица' : 'Канбан'}
              </button>
            ))}
          </div>

          <div className="w-px h-4 bg-neutral-200 dark:bg-neutral-800" />

          {/* AI filter */}
          <div className="flex items-center gap-0.5 bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded">
            {([['all', 'Все'], ['ai_ready', 'AI готов'], ['needs_action', 'Требуют решения']] as const).map(([v, l]) => (
              <button
                key={v}
                onClick={() => setAiFilter(v)}
                className={`px-2.5 py-1 rounded text-[12px] font-medium transition-colors ${
                  aiFilter === v ? 'bg-white dark:bg-neutral-700 text-neutral-900 dark:text-neutral-100 shadow-sm' : 'text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300'
                }`}
              >
                {l}
              </button>
            ))}
          </div>

          <div className="w-px h-4 bg-neutral-200 dark:bg-neutral-800" />

          {/* Domain tabs */}
          <div className="flex items-center gap-0.5 bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded">
            {([
              ['all', 'Все темы'],
              ['duplicates', `Дубликаты (${tickets.filter(t => t.isDuplicate || t.ruleType === 'duplicate_task').length})`],
              ['redirects', `Редиректы (${tickets.filter(t => t.isRedirect || t.ruleType?.startsWith('redirect')).length})`],
              ['wifi', `Wi-Fi (${tickets.filter(t => t.ruleType === 'wlan_access' || t.templateKey === 'wifi_access').length})`],
              ['repair', `Каб. 112 (${tickets.filter(t => t.ruleType === 'hardware_repair').length})`],
            ] as const).map(([v, l]) => (
              <button
                key={v}
                onClick={() => setDomainFilter(v)}
                className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
                  domainFilter === v ? 'bg-white dark:bg-neutral-700 text-neutral-900 dark:text-neutral-100 shadow-sm' : 'text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300'
                }`}
              >
                {l}
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="ml-auto flex items-center gap-1.5 bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded px-2 py-1">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="text-neutral-400">
              <circle cx="5.5" cy="5.5" r="3.5" stroke="currentColor" strokeWidth="1.2"/>
              <path d="M9 9l2 2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
            </svg>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="ID, тема, ФИО, хост..."
              className="text-[12px] bg-transparent outline-none text-neutral-700 dark:text-neutral-300 placeholder-neutral-400 w-40"
            />
            {search && (
              <button onClick={() => setSearch('')} className="text-neutral-300 hover:text-neutral-500 dark:text-neutral-600 dark:hover:text-neutral-400">
                <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                  <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
                </svg>
              </button>
            )}
          </div>

          <span className="text-[11px] text-neutral-400 dark:text-neutral-600">
            {sorted.length} из {tickets.length}
          </span>
        </div>

        {/* Table view */}
        {view === 'table' && (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-[13px] border-collapse">
              <thead>
                <tr className="border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950">
                  <th className="w-9 px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.size === sorted.length && sorted.length > 0}
                      onChange={e => setSelected(e.target.checked ? new Set(sorted.map(t => t.id)) : new Set())}
                      className="w-3.5 h-3.5 accent-neutral-700 dark:accent-neutral-300 cursor-pointer"
                    />
                  </th>
                  <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600 whitespace-nowrap">
                    Заявка
                  </th>
                  <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">Статус</th>
                  <th
                    className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600 cursor-pointer select-none group whitespace-nowrap"
                    onClick={() => toggleSort('priority')}
                  >
                    Приоритет<SortIcon col="priority" />
                  </th>
                  <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">AI</th>
                  <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">Категория</th>
                  <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">Исполнитель</th>
                  <th className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">Хост</th>
                  <th
                    className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600 cursor-pointer select-none group whitespace-nowrap"
                    onClick={() => toggleSort('sla')}
                  >
                    SLA<SortIcon col="sla" />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-900">
                {sorted.map(ticket => {
                  const isSelected = selected.has(ticket.id);
                  const isActive = selectedTicketId === ticket.id;
                  const assignee = operators.find(o => o.id === ticket.assigneeId);

                  return (
                    <tr
                      key={ticket.id}
                      onClick={() => onSelectTicket(isActive ? null : ticket.id)}
                      onKeyDown={e => e.key === 'Enter' && onSelectTicket(isActive ? null : ticket.id)}
                      tabIndex={0}
                      className={`cursor-pointer transition-colors outline-none focus:bg-neutral-100/70 dark:focus:bg-neutral-800/40 ${
                        isActive
                          ? 'bg-neutral-100 dark:bg-neutral-800/60'
                          : isSelected
                          ? 'bg-neutral-50 dark:bg-neutral-900'
                          : 'hover:bg-neutral-50 dark:hover:bg-neutral-900/50'
                      }`}
                    >
                      <td className="w-9 px-3 py-2.5" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(ticket.id)}
                          className="w-3.5 h-3.5 accent-neutral-700 dark:accent-neutral-300 cursor-pointer"
                        />
                      </td>
                      <td className="px-3 py-2.5 max-w-[280px]">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-mono text-[11px] text-neutral-400 dark:text-neutral-500 shrink-0">{ticket.id}</span>
                          {ticket.isDuplicate && (
                            <span className="px-1.5 py-0.5 border border-amber-300 dark:border-amber-800/80 bg-amber-50/50 dark:bg-amber-950/30 text-amber-800 dark:text-amber-300 rounded text-[10px] font-mono uppercase tracking-wider">
                              дубликат
                            </span>
                          )}
                          {ticket.isRedirect && (
                            <span className="px-1.5 py-0.5 border border-neutral-300 dark:border-neutral-700 bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 rounded text-[10px] font-mono uppercase tracking-wider">
                              редирект
                            </span>
                          )}
                          {ticket.ruleType === 'wlan_access' && (
                            <span className="px-1.5 py-0.5 border border-neutral-300 dark:border-neutral-700 bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 rounded text-[10px] font-mono uppercase tracking-wider">
                              wi-fi
                            </span>
                          )}
                          {ticket.ruleType === 'hardware_repair' && (
                            <span className="px-1.5 py-0.5 border border-neutral-300 dark:border-neutral-700 bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 rounded text-[10px] font-mono uppercase tracking-wider">
                              каб 112
                            </span>
                          )}
                          <span className="text-neutral-800 dark:text-neutral-200 truncate font-medium">{ticket.title}</span>
                        </div>
                        <div className="text-[11px] text-neutral-400 dark:text-neutral-600 mt-0.5">{ticket.requesterName}</div>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap" onClick={e => { e.stopPropagation(); setInlineStatus(inlineStatus === ticket.id ? null : ticket.id); }}>
                        <div className="relative">
                          <span className={`text-[11px] px-1.5 py-0.5 rounded-sm font-medium cursor-pointer ${statusConfig[ticket.status].className}`}>
                            {statusConfig[ticket.status].label}
                          </span>
                          {inlineStatus === ticket.id && (
                            <div className="absolute left-0 top-6 z-20 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded shadow-lg py-1 min-w-[130px]">
                              {(['new', 'in_progress', 'waiting', 'resolved'] as Status[]).map(s => (
                                <button
                                  key={s}
                                  onClick={e => { e.stopPropagation(); onUpdateTicket(ticket.id, { status: s }); setInlineStatus(null); }}
                                  className={`w-full px-3 py-1.5 text-left text-[11px] font-medium hover:bg-neutral-50 dark:hover:bg-neutral-800 ${statusConfig[s].className}`}
                                >
                                  {statusConfig[s].label}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <span className={`text-[11px] px-1.5 py-0.5 rounded-sm font-medium flex items-center gap-1 w-fit ${priorityConfig[ticket.priority].className}`}>
                          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${priorityConfig[ticket.priority].dotClass}`} />
                          {priorityConfig[ticket.priority].label}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        {ticket.aiConfidence !== null ? (
                          <span className={`font-mono text-[11px] px-1.5 py-0.5 rounded-sm ${
                            ticket.aiConfidence >= 80
                              ? 'bg-green-50 text-green-700 dark:bg-green-950/50 dark:text-green-300'
                              : ticket.aiConfidence >= 60
                              ? 'bg-yellow-50 text-yellow-700 dark:bg-yellow-950/50 dark:text-yellow-300'
                              : 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400'
                          }`}>
                            {ticket.aiConfidence}%
                          </span>
                        ) : (
                          <span className="text-neutral-300 dark:text-neutral-700 text-[11px]">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <span className="text-[11px] text-neutral-500 dark:text-neutral-400">{categoryLabel[ticket.category]}</span>
                      </td>
                      <td className="px-3 py-2.5" onClick={e => e.stopPropagation()}>
                        {assignee ? (
                          <div className="flex items-center gap-1.5">
                            <div className="w-5 h-5 bg-neutral-200 dark:bg-neutral-700 rounded-full flex items-center justify-center text-[9px] font-semibold text-neutral-600 dark:text-neutral-300 shrink-0">
                              {assignee.initials}
                            </div>
                            <span className="text-[11px] text-neutral-600 dark:text-neutral-400 whitespace-nowrap">{assignee.name}</span>
                          </div>
                        ) : (
                          <button
                            onClick={() => { onUpdateTicket(ticket.id, { assigneeId: 'op1' }); onToast({ type: 'success', message: `${ticket.id} взята в работу` }); }}
                            className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap font-medium"
                          >
                            + Взять
                          </button>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="font-mono text-[11px] bg-neutral-100 dark:bg-neutral-800 text-neutral-500 dark:text-neutral-500 px-1.5 py-0.5 rounded whitespace-nowrap">
                          {ticket.host}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <span className={`font-mono text-[12px] ${getSlaClass(ticket.slaDeadline)}`}>
                          {formatSla(ticket.slaDeadline)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {sorted.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-neutral-400 dark:text-neutral-600">
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none" className="mb-3 opacity-40">
                  <rect x="4" y="6" width="24" height="4" rx="2" fill="currentColor"/>
                  <rect x="4" y="14" width="24" height="4" rx="2" fill="currentColor"/>
                  <rect x="4" y="22" width="16" height="4" rx="2" fill="currentColor"/>
                </svg>
                <p className="text-sm">Нет заявок, соответствующих фильтру</p>
              </div>
            )}
          </div>
        )}

        {/* Kanban view */}
        {view === 'kanban' && (
          <div className="flex-1 overflow-x-auto p-4">
            <div className="flex gap-3 h-full min-w-max">
              {kanbanCols.map(col => {
                const colTickets = sorted.filter(t => t.status === col.status);
                return (
                  <div
                    key={col.status}
                    className={`w-72 shrink-0 flex flex-col rounded border transition-colors ${
                      dragOver === col.status
                        ? 'border-blue-400 bg-blue-50/50 dark:bg-blue-950/20'
                        : 'border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-900'
                    }`}
                    onDragOver={e => { e.preventDefault(); setDragOver(col.status); }}
                    onDragLeave={() => setDragOver(null)}
                    onDrop={e => {
                      const id = e.dataTransfer.getData('ticketId');
                      if (id) handleDrop(col.status, id);
                    }}
                  >
                    <div className="flex items-center gap-2 px-3 py-2.5 border-b border-neutral-200 dark:border-neutral-800 shrink-0">
                      <span className={`w-2 h-2 rounded-full ${
                        col.status === 'new' ? 'bg-blue-400' :
                        col.status === 'in_progress' ? 'bg-amber-400' :
                        col.status === 'waiting' ? 'bg-violet-400' : 'bg-green-400'
                      }`} />
                      <span className="text-[12px] font-semibold text-neutral-700 dark:text-neutral-300">{col.label}</span>
                      <span className="ml-auto text-[11px] bg-neutral-200 dark:bg-neutral-700 text-neutral-500 dark:text-neutral-400 px-1.5 rounded-full font-medium">
                        {colTickets.length}
                      </span>
                    </div>
                    <div className="flex-1 overflow-y-auto p-2 space-y-2">
                      {colTickets.map(t => (
                        <div
                          key={t.id}
                          draggable
                          onDragStart={e => e.dataTransfer.setData('ticketId', t.id)}
                          onClick={() => onSelectTicket(selectedTicketId === t.id ? null : t.id)}
                          className={`bg-white dark:bg-neutral-800 rounded border p-3 cursor-pointer transition-colors ${
                            selectedTicketId === t.id
                              ? 'border-blue-400 dark:border-blue-600'
                              : 'border-neutral-200 dark:border-neutral-700 hover:border-neutral-300 dark:hover:border-neutral-600'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2 mb-1.5">
                            <span className="font-mono text-[10px] text-neutral-400">{t.id}</span>
                            <span className={`text-[10px] px-1.5 py-0.5 rounded-sm font-medium flex items-center gap-1 ${priorityConfig[t.priority].className}`}>
                              <span className={`w-1 h-1 rounded-full ${priorityConfig[t.priority].dotClass}`} />
                              {priorityConfig[t.priority].label}
                            </span>
                          </div>
                          <p className="text-[12px] font-medium text-neutral-800 dark:text-neutral-200 leading-snug mb-2">{t.title}</p>
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] text-neutral-400">{t.requesterName}</span>
                            <div className="flex items-center gap-1.5">
                              {t.assigneeId ? (
                                <div className="w-5 h-5 bg-neutral-200 dark:bg-neutral-700 rounded-full flex items-center justify-center text-[9px] font-semibold text-neutral-600 dark:text-neutral-300">
                                  {operators.find(o => o.id === t.assigneeId)?.initials}
                                </div>
                              ) : (
                                <div className="w-5 h-5 border border-dashed border-neutral-300 dark:border-neutral-600 rounded-full" />
                              )}
                              <span className={`font-mono text-[10px] ${getSlaClass(t.slaDeadline)}`}>
                                {formatSla(t.slaDeadline)}
                              </span>
                            </div>
                          </div>
                        </div>
                      ))}
                      {colTickets.length === 0 && (
                        <div className="flex items-center justify-center h-16 text-[12px] text-neutral-300 dark:text-neutral-700">
                          Нет заявок
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Inspector panel */}
      {selectedTicket && (
        <TicketInspector
          ticket={selectedTicket}
          onClose={() => onSelectTicket(null)}
          onUpdateTicket={onUpdateTicket}
          onToast={onToast}
        />
      )}

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 px-4 py-2.5 bg-neutral-900 dark:bg-neutral-100 rounded-lg shadow-xl">
          <span className="text-[12px] text-neutral-300 dark:text-neutral-600 mr-1">
            {selected.size} выбрано
          </span>
          <div className="w-px h-4 bg-neutral-700 dark:bg-neutral-300" />
          <button
            onClick={batchAssign}
            className="text-[12px] font-medium text-white dark:text-neutral-900 hover:text-blue-300 dark:hover:text-blue-700 transition-colors px-1"
          >
            Взять себе
          </button>
          <button
            onClick={() => batchStatus('in_progress')}
            className="text-[12px] font-medium text-white dark:text-neutral-900 hover:text-amber-300 dark:hover:text-amber-700 transition-colors px-1"
          >
            В работу
          </button>
          <button
            onClick={() => batchStatus('resolved')}
            className="text-[12px] font-medium text-white dark:text-neutral-900 hover:text-green-300 dark:hover:text-green-700 transition-colors px-1"
          >
            Решить
          </button>
          <div className="w-px h-4 bg-neutral-700 dark:bg-neutral-300" />
          <button
            onClick={() => setSelected(new Set())}
            className="text-[12px] text-neutral-400 dark:text-neutral-500 hover:text-white dark:hover:text-neutral-900 transition-colors"
          >
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M2 2l9 9M11 2l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}
