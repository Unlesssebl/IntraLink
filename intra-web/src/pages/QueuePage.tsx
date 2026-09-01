import { useState, useCallback } from 'react';
import type { Ticket, Status } from '../data/mock';
import { statusConfig, priorityConfig } from '../data/mock';
import { applyTask, bulkApplyTasks, mapStatusToStatusId } from '../lib/tasks';
import type { ServiceSelection } from '../components/Sidebar';
import TicketInspector from '../components/TicketInspector';

interface Props {
  tickets: Ticket[];
  selectedTicketId: string | null;
  onSelectTicket: (id: string | null) => void;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onRefresh: () => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  selectedService: ServiceSelection;
  onResetService: () => void;
}

type ViewMode = 'table' | 'kanban';
type FilterTab = 'all' | 'new' | 'duplicates' | 'redirects' | 'repair' | 'wifi';

function getSlaClass(deadline: Date) {
  const h = (deadline.getTime() - Date.now()) / 3600000;
  if (h < 0) return 'text-rose-700/90 dark:text-rose-400/90 font-medium';
  if (h < 1) return 'text-amber-700 dark:text-amber-400 font-medium';
  if (h < 3) return 'text-neutral-600 dark:text-neutral-400 font-medium';
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

export default function QueuePage({
  tickets,
  selectedTicketId,
  onSelectTicket,
  onUpdateTicket,
  onRefresh,
  onToast,
  selectedService,
  onResetService,
}: Props) {
  const [view, setView] = useState<ViewMode>('table');
  const [filterTab, setFilterTab] = useState<FilterTab>('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [inlineStatusTicketId, setInlineStatusTicketId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<Status | null>(null);
  const [sortCol, setSortCol] = useState<'sla' | 'created' | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [processingBulk, setProcessingBulk] = useState(false);

  const selectedTicket = tickets.find(t => t.id === selectedTicketId) ?? null;

  // Filter by service first to get scope-specific counts
  const scopedTickets = tickets.filter(t => {
    if (selectedService.serviceId !== null) {
      return t.serviceId === selectedService.serviceId;
    }
    if (selectedService.rootId !== null) {
      return t.rootServiceId === selectedService.rootId || t.serviceId === selectedService.rootId;
    }
    return true;
  });

  // Counts for KPI within current scope
  const countTotal = scopedTickets.length;
  const countNew = scopedTickets.filter(t => t.status === 'new').length;
  const countDuplicates = scopedTickets.filter(t => t.isDuplicate || t.ruleType === 'duplicate_task').length;
  const countRedirects = scopedTickets.filter(t => t.isRedirect || t.ruleType?.startsWith('redirect')).length;
  const countRepair = scopedTickets.filter(t => t.ruleType === 'hardware_repair').length;
  const countWifi = scopedTickets.filter(t => t.ruleType === 'wlan_access' || t.templateKey === 'wifi_access').length;

  const filtered = scopedTickets.filter(t => {
    if (filterTab === 'new' && t.status !== 'new') return false;
    if (filterTab === 'duplicates' && !t.isDuplicate && t.ruleType !== 'duplicate_task') return false;
    if (filterTab === 'redirects' && !t.isRedirect && !t.ruleType?.startsWith('redirect')) return false;
    if (filterTab === 'repair' && t.ruleType !== 'hardware_repair') return false;
    if (filterTab === 'wifi' && t.ruleType !== 'wlan_access' && t.templateKey !== 'wifi_access') return false;

    if (search) {
      const q = search.toLowerCase().trim();
      const matchId = t.id.toLowerCase().includes(q) || String(t.rawId).includes(q);
      const matchTitle = t.title.toLowerCase().includes(q);
      const matchReq = t.requesterName.toLowerCase().includes(q);
      const matchHost = t.host.toLowerCase().includes(q);
      const matchService = (t.serviceName || '').toLowerCase().includes(q);
      if (!matchId && !matchTitle && !matchReq && !matchHost && !matchService) return false;
    }
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    if (!sortCol) return 0;
    let va: number, vb: number;
    if (sortCol === 'sla') {
      va = a.slaDeadline.getTime();
      vb = b.slaDeadline.getTime();
    } else {
      va = a.createdAt.getTime();
      vb = b.createdAt.getTime();
    }
    return sortDir === 'asc' ? va - vb : vb - va;
  });

  const toggleSort = (col: typeof sortCol) => {
    if (sortCol === col) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortCol(col);
      setSortDir('asc');
    }
  };

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Real Single Inline Actions
  const handleInlineTake = async (t: Ticket) => {
    try {
      await applyTask(t.rawId, {
        status_id: 27,
        comment: 'Взято в работу инженером 1-й линии',
        minutes: 5,
        executor_ids: '8664,10502',
      });
      onUpdateTicket(t.id, { status: 'in_progress', statusId: 27, statusName: 'В работе' });
      onToast({ type: 'success', message: `Заявка #${t.rawId} взята в работу` });
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка: ${err.message || err}` });
    }
  };

  const handleInlineStatusChange = async (t: Ticket, s: Status) => {
    const statusId = mapStatusToStatusId(s);
    try {
      await applyTask(t.rawId, {
        status_id: statusId,
        comment: `Статус изменен на "${statusConfig[s].label}"`,
        minutes: 0,
      });
      onUpdateTicket(t.id, { status: s, statusId });
      setInlineStatusTicketId(null);
      onToast({ type: 'success', message: `Заявка #${t.rawId}: статус обновлен на "${statusConfig[s].label}"` });
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка: ${err.message || err}` });
    }
  };

  // Real Bulk Apply Actions
  const handleBulkAssign = async () => {
    if (selected.size === 0) return;
    setProcessingBulk(true);
    const selectedTickets = tickets.filter(t => selected.has(t.id));
    try {
      const payload = selectedTickets.map(t => ({
        task_id: t.rawId,
        status_id: 27,
        comment: 'Пакетно взято в работу специалистом 1-й линии',
        minutes: 5,
        executor_ids: '8664,10502',
      }));
      const res = await bulkApplyTasks(payload);
      selectedTickets.forEach(t => onUpdateTicket(t.id, { status: 'in_progress', statusId: 27, statusName: 'В работе' }));
      onToast({
        type: 'success',
        message: `Успешно взято в работу: ${res.success_count} из ${payload.length} заявок`,
      });
      setSelected(new Set());
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка пакетной обработки: ${err.message || err}` });
    } finally {
      setProcessingBulk(false);
    }
  };

  const handleBulkStatus = async (targetStatusId: number, statusLabelName: string) => {
    if (selected.size === 0) return;
    setProcessingBulk(true);
    const selectedTickets = tickets.filter(t => selected.has(t.id));
    try {
      const payload = selectedTickets.map(t => {
        let comment = `Заявка переведена в статус ${statusLabelName}`;
        if (targetStatusId === 30) {
          if (t.isDuplicate) {
            comment = `Заявка отменена как повторная (дубликат инцидента #${t.duplicateInfo?.master_task_id || ''}). По вопросам звоните 49-87.`;
          } else if (t.isRedirect) {
            comment = `Заявка отменена, т. к. создана не в подходящем разделе. Требуется оставить заявку в разделе: ${t.targetServiceName || 'соответствующий сервис'}.`;
          }
        }
        return {
          task_id: t.rawId,
          status_id: targetStatusId,
          comment,
          minutes: targetStatusId === 30 ? 5 : 10,
        };
      });

      const res = await bulkApplyTasks(payload);
      const newStatus = targetStatusId === 29 || targetStatusId === 30 ? 'resolved' : (targetStatusId === 35 ? 'waiting' : 'in_progress');
      selectedTickets.forEach(t => onUpdateTicket(t.id, { status: newStatus, statusId: targetStatusId }));
      onToast({
        type: 'success',
        message: `Пакетное действие выполнено: ${res.success_count} заявок обновлено`,
      });
      setSelected(new Set());
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка: ${err.message || err}` });
    } finally {
      setProcessingBulk(false);
    }
  };

  const kanbanCols: { status: Status; label: string }[] = [
    { status: 'new', label: 'Новые' },
    { status: 'in_progress', label: 'В работе' },
    { status: 'waiting', label: 'Ожидание' },
    { status: 'resolved', label: 'Решено / Отменено' },
  ];

  const handleKanbanDrop = useCallback(
    async (status: Status, ticketId: string) => {
      const t = tickets.find(x => x.id === ticketId);
      if (!t) return;
      const statusId = mapStatusToStatusId(status);
      onUpdateTicket(ticketId, { status, statusId });
      setDragOver(null);
      try {
        await applyTask(t.rawId, {
          status_id: statusId,
          comment: `Статус изменен в Kanban на "${statusConfig[status].label}"`,
          minutes: 0,
        });
        onToast({ type: 'success', message: `Заявка #${t.rawId} переведена в "${statusConfig[status].label}"` });
      } catch (err: any) {
        onToast({ type: 'error', message: `Ошибка перемещения: ${err.message || err}` });
      }
    },
    [tickets, onUpdateTicket, onToast]
  );

  const SortIcon = ({ col }: { col: typeof sortCol }) => (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      fill="none"
      className={`ml-1 inline ${sortCol === col ? 'opacity-100' : 'opacity-0 group-hover:opacity-50'}`}
    >
      {sortDir === 'asc' || sortCol !== col ? (
        <path d="M5 2l3 4H2l3-4z" fill="currentColor" />
      ) : (
        <path d="M5 8L2 4h6L5 8z" fill="currentColor" />
      )}
    </svg>
  );

  return (
    <div className="h-full flex overflow-hidden">
      {/* Main queue panel */}
      <div className="flex-1 flex flex-col min-w-0 bg-white dark:bg-neutral-950">
        {/* Unified Clean Toolbar */}
        <div className="shrink-0 flex items-center justify-between gap-3 px-4 py-2 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            {/* View Mode Toggle */}
            <div className="flex items-center gap-0.5 bg-neutral-100 dark:bg-neutral-900 p-0.5 rounded border border-neutral-200 dark:border-neutral-800">
              {(['table', 'kanban'] as const).map(v => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer ${
                    view === v
                      ? 'bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 shadow-2xs font-semibold'
                      : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
                  }`}
                >
                  {v === 'table' ? 'Таблица' : 'Канбан'}
                </button>
              ))}
            </div>

            <div className="w-px h-4 bg-neutral-200 dark:bg-neutral-800 mx-0.5" />

            {/* Filter Tabs with subtle counts */}
            <div className="flex items-center gap-0.5 flex-wrap">
              {([
                ['all', 'Все', countTotal],
                ['new', 'Новые', countNew],
                ['duplicates', 'Дубли', countDuplicates],
                ['redirects', 'Редиректы', countRedirects],
                ['repair', 'В ремонт', countRepair],
                ['wifi', 'Wi-Fi', countWifi],
              ] as const).map(([tabKey, label, count]) => {
                const isActive = filterTab === tabKey;
                return (
                  <button
                    key={tabKey}
                    onClick={() => setFilterTab(tabKey)}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[12px] font-medium transition-colors cursor-pointer ${
                      isActive
                        ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-semibold'
                        : 'text-neutral-500 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-900 hover:text-neutral-800 dark:hover:text-neutral-200'
                    }`}
                  >
                    <span>{label}</span>
                    <span
                      className={`text-[10px] tabular-nums font-sans px-1 py-0.2 rounded ${
                        isActive
                          ? 'bg-neutral-200/90 dark:bg-neutral-700 text-neutral-800 dark:text-neutral-200 font-semibold'
                          : 'text-neutral-400 dark:text-neutral-500'
                      }`}
                    >
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Right side: Search & Refresh */}
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded px-2.5 py-1">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="text-neutral-400">
                <circle cx="5.5" cy="5.5" r="3.5" stroke="currentColor" strokeWidth="1.2" />
                <path d="M9 9l2 2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
              </svg>
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Поиск в очереди..."
                className="text-[12px] bg-transparent outline-none text-neutral-800 dark:text-neutral-200 placeholder-neutral-400 w-36 md:w-44"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200 cursor-pointer"
                >
                  <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                    <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                  </svg>
                </button>
              )}
            </div>

            <button
              onClick={onRefresh}
              className="w-7 h-7 flex items-center justify-center rounded text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
              title="Обновить список"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M10 2.5a4.5 4.5 0 11-7.8 4.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                <path d="M10 2.5v2.5H7.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>
        </div>

        {/* Table View */}
        {view === 'table' && (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-[13px] border-collapse">
              <thead>
                <tr className="border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 sticky top-0 z-10">
                  <th className="w-9 px-3.5 py-2.5">
                    <input
                      type="checkbox"
                      checked={selected.size === sorted.length && sorted.length > 0}
                      onChange={e => setSelected(e.target.checked ? new Set(sorted.map(t => t.id)) : new Set())}
                      className="w-3.5 h-3.5 accent-neutral-900 dark:accent-neutral-100 cursor-pointer"
                    />
                  </th>
                  <th className="px-3.5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-neutral-400 dark:text-neutral-500 whitespace-nowrap">
                    Заявка
                  </th>
                  <th className="px-3.5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    Статус
                  </th>
                  <th className="px-3.5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    Категория / Сервис
                  </th>
                  <th className="px-3.5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    ПК / Хост
                  </th>
                  <th className="px-3.5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    Действие
                  </th>
                  <th
                    className="px-3.5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-neutral-400 dark:text-neutral-500 cursor-pointer select-none group whitespace-nowrap"
                    onClick={() => toggleSort('sla')}
                  >
                    SLA<SortIcon col="sla" />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-200/90 dark:divide-neutral-800">
                {sorted.map((ticket, index) => {
                  const isSelected = selected.has(ticket.id);
                  const isActive = selectedTicketId === ticket.id;
                  const isEven = index % 2 === 0;

                  const rowBg = isActive
                    ? 'bg-blue-50/90 dark:bg-blue-950/50 border-l-2 border-l-blue-600 dark:border-l-blue-400'
                    : isSelected
                    ? 'bg-neutral-200/90 dark:bg-neutral-800'
                    : isEven
                    ? 'bg-white dark:bg-neutral-950'
                    : 'bg-neutral-100/90 dark:bg-neutral-900/90';

                  return (
                    <tr
                      key={ticket.id}
                      onClick={() => onSelectTicket(isActive ? null : ticket.id)}
                      className={`cursor-pointer transition-colors outline-none hover:bg-neutral-200/70 dark:hover:bg-neutral-800/80 ${rowBg}`}
                    >
                      {/* Checkbox */}
                      <td className="w-9 px-3.5 py-3" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(ticket.id)}
                          className="w-3.5 h-3.5 accent-neutral-900 dark:accent-neutral-100 cursor-pointer"
                        />
                      </td>

                      {/* Ticket Title & Creator */}
                      <td className="px-3.5 py-3 max-w-[440px]">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-sans font-medium tabular-nums text-[12px] text-neutral-500 shrink-0">
                            #{ticket.rawId}
                          </span>
                          {ticket.isDuplicate && (
                            <span className="px-1.5 py-0.2 border border-amber-200/70 dark:border-amber-800/50 bg-amber-50/80 dark:bg-amber-950/30 text-amber-800 dark:text-amber-300 rounded text-[10px] font-sans font-medium">
                              дубликат
                            </span>
                          )}
                          {ticket.isRedirect && (
                            <span className="px-1.5 py-0.2 border border-neutral-300/80 dark:border-neutral-700 bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 rounded text-[10px] font-sans font-medium">
                              редирект
                            </span>
                          )}
                          {ticket.ruleType === 'hardware_repair' && (
                            <span className="px-1.5 py-0.2 border border-purple-200/70 dark:border-purple-800/50 bg-purple-50/80 dark:bg-purple-950/30 text-purple-800 dark:text-purple-300 rounded text-[10px] font-sans font-medium">
                              в ремонт
                            </span>
                          )}
                          {(ticket.ruleType === 'wlan_access' || ticket.templateKey === 'wifi_access') && (
                            <span className="px-1.5 py-0.2 border border-emerald-200/70 dark:border-emerald-800/50 bg-emerald-50/80 dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300 rounded text-[10px] font-sans font-medium">
                              wi-fi
                            </span>
                          )}
                          {ticket.hasAttachments && (
                            <span className="text-neutral-400 text-[11px]" title="Есть вложения">📎</span>
                          )}
                          <span className="text-neutral-900 dark:text-neutral-100 truncate font-semibold text-[13px] tracking-tight">
                            {ticket.title}
                          </span>
                        </div>
                        <div className="text-[11px] text-neutral-500 dark:text-neutral-400 mt-1 flex items-center gap-2 font-medium">
                          <span>{ticket.requesterName}</span>
                          {ticket.room && <span>каб. {ticket.room}</span>}
                        </div>
                      </td>

                      {/* Status */}
                      <td
                        className="px-3.5 py-3 whitespace-nowrap"
                        onClick={e => {
                          e.stopPropagation();
                          setInlineStatusTicketId(inlineStatusTicketId === ticket.id ? null : ticket.id);
                        }}
                      >
                        <div className="relative">
                          <span
                            className={`text-[11px] px-2 py-0.5 rounded font-medium cursor-pointer ${statusConfig[ticket.status].className}`}
                          >
                            {ticket.statusName || statusConfig[ticket.status].label}
                          </span>
                          {inlineStatusTicketId === ticket.id && (
                            <div className="absolute left-0 top-6 z-20 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded shadow-lg py-1 min-w-[130px]">
                              {(['new', 'in_progress', 'waiting', 'resolved'] as Status[]).map(s => (
                                <button
                                  key={s}
                                  onClick={e => {
                                    e.stopPropagation();
                                    handleInlineStatusChange(ticket, s);
                                  }}
                                  className="w-full px-3 py-1.5 text-left text-[11px] font-medium hover:bg-neutral-50 dark:hover:bg-neutral-800 cursor-pointer"
                                >
                                  {statusConfig[s].label}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>

                      {/* Service / Category */}
                      <td className="px-3.5 py-3 whitespace-nowrap">
                        <span className="text-[12px] text-neutral-700 dark:text-neutral-300 font-medium truncate max-w-[200px] block" title={ticket.servicePath || ticket.serviceName}>
                          {ticket.serviceName}
                        </span>
                      </td>

                      {/* Host */}
                      <td className="px-3.5 py-3 whitespace-nowrap">
                        {ticket.host ? (
                          <span className="font-sans font-medium tabular-nums text-[11px] bg-neutral-200/80 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 px-2 py-0.5 rounded">
                            {ticket.host}
                          </span>
                        ) : (
                          <span className="text-neutral-300 dark:text-neutral-700 text-[11px]">—</span>
                        )}
                      </td>

                      {/* Quick Take Action */}
                      <td className="px-3.5 py-3 whitespace-nowrap" onClick={e => e.stopPropagation()}>
                        {ticket.statusId === 27 ? (
                          <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-semibold">
                            В работе
                          </span>
                        ) : (
                          <button
                            onClick={() => handleInlineTake(ticket)}
                            className="px-2.5 py-1 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-700 text-neutral-800 dark:text-neutral-200 border border-neutral-300/80 dark:border-neutral-700 rounded text-[11px] font-medium transition-colors cursor-pointer"
                          >
                            + Взять
                          </button>
                        )}
                      </td>

                      {/* SLA */}
                      <td className="px-3.5 py-3 whitespace-nowrap">
                        <span className={`font-sans tabular-nums text-[11px] ${getSlaClass(ticket.slaDeadline)}`}>
                          {formatSla(ticket.slaDeadline)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Empty State */}
            {sorted.length === 0 && (
              <div className="flex flex-col items-center justify-center py-20 text-neutral-400 dark:text-neutral-600">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" className="mb-3 opacity-40">
                  <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                <p className="text-sm font-medium text-neutral-700 dark:text-neutral-300">
                  {scopedTickets.length === 0
                    ? (selectedService.name ? `В разделе «${selectedService.name}» нет заявок` : 'Очередь 1-й линии пуста')
                    : 'Нет заявок по данному фильтру'}
                </p>
                <p className="text-xs text-neutral-400 mt-1">
                  {scopedTickets.length === 0 && selectedService.name
                    ? 'Выберите другой сервис в сайдбаре или нажмите «Сброс»'
                    : (tickets.length === 0 ? 'Все заявки в фильтре 984 успешно обработаны' : 'Попробуйте изменить параметры поиска или сбросить фильтр')}
                </p>
              </div>
            )}
          </div>
        )}

        {/* Kanban View */}
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
                    onDragOver={e => {
                      e.preventDefault();
                      setDragOver(col.status);
                    }}
                    onDragLeave={() => setDragOver(null)}
                    onDrop={e => {
                      const id = e.dataTransfer.getData('ticketId');
                      if (id) handleKanbanDrop(col.status, id);
                    }}
                  >
                    <div className="flex items-center gap-2 px-3 py-2.5 border-b border-neutral-200 dark:border-neutral-800 shrink-0">
                      <span className={`w-2 h-2 rounded-full ${
                        col.status === 'new' ? 'bg-blue-500' :
                        col.status === 'in_progress' ? 'bg-amber-500' :
                        col.status === 'waiting' ? 'bg-purple-500' : 'bg-emerald-500'
                      }`} />
                      <span className="text-[12px] font-semibold text-neutral-800 dark:text-neutral-200">{col.label}</span>
                      <span className="ml-auto text-[11px] bg-neutral-200 dark:bg-neutral-700 text-neutral-600 dark:text-neutral-300 px-1.5 rounded-full font-medium">
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
                          className={`bg-white dark:bg-neutral-800 rounded border p-3 cursor-pointer transition-colors shadow-sm ${
                            selectedTicketId === t.id
                              ? 'border-blue-500 dark:border-blue-400 ring-1 ring-blue-500'
                              : 'border-neutral-200 dark:border-neutral-700 hover:border-neutral-300 dark:hover:border-neutral-600'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2 mb-1.5">
                            <span className="font-sans font-medium tabular-nums text-[11px] text-neutral-500">#{t.rawId}</span>
                            <span className={`text-[11px] font-medium flex items-center gap-1 ${priorityConfig[t.priority].textClass}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${priorityConfig[t.priority].dotClass}`} />
                              {priorityConfig[t.priority].label}
                            </span>
                          </div>
                          <p className="text-[12px] font-medium text-neutral-900 dark:text-neutral-100 leading-snug mb-2">
                            {t.title}
                          </p>
                          <div className="flex items-center justify-between text-[11px] text-neutral-400">
                            <span className="truncate max-w-[130px]">{t.requesterName}</span>
                            <span className={`font-sans font-medium tabular-nums ${getSlaClass(t.slaDeadline)}`}>
                              {formatSla(t.slaDeadline)}
                            </span>
                          </div>
                        </div>
                      ))}

                      {colTickets.length === 0 && (
                        <div className="flex items-center justify-center h-16 text-[12px] text-neutral-400 italic">
                          Пусто
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

      {/* Ticket Inspector Slide-over */}
      {selectedTicket && (
        <TicketInspector
          ticket={selectedTicket}
          onClose={() => onSelectTicket(null)}
          onUpdateTicket={onUpdateTicket}
          onToast={onToast}
        />
      )}

      {/* Real Bulk Action Bar */}
      {selected.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 px-4 py-2.5 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded-lg shadow-2xl border border-neutral-700 dark:border-neutral-300">
          <span className="text-[12px] font-semibold mr-1">
            Выбрано: {selected.size}
          </span>
          <div className="w-px h-4 bg-neutral-700 dark:bg-neutral-300" />
          <button
            onClick={handleBulkAssign}
            disabled={processingBulk}
            className="text-[12px] font-medium hover:text-blue-300 dark:hover:text-blue-700 transition-colors px-1 cursor-pointer disabled:opacity-50"
          >
            Взять себе (27)
          </button>
          <button
            onClick={() => handleBulkStatus(30, 'Отменена (Редирект/Дубликат)')}
            disabled={processingBulk}
            className="text-[12px] font-medium hover:text-amber-300 dark:hover:text-amber-700 transition-colors px-1 cursor-pointer disabled:opacity-50"
          >
            Отменить (30)
          </button>
          <button
            onClick={() => handleBulkStatus(29, 'Выполнена')}
            disabled={processingBulk}
            className="text-[12px] font-medium hover:text-emerald-300 dark:hover:text-emerald-700 transition-colors px-1 cursor-pointer disabled:opacity-50"
          >
            Выполнить (29)
          </button>
          <div className="w-px h-4 bg-neutral-700 dark:bg-neutral-300" />
          <button
            onClick={() => setSelected(new Set())}
            className="text-neutral-400 hover:text-white dark:hover:text-neutral-900 transition-colors cursor-pointer"
            title="Снять выделение"
          >
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M2 2l9 9M11 2l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}
