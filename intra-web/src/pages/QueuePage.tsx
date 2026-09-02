import { useState, useCallback, useEffect } from 'react';
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
  searchQuery?: string;
}

type ViewMode = 'table' | 'kanban';
type FilterTab = 'all' | 'new' | 'duplicates' | 'redirects' | 'repair' | 'wifi';

interface BulkConfirmModalState {
  open: boolean;
  actionType: 'take' | 'cancel' | 'resolve';
  targetStatusId: number;
  statusLabelName: string;
  count: number;
  hasRepair: boolean;
  ticketIds: number[];
}

function getSlaClass(deadline: Date) {
  const h = (deadline.getTime() - Date.now()) / 3600000;
  if (h < 0) return 'text-rose-700 dark:text-rose-400 font-bold';
  if (h < 1) return 'text-amber-700 dark:text-amber-400 font-bold';
  if (h < 3) return 'text-neutral-700 dark:text-neutral-300 font-semibold';
  return 'text-neutral-500 dark:text-neutral-400';
}

function formatSla(deadline: Date) {
  const ms = deadline.getTime() - Date.now();
  if (ms < 0) return 'Просрочена';
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  if (h > 24) return `${Math.floor(h / 24)}д ${h % 24}ч`;
  if (h > 0) return `${h}ч ${m}м`;
  return `${m}м`;
}

function parseHostList(hostStr?: string): string[] {
  if (!hostStr) return [];
  return hostStr
    .split(/[,;\s/]+/)
    .map(h => h.trim())
    .filter(Boolean);
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
  searchQuery = '',
}: Props) {
  const [view, setView] = useState<ViewMode>('table');
  const [filterTab, setFilterTab] = useState<FilterTab>('all');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [inlineStatusTicketId, setInlineStatusTicketId] = useState<string | null>(null);
  const [openHostTicketId, setOpenHostTicketId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<Status | null>(null);
  const [sortCol, setSortCol] = useState<'sla' | 'created' | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [processingBulk, setProcessingBulk] = useState(false);
  const [bulkModal, setBulkModal] = useState<BulkConfirmModalState | null>(null);

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

  // Adaptive smart tabs: hide tabs that have 0 items in selected service scope (Marks #3)
  const availableTabs: { key: FilterTab; label: string; count: number }[] = [
    { key: 'all', label: 'Все заявки', count: countTotal },
    { key: 'new', label: 'Новые', count: countNew },
  ];

  if (countDuplicates > 0) {
    availableTabs.push({ key: 'duplicates', label: 'Дубликаты', count: countDuplicates });
  }
  if (countRedirects > 0) {
    availableTabs.push({ key: 'redirects', label: 'Редиректы', count: countRedirects });
  }
  if (countRepair > 0) {
    availableTabs.push({ key: 'repair', label: 'В ремонт', count: countRepair });
  }
  if (countWifi > 0) {
    availableTabs.push({ key: 'wifi', label: 'Wi-Fi доступ', count: countWifi });
  }

  // If currently active tab is no longer present in available tabs, fallback to 'all'
  useEffect(() => {
    if (!availableTabs.some(tab => tab.key === filterTab)) {
      setFilterTab('all');
    }
  }, [availableTabs, filterTab]);

  const filtered = scopedTickets.filter(t => {
    if (filterTab === 'new' && t.status !== 'new') return false;
    if (filterTab === 'duplicates' && !t.isDuplicate && t.ruleType !== 'duplicate_task') return false;
    if (filterTab === 'redirects' && !t.isRedirect && !t.ruleType?.startsWith('redirect')) return false;
    if (filterTab === 'repair' && t.ruleType !== 'hardware_repair') return false;
    if (filterTab === 'wifi' && t.ruleType !== 'wlan_access' && t.templateKey !== 'wifi_access') return false;

    // Search with null-guards (Audit E-5)
    if (searchQuery) {
      const q = searchQuery.toLowerCase().trim();
      const matchId = t.id.toLowerCase().includes(q) || String(t.rawId).includes(q);
      const matchTitle = (t.title || '').toLowerCase().includes(q);
      const matchReq = (t.requesterName || '').toLowerCase().includes(q);
      const matchHost = (t.host || '').toLowerCase().includes(q);
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
    // Guard: Do not allow blind closing without comment (Audit E-2)
    if (s === 'resolved') {
      onSelectTicket(t.id);
      setInlineStatusTicketId(null);
      onToast({
        type: 'info',
        message: 'Для финализации заявки введите отчетный комментарий в карточке инспектора.',
      });
      return;
    }

    const statusId = mapStatusToStatusId(s);
    try {
      await applyTask(t.rawId, {
        status_id: statusId,
        comment: `Статус изменен на "${statusConfig[s].label}"`,
        minutes: 0,
      });
      onUpdateTicket(t.id, { status: s, statusId, statusName: statusConfig[s].label });
      setInlineStatusTicketId(null);
      onToast({ type: 'success', message: `Заявка #${t.rawId}: статус обновлен на «${statusConfig[s].label}»` });
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка: ${err.message || err}` });
    }
  };

  // Bulk Actions with Confirm Modal (Audit C-3)
  const initiateBulkAction = (actionType: 'take' | 'cancel' | 'resolve') => {
    if (selected.size === 0) return;
    const selectedTickets = tickets.filter(t => selected.has(t.id));
    const hasRepair = selectedTickets.some(t => t.ruleType === 'hardware_repair');
    const ticketIds = selectedTickets.map(t => t.rawId);

    if (actionType === 'take') {
      setBulkModal({
        open: true,
        actionType: 'take',
        targetStatusId: 27,
        statusLabelName: 'В работе',
        count: selectedTickets.length,
        hasRepair,
        ticketIds,
      });
    } else if (actionType === 'cancel') {
      setBulkModal({
        open: true,
        actionType: 'cancel',
        targetStatusId: 30,
        statusLabelName: 'Отменена',
        count: selectedTickets.length,
        hasRepair,
        ticketIds,
      });
    } else {
      setBulkModal({
        open: true,
        actionType: 'resolve',
        targetStatusId: 29,
        statusLabelName: 'Выполнена',
        count: selectedTickets.length,
        hasRepair,
        ticketIds,
      });
    }
  };

  const executeBulkAction = async () => {
    if (!bulkModal) return;
    setProcessingBulk(true);
    const selectedTickets = tickets.filter(t => selected.has(t.id));
    const { targetStatusId, statusLabelName } = bulkModal;

    try {
      const payload = selectedTickets.map(t => {
        let comment = `Заявка переведена в статус «${statusLabelName}»`;
        if (targetStatusId === 30) {
          if (t.isDuplicate) {
            comment = `Заявка отменена как повторная (дубликат инцидента #${t.duplicateInfo?.master_task_id || ''}). Все работы ведутся в основной заявке. По вопросам звоните 49-87.`;
          } else if (t.isRedirect) {
            comment = `Заявка отменена, т. к. создана не в подходящем разделе. Требуется оставить заявку в разделе: ${t.targetServiceName || 'соответствующий сервис'}. По вопросам звоните 49-87.`;
          }
        } else if (targetStatusId === 29) {
          if (t.ruleType === 'wlan_access' || t.templateKey === 'wifi_access') {
            comment = 'Доступ к беспроводной корпоративной сети WLAN-WORKNET успешно предоставлен.';
          }
        }
        return {
          task_id: t.rawId,
          status_id: targetStatusId,
          comment,
          minutes: targetStatusId === 30 ? 5 : 10,
          executor_ids: '8664,10502',
        };
      });

      const res = await bulkApplyTasks(payload);
      const newStatus = targetStatusId === 29 || targetStatusId === 30 ? 'resolved' : (targetStatusId === 35 ? 'waiting' : 'in_progress');
      selectedTickets.forEach(t => onUpdateTicket(t.id, { status: newStatus, statusId: targetStatusId, statusName: statusLabelName }));

      onToast({
        type: 'success',
        message: `Успешно обработано: ${res.success_count} из ${payload.length} заявок`,
      });
      setSelected(new Set());
      setBulkModal(null);
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка пакетного действия: ${err.message || err}` });
    } finally {
      setProcessingBulk(false);
    }
  };

  const kanbanCols: { status: Status; label: string }[] = [
    { status: 'new', label: 'Новые' },
    { status: 'in_progress', label: 'В работе' },
    { status: 'waiting', label: 'Ожидание' },
    { status: 'resolved', label: 'Выполнены / Отменены' },
  ];

  // Kanban Drag & Drop with state ROLLBACK on failure (Audit C-2)
  const handleKanbanDrop = useCallback(
    async (status: Status, ticketId: string) => {
      const t = tickets.find(x => x.id === ticketId);
      if (!t) return;

      const prevStatus = t.status;
      const prevStatusId = t.statusId;
      const prevStatusName = t.statusName;
      const targetStatusId = mapStatusToStatusId(status);

      // Optimistic update
      onUpdateTicket(ticketId, { status, statusId: targetStatusId, statusName: statusConfig[status].label });
      setDragOver(null);

      try {
        await applyTask(t.rawId, {
          status_id: targetStatusId,
          comment: `Статус изменен в Канбан на «${statusConfig[status].label}»`,
          minutes: 0,
        });
        onToast({ type: 'success', message: `Заявка #${t.rawId} переведена в «${statusConfig[status].label}»` });
      } catch (err: any) {
        // Rollback state!
        onUpdateTicket(ticketId, { status: prevStatus, statusId: prevStatusId, statusName: prevStatusName });
        onToast({
          type: 'error',
          message: `Ошибка перемещения заявки #${t.rawId}: ${err.message || err}. Возврат в «${statusConfig[prevStatus].label}»`,
        });
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
        {/* Unified Clean Toolbar (Without redundant search, with adaptive smart tabs) */}
        <div className="shrink-0 flex items-center justify-between gap-3 px-4 py-2.5 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 flex-wrap">
          <div className="flex items-center gap-3 flex-wrap">
            {/* View Mode Toggle */}
            <div className="flex items-center gap-0.5 bg-neutral-100 dark:bg-neutral-900 p-0.5 rounded-lg border border-neutral-200 dark:border-neutral-800">
              {(['table', 'kanban'] as const).map(v => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`px-3 py-1 rounded-md text-[12.5px] font-semibold transition-colors cursor-pointer ${view === v
                    ? 'bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                    : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
                    }`}
                >
                  {v === 'table' ? 'Таблица' : 'Канбан'}
                </button>
              ))}
            </div>

            <div className="w-px h-5 bg-neutral-200 dark:bg-neutral-800" />

            {/* Adaptive Smart Filter Tabs (Marks #3) */}
            <div className="flex items-center gap-1 flex-wrap">
              {availableTabs.map(tab => {
                const isActive = filterTab === tab.key;
                return (
                  <button
                    key={tab.key}
                    onClick={() => setFilterTab(tab.key)}
                    className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-[13px] font-medium transition-colors cursor-pointer ${isActive
                      ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-bold border border-neutral-200/80 dark:border-neutral-700/80'
                      : 'text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-900 hover:text-neutral-900 dark:hover:text-neutral-100'
                      }`}
                  >
                    <span>{tab.label}</span>
                    <span
                      className={`text-[11px] tabular-nums font-sans px-1.5 py-0.2 rounded-full font-bold ${isActive
                        ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                        : 'bg-neutral-200/80 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400'
                        }`}
                    >
                      {tab.count}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Table View (Matching style and layout from image-2.png) */}
        {view === 'table' && (
          <div className="flex-1 overflow-auto bg-white dark:bg-neutral-950">
            <table className="w-full min-w-[1020px] text-[14px] border-collapse table-auto">
              <thead className="sticky top-0 z-10 bg-white dark:bg-neutral-950 border-b border-neutral-200 dark:border-neutral-800">
                <tr>
                  <th className="w-11 px-3.5 py-3 text-center">
                    <input
                      type="checkbox"
                      checked={selected.size === sorted.length && sorted.length > 0}
                      onChange={e => setSelected(e.target.checked ? new Set(sorted.map(t => t.id)) : new Set())}
                      className="w-4 h-4 accent-blue-600 cursor-pointer rounded"
                    />
                  </th>
                  <th className="px-3.5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    СТАТУС
                  </th>
                  <th className="px-3.5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    ЗАЯВКА
                  </th>
                  <th className="px-3.5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    СЕРВИС
                  </th>
                  <th className="px-3.5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    ХОСТ
                  </th>
                  <th className="px-3.5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    ИСПОЛНИТЕЛЬ
                  </th>
                  <th className="px-3.5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    ДЕЙСТВИЕ
                  </th>
                  <th
                    className="w-24 px-3.5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500 cursor-pointer select-none group whitespace-nowrap"
                    onClick={() => toggleSort('sla')}
                  >
                    SLA<SortIcon col="sla" />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-850">
                {sorted.map((ticket, index) => {
                  const isSelected = selected.has(ticket.id);
                  const isActive = selectedTicketId === ticket.id;
                  const isEven = index % 2 === 0;

                  const rowBg = isActive
                    ? '!bg-blue-100/90 dark:!bg-blue-950/80 border-l-4 border-l-blue-600 dark:border-l-blue-500'
                    : isSelected
                      ? '!bg-neutral-200/90 dark:!bg-neutral-800'
                      : isEven
                        ? 'bg-[#f4f7fb] dark:bg-neutral-900/60'
                        : 'bg-white dark:bg-neutral-950';

                  const hostList = parseHostList(ticket.host);
                  const primaryHost = hostList[0];
                  const otherHosts = hostList.slice(1);
                  const smartTagClass = "px-1.5 py-0.2 border border-neutral-300 dark:border-neutral-700 bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 rounded text-[11px] font-medium";

                  return (
                    <tr
                      key={ticket.id}
                      onClick={() => onSelectTicket(isActive ? null : ticket.id)}
                      className={`cursor-pointer transition-colors outline-none hover:!bg-blue-50/70 dark:hover:!bg-neutral-800/80 h-[58px] border-b border-neutral-100 dark:border-neutral-850 ${rowBg}`}
                    >
                      {/* Checkbox */}
                      <td className="w-11 px-3.5 py-2.5 text-center" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(ticket.id)}
                          className="w-4 h-4 accent-blue-600 cursor-pointer rounded"
                        />
                      </td>

                      {/* Status Button (Moved first after checkbox) */}
                      <td
                        className="px-3.5 py-2.5 whitespace-nowrap"
                        onClick={e => {
                          e.stopPropagation();
                          setInlineStatusTicketId(inlineStatusTicketId === ticket.id ? null : ticket.id);
                        }}
                      >
                        <div className="relative inline-block">
                          <span className="text-[12px] px-2.5 py-0.5 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-neutral-700 dark:text-neutral-300 font-normal cursor-pointer hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors shadow-2xs inline-block">
                            {ticket.statusName || statusConfig[ticket.status].label}
                          </span>
                          {inlineStatusTicketId === ticket.id && (
                            <div className="absolute left-0 top-8 z-20 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg shadow-xl py-1 min-w-[150px]">
                              {(['new', 'in_progress', 'waiting'] as Status[]).map(s => (
                                <button
                                  key={s}
                                  onClick={e => {
                                    e.stopPropagation();
                                    handleInlineStatusChange(ticket, s);
                                  }}
                                  className="w-full px-3 py-1.5 text-left text-[12px] font-medium hover:bg-neutral-100 dark:hover:bg-neutral-800 cursor-pointer"
                                >
                                  {statusConfig[s].label}
                                </button>
                              ))}
                              <div className="border-t border-neutral-200 dark:border-neutral-800 my-1" />
                              <button
                                onClick={e => {
                                  e.stopPropagation();
                                  handleInlineStatusChange(ticket, 'resolved');
                                }}
                                className="w-full px-3 py-1.5 text-left text-[12px] font-medium text-blue-600 dark:text-blue-400 hover:bg-neutral-100 dark:hover:bg-neutral-800 cursor-pointer"
                              >
                                Выполнить / Отменить →
                              </button>
                            </div>
                          )}
                        </div>
                      </td>

                      {/* Ticket Title (Top) & Requester Info (Bottom), Tags next to Description */}
                      <td className="px-3.5 py-2.5 min-w-[280px]">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-neutral-900 dark:text-neutral-100 font-bold text-[13.5px] truncate max-w-md">
                            {ticket.title}
                          </span>

                          {/* Smart tag badges placed right beside title/description */}
                          {ticket.isDuplicate && (
                            <span className={smartTagClass}>
                              дубликат
                            </span>
                          )}
                          {ticket.isRedirect && (
                            <span className={smartTagClass}>
                              редирект
                            </span>
                          )}
                          {ticket.ruleType === 'hardware_repair' && (
                            <span className={smartTagClass}>
                              в ремонт
                            </span>
                          )}
                          {(ticket.ruleType === 'wlan_access' || ticket.templateKey === 'wifi_access') && (
                            <span className={smartTagClass}>
                              wi-fi
                            </span>
                          )}
                          {ticket.hasAttachments && (
                            <span className="text-neutral-400 text-[12px]" title="Есть вложения">📎</span>
                          )}
                        </div>

                        <div className="text-[12px] text-neutral-500 dark:text-neutral-400 mt-0.5 flex items-center gap-1.5 font-normal">
                          <a
                            href={`/admin/api/tasks/${ticket.rawId}/open`}
                            target="_blank"
                            rel="noreferrer"
                            onClick={e => e.stopPropagation()}
                            className="font-mono tabular-nums text-neutral-500 hover:text-blue-600 dark:hover:text-blue-400 hover:underline shrink-0 font-medium"
                            title="Открыть заявку в IntraService"
                          >
                            #{ticket.rawId}
                          </a>
                          <span>·</span>
                          <span>{ticket.requesterName}</span>
                          {ticket.room && <span>· каб. {ticket.room}</span>}
                          {ticket.department && <span className="truncate max-w-[200px]">· {ticket.department}</span>}
                        </div>
                      </td>

                      {/* Service / Category */}
                      <td className="px-3.5 py-2.5 whitespace-nowrap">
                        <span className="text-[13px] text-neutral-800 dark:text-neutral-200 font-normal truncate block max-w-[220px]" title={ticket.servicePath || ticket.serviceName}>
                          {ticket.serviceName}
                        </span>
                      </td>

                      {/* Host (Clean Badge style from image-2.png) */}
                      <td className="px-3.5 py-2.5 whitespace-nowrap" onClick={e => e.stopPropagation()}>
                        {primaryHost ? (
                          <div className="relative inline-flex items-center gap-1.5">
                            <span
                              onClick={() => {
                                navigator.clipboard.writeText(primaryHost);
                                onToast({ type: 'info', message: `Хост ${primaryHost} скопирован в буфер` });
                              }}
                              className="font-sans font-bold text-[12px] bg-neutral-200/80 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 px-2 py-0.5 rounded cursor-pointer hover:bg-neutral-300 dark:hover:bg-neutral-700 transition-colors tracking-wide"
                              title="Нажмите, чтобы скопировать хост"
                            >
                              {primaryHost}
                            </span>

                            {otherHosts.length > 0 && (
                              <div className="relative">
                                <button
                                  onClick={() => setOpenHostTicketId(openHostTicketId === ticket.id ? null : ticket.id)}
                                  className="px-1.5 py-0.5 bg-blue-100 hover:bg-blue-200 dark:bg-blue-950/80 dark:hover:bg-blue-900 text-blue-800 dark:text-blue-200 border border-blue-300 dark:border-blue-700 rounded text-[11px] font-bold cursor-pointer transition-colors"
                                  title="Показать все хосты"
                                >
                                  +{otherHosts.length} ▾
                                </button>

                                {openHostTicketId === ticket.id && (
                                  <div className="absolute left-0 top-7 z-30 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg shadow-2xl p-2 min-w-[160px] space-y-1 animate-in fade-in zoom-in-95 duration-100">
                                    <span className="text-[10px] uppercase font-bold text-neutral-400 block px-1">
                                      Хосты ({hostList.length})
                                    </span>
                                    {hostList.map((h, i) => (
                                      <div
                                        key={i}
                                        onClick={() => {
                                          navigator.clipboard.writeText(h);
                                          onToast({ type: 'info', message: `Хост ${h} скопирован` });
                                          setOpenHostTicketId(null);
                                        }}
                                        className="flex items-center justify-between px-2 py-1 bg-neutral-50 dark:bg-neutral-800 hover:bg-blue-50 dark:hover:bg-blue-950/50 rounded cursor-pointer transition-colors"
                                      >
                                        <span className="font-sans font-bold text-[12px] text-neutral-800 dark:text-neutral-200">{h}</span>
                                        <span className="text-[10px] text-neutral-400">копировать</span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="text-neutral-300 dark:text-neutral-700 text-[12px]">—</span>
                        )}
                      </td>

                      {/* Executors Column */}
                      <td className="px-3.5 py-2.5 whitespace-nowrap">
                        {ticket.executors ? (
                          <span className="text-[12.5px] text-neutral-800 dark:text-neutral-200 font-medium truncate block max-w-[150px]" title={ticket.executors}>
                            {ticket.executors}
                          </span>
                        ) : (
                          <span className="text-neutral-400 dark:text-neutral-500 text-[12px] font-normal">Не назначен</span>
                        )}
                      </td>

                      {/* Action (В работу) */}
                      <td className="px-3.5 py-2.5 whitespace-nowrap" onClick={e => e.stopPropagation()}>
                        {ticket.statusId === 27 ? (
                          <span className="text-[12px] text-emerald-600 dark:text-emerald-400 font-medium">
                            В работе
                          </span>
                        ) : (
                          <button
                            onClick={() => handleInlineTake(ticket)}
                            className="px-2.5 py-1 bg-neutral-100 hover:bg-neutral-200 dark:bg-neutral-800 dark:hover:bg-neutral-700 text-neutral-800 dark:text-neutral-200 border border-neutral-300/90 dark:border-neutral-700 rounded text-[12px] font-medium transition-colors cursor-pointer"
                          >
                            В работу
                          </button>
                        )}
                      </td>

                      {/* SLA */}
                      <td className="w-24 px-3.5 py-2.5 whitespace-nowrap">
                        <span className={`text-[12px] ${ticket.slaDeadline.getTime() < Date.now() ? 'text-rose-600 dark:text-rose-400 font-normal' : 'text-neutral-600 dark:text-neutral-400 font-normal'}`}>
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
              <div className="flex flex-col items-center justify-center py-24 text-neutral-400 dark:text-neutral-600">
                <svg width="44" height="44" viewBox="0 0 24 24" fill="none" className="mb-3 opacity-40">
                  <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <p className="text-base font-bold text-neutral-700 dark:text-neutral-300">
                  {scopedTickets.length === 0
                    ? (selectedService.name ? `В разделе «${selectedService.name}» нет заявок` : 'Очередь 1-й линии пуста')
                    : 'Нет заявок по данному фильтру'}
                </p>
                <p className="text-xs text-neutral-400 mt-1">
                  {scopedTickets.length === 0 && selectedService.name
                    ? 'Выберите другой сервис в сайдбаре или нажмите «Сбросить»'
                    : (tickets.length === 0 ? 'Все заявки в фильтре 984 успешно обработаны' : 'Попробуйте изменить поисковый запрос или сбросить фильтры')}
                </p>
              </div>
            )}
          </div>
        )}

        {/* Kanban View (With Rollback support, Marks #4, #5) */}
        {view === 'kanban' && (
          <div className="flex-1 overflow-x-auto p-4">
            <div className="flex gap-4 h-full min-w-max">
              {kanbanCols.map(col => {
                const colTickets = sorted.filter(t => t.status === col.status);
                return (
                  <div
                    key={col.status}
                    className={`w-80 shrink-0 flex flex-col rounded-xl border transition-colors ${dragOver === col.status
                      ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-950/20 ring-2 ring-blue-500/20'
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
                    <div className="flex items-center gap-2 px-4 py-3 border-b border-neutral-200 dark:border-neutral-800 shrink-0">
                      <span className={`w-2.5 h-2.5 rounded-full ${col.status === 'new' ? 'bg-blue-500' :
                        col.status === 'in_progress' ? 'bg-amber-500' :
                          col.status === 'waiting' ? 'bg-purple-500' : 'bg-emerald-500'
                        }`} />
                      <span className="text-[13.5px] font-bold text-neutral-800 dark:text-neutral-200">{col.label}</span>
                      <span className="ml-auto text-[12px] bg-neutral-200 dark:bg-neutral-700 text-neutral-700 dark:text-neutral-300 px-2 py-0.5 rounded-full font-bold">
                        {colTickets.length}
                      </span>
                    </div>

                    <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
                      {colTickets.map(t => (
                        <div
                          key={t.id}
                          draggable
                          onDragStart={e => e.dataTransfer.setData('ticketId', t.id)}
                          onClick={() => onSelectTicket(selectedTicketId === t.id ? null : t.id)}
                          className={`bg-white dark:bg-neutral-800 rounded-lg border p-3.5 cursor-pointer transition-all shadow-xs ${selectedTicketId === t.id
                            ? 'border-blue-500 dark:border-blue-400 ring-2 ring-blue-500/30'
                            : 'border-neutral-200 dark:border-neutral-700 hover:border-neutral-300 dark:hover:border-neutral-600'
                            }`}
                        >
                          <div className="flex items-start justify-between gap-2 mb-1.5">
                            <span className="font-mono font-bold text-[12px] text-neutral-500">#{t.rawId}</span>
                            <span className={`text-[11px] font-bold flex items-center gap-1 ${priorityConfig[t.priority].textClass}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${priorityConfig[t.priority].dotClass}`} />
                              {priorityConfig[t.priority].label}
                            </span>
                          </div>
                          <p className="text-[13.5px] font-semibold text-neutral-900 dark:text-neutral-100 leading-snug mb-2">
                            {t.title}
                          </p>
                          <div className="flex items-center justify-between text-[12px] text-neutral-400">
                            <span className="truncate max-w-[150px] font-medium">{t.requesterName}</span>
                            <span className={`font-mono font-bold ${getSlaClass(t.slaDeadline)}`}>
                              {formatSla(t.slaDeadline)}
                            </span>
                          </div>
                        </div>
                      ))}

                      {colTickets.length === 0 && (
                        <div className="flex items-center justify-center h-20 text-[13px] text-neutral-400 italic">
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

      {/* Real Bulk Action Bar (Marks #6, #7: Terminology Helpdesk & Statuses in words) */}
      {selected.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 px-5 py-3 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded-xl shadow-2xl border border-neutral-700 dark:border-neutral-300">
          <span className="text-[13px] font-bold mr-1">
            Выбрано: {selected.size}
          </span>
          <div className="w-px h-5 bg-neutral-700 dark:bg-neutral-300" />
          <button
            onClick={() => initiateBulkAction('take')}
            disabled={processingBulk}
            className="text-[13px] font-semibold hover:text-blue-300 dark:hover:text-blue-700 transition-colors px-2 py-1 cursor-pointer disabled:opacity-50"
          >
            Взять в работу
          </button>
          <button
            onClick={() => initiateBulkAction('cancel')}
            disabled={processingBulk}
            className="text-[13px] font-semibold hover:text-amber-300 dark:hover:text-amber-700 transition-colors px-2 py-1 cursor-pointer disabled:opacity-50"
          >
            Отменить заявки
          </button>
          <button
            onClick={() => initiateBulkAction('resolve')}
            disabled={processingBulk}
            className="text-[13px] font-semibold hover:text-emerald-300 dark:hover:text-emerald-700 transition-colors px-2 py-1 cursor-pointer disabled:opacity-50"
          >
            Выполнить заявки
          </button>
          <div className="w-px h-5 bg-neutral-700 dark:bg-neutral-300" />
          <button
            onClick={() => setSelected(new Set())}
            className="text-neutral-400 hover:text-white dark:hover:text-neutral-900 transition-colors cursor-pointer p-1"
            title="Снять выделение"
          >
            <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
              <path d="M2 2l9 9M11 2l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      )}

      {/* Bulk Confirm Modal (Audit C-3: Verified Execution Safeguard) */}
      {bulkModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-2xs p-4">
          <div className="w-full max-w-md bg-white dark:bg-neutral-900 rounded-xl shadow-2xl border border-neutral-200 dark:border-neutral-800 p-5 space-y-4 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-bold text-neutral-900 dark:text-neutral-100">
                Подтверждение массового действия
              </h3>
              <button
                onClick={() => setBulkModal(null)}
                className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 cursor-pointer"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>

            <p className="text-[13.5px] text-neutral-700 dark:text-neutral-300 leading-relaxed">
              Вы уверены, что хотите перевести <strong>{bulkModal.count}</strong> {bulkModal.count === 1 ? 'заявку' : 'заявок'} в статус{' '}
              <span className="font-bold underline">«{bulkModal.statusLabelName}»</span>?
            </p>

            {/* List of ticket IDs */}
            <div className="bg-neutral-100 dark:bg-neutral-800/80 p-2.5 rounded-lg text-[12px] font-mono text-neutral-700 dark:text-neutral-300 max-h-24 overflow-y-auto">
              {bulkModal.ticketIds.map(id => `#${id}`).join(', ')}
            </div>

            {/* Warning if hardware repair is selected for bulk resolve */}
            {bulkModal.hasRepair && bulkModal.actionType === 'resolve' && (
              <div className="p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 rounded-lg text-[12.5px] text-amber-900 dark:text-amber-200 leading-snug">
                ⚠️ <strong>Внимание (Verified Execution):</strong> в выборке присутствуют заявки на аппаратный ремонт (Каб. 112). Завершайте их только после физической выдачи устройства заявителю!
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setBulkModal(null)}
                disabled={processingBulk}
                className="px-4 py-2 text-[13px] font-medium text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg transition-colors cursor-pointer"
              >
                Отмена
              </button>
              <button
                onClick={executeBulkAction}
                disabled={processingBulk}
                className="px-4 py-2 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-[13px] font-bold rounded-lg hover:bg-neutral-800 dark:hover:bg-neutral-200 transition-colors cursor-pointer disabled:opacity-50"
              >
                {processingBulk ? 'Выполнение...' : 'Подтвердить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
