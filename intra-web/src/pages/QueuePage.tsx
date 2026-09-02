import { useState, useCallback, useEffect } from 'react';
import type { Ticket, Status } from '../data/mock';
import { statusConfig, priorityConfig, getStatusDotClass } from '../data/mock';
import { applyTask, bulkApplyTasks, smartBulkApplyTasks, mapStatusToStatusId } from '../lib/tasks';
import type { SmartBulkApplyItemPayload } from '../lib/types';
import type { ServiceSelection } from '../components/Sidebar';
import TicketInspector from '../components/TicketInspector';
import {
  IconWifi,
  IconDuplicate,
  IconRedirect,
  IconWrench,
  IconPlay,
  IconPaperclip,
  IconSparkles,
  IconPencil,
  IconAlertTriangle,
  IconChevronDown,
  IconChevronRight,
  IconRocket,
} from '../components/Icons';

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

interface SmartBatchItem {
  ticket: Ticket;
  selected: boolean;
  comment: string;
  minutes: number;
  isEditing: boolean;
}

interface SmartBatchModalState {
  open: boolean;
  items: SmartBatchItem[];
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
  const [smartBatchModal, setSmartBatchModal] = useState<SmartBatchModalState | null>(null);

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

  // Smart Plan Direct Action for Single Ticket
  const handleApplyTicketPlan = async (t: Ticket) => {
    const plan = t.aiPlan;
    if (!plan) {
      await handleInlineTake(t);
      return;
    }

    try {
      onToast({ type: 'info', message: `Выполняется: ${plan.actionTitle} (#${t.rawId})...` });
      const payload: SmartBulkApplyItemPayload = {
        task_id: t.rawId,
        status_id: plan.targetStatusId,
        comment: plan.comment,
        minutes: plan.expensesMinutes,
        requires_domain_job: plan.requiresDomainJob,
        domain_job: plan.domainJob,
        executor_ids: '8664,10502',
      };

      const res = await smartBulkApplyTasks([payload]);
      if (res.success_count > 0) {
        const newStatus = plan.targetStatusId === 29 || plan.targetStatusId === 30 ? 'resolved' : (plan.targetStatusId === 35 || plan.targetStatusId === 48 ? 'waiting' : 'in_progress');
        onUpdateTicket(t.id, {
          status: newStatus,
          statusId: plan.targetStatusId,
          statusName: plan.targetStatusName,
        });
        onToast({ type: 'success', message: `Заявка #${t.rawId}: ${plan.actionTitle} успешно выполнено` });
      } else {
        const err = res.errors[0]?.error || 'Ошибка исполнения';
        onToast({ type: 'error', message: `Ошибка #${t.rawId}: ${err}` });
      }
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка: ${err.message || err}` });
    }
  };

  const openSmartBatchModal = (ticketsToProcess: Ticket[]) => {
    if (ticketsToProcess.length === 0) return;
    const items: SmartBatchItem[] = ticketsToProcess.map(t => ({
      ticket: t,
      selected: true,
      comment: t.aiPlan?.comment || t.aiSuggestion || 'Принято в работу специалистом 1-й линии техподдержки.',
      minutes: t.aiPlan?.expensesMinutes || t.expenses || 10,
      isEditing: false,
    }));
    setSmartBatchModal({ open: true, items });
  };

  const executeSmartBatch = async () => {
    if (!smartBatchModal) return;
    const activeItems = smartBatchModal.items.filter(x => x.selected);
    if (activeItems.length === 0) return;

    setProcessingBulk(true);
    try {
      const payload: SmartBulkApplyItemPayload[] = activeItems.map(item => {
        const plan = item.ticket.aiPlan;
        return {
          task_id: item.ticket.rawId,
          status_id: plan?.targetStatusId || 27,
          comment: item.comment,
          minutes: item.minutes,
          requires_domain_job: plan?.requiresDomainJob,
          domain_job: plan?.domainJob,
          executor_ids: '8664,10502',
        };
      });

      const res = await smartBulkApplyTasks(payload);

      activeItems.forEach(item => {
        const plan = item.ticket.aiPlan;
        const targetStatusId = plan?.targetStatusId || 27;
        const newStatus = targetStatusId === 29 || targetStatusId === 30 ? 'resolved' : (targetStatusId === 35 || targetStatusId === 48 ? 'waiting' : 'in_progress');
        onUpdateTicket(item.ticket.id, {
          status: newStatus,
          statusId: targetStatusId,
          statusName: plan?.targetStatusName || 'В работе',
        });
      });

      onToast({
        type: res.failed_count === 0 ? 'success' : 'warning',
        message: `Пакетное выполнение: ${res.success_count} успешно${res.failed_count > 0 ? `, ${res.failed_count} ошибок` : ''}`,
      });

      setSelected(new Set());
      setSmartBatchModal(null);
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка пакетного выполнения: ${err.message || err}` });
    } finally {
      setProcessingBulk(false);
    }
  };

  // Real Single Inline Actions
  const handleInlineTake = async (t: Ticket) => {
    try {
      await applyTask(t.rawId, {
        status_id: 27,
        comment: 'Взято в работу инженером 1-й линии',
        minutes: 5,
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

            {/* Smart Tab Context Action Buttons (100% HITL Batch Trigger) */}
            {filterTab === 'wifi' && countWifi > 0 && (
              <button
                onClick={() => openSmartBatchModal(scopedTickets.filter(t => t.ruleType === 'wlan_access' || t.templateKey === 'wifi_access'))}
                disabled={processingBulk}
                className="flex items-center gap-1.5 px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-[12.5px] font-bold shadow-xs cursor-pointer transition-colors ml-2 animate-in fade-in"
              >
                <IconWifi size={13} />
                <span>Выдать Wi-Fi всем ({countWifi})</span>
              </button>
            )}

            {filterTab === 'duplicates' && countDuplicates > 0 && (
              <button
                onClick={() => openSmartBatchModal(scopedTickets.filter(t => t.isDuplicate || t.ruleType === 'duplicate_task'))}
                disabled={processingBulk}
                className="flex items-center gap-1.5 px-3 py-1 bg-neutral-800 hover:bg-neutral-900 text-white dark:bg-neutral-200 dark:text-neutral-900 rounded-md text-[12.5px] font-bold shadow-xs cursor-pointer transition-colors ml-2 animate-in fade-in"
              >
                <IconDuplicate size={13} />
                <span>Отменить все дубликаты ({countDuplicates})</span>
              </button>
            )}

            {filterTab === 'redirects' && countRedirects > 0 && (
              <button
                onClick={() => openSmartBatchModal(scopedTickets.filter(t => t.isRedirect || t.ruleType?.startsWith('redirect')))}
                disabled={processingBulk}
                className="flex items-center gap-1.5 px-3 py-1 bg-amber-600 hover:bg-amber-500 text-white rounded-md text-[12.5px] font-bold shadow-xs cursor-pointer transition-colors ml-2 animate-in fade-in"
              >
                <IconRedirect size={13} />
                <span>Перенаправить все ({countRedirects})</span>
              </button>
            )}

            {filterTab === 'repair' && countRepair > 0 && (
              <button
                onClick={() => openSmartBatchModal(scopedTickets.filter(t => t.ruleType === 'hardware_repair'))}
                disabled={processingBulk}
                className="flex items-center gap-1.5 px-3 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-md text-[12.5px] font-bold shadow-xs cursor-pointer transition-colors ml-2 animate-in fade-in"
              >
                <IconWrench size={13} />
                <span>В ремонт все ({countRepair})</span>
              </button>
            )}
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
                  <th className="px-3.5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-blue-600 dark:text-blue-400">
                    РЕШЕНИЕ AI
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
                  const smartTagClass = "px-1.5 py-0.2 border border-neutral-200/80 dark:border-neutral-700/80 bg-neutral-100/80 dark:bg-neutral-800/80 text-neutral-600 dark:text-neutral-400 rounded text-[11px] font-medium";

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

                      {/* Status Indicator (Card/Button style, h-7 rounded-lg) */}
                      <td
                        className="px-3.5 py-2.5 whitespace-nowrap"
                        onClick={e => {
                          e.stopPropagation();
                          setInlineStatusTicketId(inlineStatusTicketId === ticket.id ? null : ticket.id);
                        }}
                      >
                        <div className="relative inline-block">
                          <button
                            type="button"
                            className="group h-7 inline-flex items-center gap-1.5 px-2.5 rounded-lg text-[11.5px] font-medium border border-neutral-200/90 dark:border-neutral-750 bg-neutral-50/80 hover:bg-neutral-100/90 dark:bg-neutral-850 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 transition-all cursor-pointer shadow-2xs hover:scale-[1.01] active:scale-[0.99]"
                            title="Нажмите для изменения статуса"
                          >
                            <span className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${statusConfig[ticket.status].dotClass}`} />
                            <span>{ticket.statusName || statusConfig[ticket.status].label}</span>
                            <IconChevronDown size={10} className="opacity-40 group-hover:opacity-100 transition-opacity ml-0.5" />
                          </button>

                          {inlineStatusTicketId === ticket.id && (
                            <div className="absolute left-0 top-8 z-30 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-xl shadow-xl py-1.5 min-w-[170px] animate-in fade-in zoom-in-95 duration-100">
                              <div className="px-2.5 py-1 text-[10px] uppercase font-bold text-neutral-400 dark:text-neutral-500 tracking-wider">
                                Сменить статус
                              </div>
                              {(['new', 'in_progress', 'waiting'] as Status[]).map(s => {
                                const sc = statusConfig[s];
                                return (
                                  <button
                                    key={s}
                                    onClick={e => {
                                      e.stopPropagation();
                                      handleInlineStatusChange(ticket, s);
                                    }}
                                    className="w-full px-2.5 py-1.5 text-left text-[12px] font-medium hover:bg-neutral-100 dark:hover:bg-neutral-800 cursor-pointer flex items-center gap-2 text-neutral-800 dark:text-neutral-200"
                                  >
                                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${sc.dotClass}`} />
                                    <span>{sc.label}</span>
                                  </button>
                                );
                              })}
                              <div className="border-t border-neutral-100 dark:border-neutral-800 my-1" />
                              <button
                                onClick={e => {
                                  e.stopPropagation();
                                  handleInlineStatusChange(ticket, 'resolved');
                                }}
                                className="w-full px-2.5 py-1.5 text-left text-[12px] font-semibold text-blue-600 dark:text-blue-400 hover:bg-neutral-100 dark:hover:bg-neutral-800 cursor-pointer flex items-center justify-between"
                              >
                                <div className="flex items-center gap-2">
                                  <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-emerald-500" />
                                  <span>Выполнить / Отменить</span>
                                </div>
                                <IconChevronRight size={12} />
                              </button>
                            </div>
                          )}
                        </div>
                      </td>

                      {/* Unified Smart AI Solution & Action Button (h-7 rounded-lg, монохромный с консистентным пульсирующим dot целевого статуса) */}
                      <td className="px-3.5 py-2.5 whitespace-nowrap" onClick={e => e.stopPropagation()}>
                        {ticket.statusId === 27 ? (
                          <div
                            className="h-7 inline-flex items-center gap-1.5 px-2.5 rounded-lg text-[11.5px] font-medium border border-neutral-200/90 dark:border-neutral-750 bg-neutral-50/80 dark:bg-neutral-850 text-neutral-700 dark:text-neutral-300 shadow-2xs"
                            title="Заявка уже переведена в статус «В работе»"
                          >
                            <span className="w-1.5 h-1.5 rounded-full bg-cyan-500 animate-pulse shrink-0" />
                            <span>В работе</span>
                          </div>
                        ) : (
                          <button
                            onClick={() => handleApplyTicketPlan(ticket)}
                            className="group h-7 inline-flex items-center gap-1.5 px-2.5 rounded-lg text-[11.5px] font-medium border border-neutral-200/90 dark:border-neutral-750 bg-neutral-50/80 hover:bg-neutral-100/90 dark:bg-neutral-850 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 transition-all cursor-pointer shadow-2xs hover:scale-[1.01] active:scale-[0.99]"
                            title={ticket.aiPlan ? `${ticket.aiPlan.actionTitle}\nОтвет: «${ticket.aiPlan.comment}»\nСписание: ${ticket.aiPlan.expensesMinutes} мин` : 'Принять заявку в работу'}
                          >
                            <span
                              className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${getStatusDotClass(
                                ticket.aiPlan?.targetStatusId ?? (ticket.ruleType === 'hardware_repair' ? 48 : 27)
                              )}`}
                            />
                            <span>{ticket.aiPlan?.targetStatusName || (ticket.ruleType === 'hardware_repair' ? 'Ожидание устройства' : 'В работе')}</span>
                          </button>
                        )}
                      </td>

                      {/* Ticket Title (Top) & Requester Info (Bottom), Tags next to Description */}
                      <td className="px-3.5 py-2.5 min-w-[280px]">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-neutral-900 dark:text-neutral-100 font-bold text-[13.5px] truncate max-w-md">
                            {ticket.title}
                          </span>

                          {/* Smart tag badges placed right beside title/description */}
                          {ticket.isDuplicate && (
                            <span className={`${smartTagClass} inline-flex items-center gap-1`}>
                              <IconDuplicate size={11} className="text-neutral-500 shrink-0" />
                              <span>дубликат</span>
                            </span>
                          )}
                          {ticket.isRedirect && (
                            <span className={`${smartTagClass} inline-flex items-center gap-1`}>
                              <IconRedirect size={11} className="text-neutral-500 shrink-0" />
                              <span>редирект</span>
                            </span>
                          )}
                          {ticket.ruleType === 'hardware_repair' && (
                            <span className={`${smartTagClass} inline-flex items-center gap-1`}>
                              <IconWrench size={11} className="text-neutral-500 shrink-0" />
                              <span>в ремонт</span>
                            </span>
                          )}
                          {(ticket.ruleType === 'wlan_access' || ticket.templateKey === 'wifi_access') && (
                            <span className={`${smartTagClass} inline-flex items-center gap-1`}>
                              <IconWifi size={11} className="text-neutral-500 shrink-0" />
                              <span>wi-fi</span>
                            </span>
                          )}
                          {ticket.hasAttachments && (
                            <span className="text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors shrink-0" title="Есть вложения">
                              <IconPaperclip size={13} />
                            </span>
                          )}
                        </div>

                        <div className="text-[12px] text-neutral-500 dark:text-neutral-400 mt-0.5 flex items-center gap-1.5 font-normal">
                          <a
                            href={`/admin/api/tasks/${ticket.rawId}/open`}
                            target="_blank"
                            rel="noreferrer"
                            onClick={e => e.stopPropagation()}
                            className="font-mono tabular-nums text-neutral-400 dark:text-neutral-500 hover:text-blue-600 dark:hover:text-blue-400 hover:underline shrink-0 font-semibold"
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

                      {/* Host (Clean Badge style) */}
                      <td className="px-3.5 py-2.5 whitespace-nowrap">
                        {primaryHost ? (
                          <div className="relative inline-flex items-center gap-1.5">
                            <span
                              onClick={(e) => {
                                e.stopPropagation();
                                navigator.clipboard.writeText(primaryHost);
                                onToast({ type: 'info', message: `Хост ${primaryHost} скопирован в буфер` });
                              }}
                              className="font-mono font-semibold text-[11.5px] bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 border border-neutral-200/80 dark:border-neutral-700/80 px-2 py-0.5 rounded cursor-pointer hover:border-neutral-300 dark:hover:border-neutral-600 transition-colors"
                              title="Нажмите, чтобы скопировать хост"
                            >
                              {primaryHost}
                            </span>

                            {otherHosts.length > 0 && (
                              <div className="relative">
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setOpenHostTicketId(openHostTicketId === ticket.id ? null : ticket.id);
                                  }}
                                  className="px-1.5 py-0.5 bg-blue-50 hover:bg-blue-100 dark:bg-blue-950/60 dark:hover:bg-blue-900 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800 rounded text-[11px] font-mono font-bold cursor-pointer transition-colors"
                                  title="Показать все хосты"
                                >
                                  +{otherHosts.length}
                                </button>

                                {openHostTicketId === ticket.id && (
                                  <div
                                    onClick={e => e.stopPropagation()}
                                    className="absolute left-0 top-7 z-30 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg shadow-2xl p-2 min-w-[160px] space-y-1 animate-in fade-in zoom-in-95 duration-100"
                                  >
                                    <span className="text-[10px] uppercase font-bold text-neutral-400 block px-1">
                                      Хосты ({hostList.length})
                                    </span>
                                    {hostList.map((h, i) => (
                                      <div
                                        key={i}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          navigator.clipboard.writeText(h);
                                          onToast({ type: 'info', message: `Хост ${h} скопирован` });
                                          setOpenHostTicketId(null);
                                        }}
                                        className="flex items-center justify-between px-2 py-1 bg-neutral-50 dark:bg-neutral-800 hover:bg-blue-50 dark:hover:bg-blue-950/50 rounded cursor-pointer transition-colors"
                                      >
                                        <span className="font-mono font-bold text-[12px] text-neutral-800 dark:text-neutral-200">{h}</span>
                                        <span className="text-[10px] text-neutral-400">копировать</span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="text-neutral-300 dark:text-neutral-700 font-mono text-[12px]">—</span>
                        )}
                      </td>

                      {/* Executors Column */}
                      <td className="px-3.5 py-2.5 whitespace-nowrap">
                        {ticket.executors ? (
                          <span className="text-[12px] text-neutral-800 dark:text-neutral-200 font-medium truncate block max-w-[140px]" title={ticket.executors}>
                            {ticket.executors}
                          </span>
                        ) : (
                          <span className="text-neutral-300 dark:text-neutral-700 font-mono text-[12px]">—</span>
                        )}
                      </td>

                      {/* SLA */}
                      <td className="w-24 px-3.5 py-2.5 whitespace-nowrap">
                        {ticket.slaDeadline.getTime() < Date.now() ? (
                          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10.5px] font-semibold bg-rose-50 text-rose-700 border border-rose-200/80 dark:bg-rose-950/50 dark:text-rose-300 dark:border-rose-900/60">
                            Просрочена
                          </span>
                        ) : (
                          <span className="text-neutral-500 dark:text-neutral-400 font-mono text-[11.5px] tabular-nums font-medium">
                            {formatSla(ticket.slaDeadline)}
                          </span>
                        )}
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
                      <span className={`w-2 h-2 rounded-full shrink-0 animate-pulse ${statusConfig[col.status].dotClass}`} />
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
                          className={`bg-white dark:bg-neutral-800 rounded-lg border p-3.5 cursor-pointer transition-all shadow-2xs hover:shadow-sm hover:scale-[1.01] active:scale-[0.99] ${selectedTicketId === t.id
                            ? 'border-blue-500 dark:border-blue-400 ring-2 ring-blue-500/30'
                            : 'border-neutral-200/80 dark:border-neutral-700/80 hover:border-neutral-300 dark:hover:border-neutral-600'
                            }`}
                        >
                          <div className="flex items-start justify-between gap-2 mb-1.5">
                            <span className="font-mono font-bold text-[12px] text-neutral-400 dark:text-neutral-500">#{t.rawId}</span>
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
                        <div className="flex flex-col items-center justify-center h-28 border border-dashed border-neutral-200 dark:border-neutral-800 rounded-xl p-4 text-center">
                          <span className="text-[12px] text-neutral-400 dark:text-neutral-500 font-medium">
                            Нет заявок
                          </span>
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

      {/* Real Bulk Action Bar (100% HITL Smart Automation) */}
      {selected.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2.5 px-4 py-2.5 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded-2xl shadow-2xl border border-neutral-700 dark:border-neutral-300 animate-in fade-in slide-in-from-bottom-3 duration-150 flex-wrap justify-center">
          <span className="text-[13px] font-bold mr-1 shrink-0">
            Выбрано: {selected.size}
          </span>
          <div className="w-px h-5 bg-neutral-700 dark:bg-neutral-300 shrink-0" />
          
          {/* Main 100% HITL Smart Button */}
          <button
            onClick={() => openSmartBatchModal(tickets.filter(t => selected.has(t.id)))}
            disabled={processingBulk}
            className="text-[13px] font-bold bg-blue-600 hover:bg-blue-500 text-white px-3.5 py-1.5 rounded-lg transition-all shadow-md cursor-pointer disabled:opacity-50 flex items-center gap-1.5 shrink-0"
          >
            <IconSparkles size={14} className="text-blue-200" />
            <span>Применить решения ({selected.size})</span>
          </button>

          <button
            onClick={() => initiateBulkAction('take')}
            disabled={processingBulk}
            className="text-[12.5px] font-semibold text-neutral-300 hover:text-white dark:text-neutral-700 dark:hover:text-neutral-900 transition-colors px-2 py-1 cursor-pointer disabled:opacity-50 shrink-0"
          >
            В работу
          </button>
          <button
            onClick={() => initiateBulkAction('cancel')}
            disabled={processingBulk}
            className="text-[12.5px] font-semibold text-neutral-300 hover:text-white dark:text-neutral-700 dark:hover:text-neutral-900 transition-colors px-2 py-1 cursor-pointer disabled:opacity-50 shrink-0"
          >
            Отменить
          </button>
          <div className="w-px h-5 bg-neutral-700 dark:bg-neutral-300 shrink-0" />
          <button
            onClick={() => setSelected(new Set())}
            className="text-neutral-400 hover:text-white dark:hover:text-neutral-900 transition-colors cursor-pointer p-1 shrink-0"
            title="Снять выделение"
          >
            <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
              <path d="M2 2l9 9M11 2l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      )}

      {/* Smart Batch Plan Modal (100% HITL Confirmation with In-line Edit) */}
      {smartBatchModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-2xs p-4">
          <div className="w-full max-w-2xl bg-white dark:bg-neutral-900 rounded-2xl shadow-2xl border border-neutral-200 dark:border-neutral-800 flex flex-col max-h-[85vh] animate-in fade-in zoom-in-95 duration-150">
            {/* Header */}
            <div className="px-6 py-4 border-b border-neutral-200 dark:border-neutral-800 flex items-center justify-between shrink-0">
              <div>
                <h3 className="text-base font-bold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                  <IconSparkles size={16} className="text-blue-600 dark:text-blue-400" />
                  <span>Сводный план индивидуального выполнения</span>
                  <span className="px-2 py-0.5 text-xs bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-200 rounded-full font-bold">
                    {smartBatchModal.items.filter(x => x.selected).length} из {smartBatchModal.items.length}
                  </span>
                </h3>
                <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
                  Каждая заявка будет исполнена в инфраструктуре и переведена в свой целевой статус с регламентным ответом заявителю
                </p>
              </div>
              <button
                onClick={() => setSmartBatchModal(null)}
                disabled={processingBulk}
                className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 cursor-pointer p-1"
              >
                <svg width="16" height="16" viewBox="0 0 14 14" fill="none">
                  <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                </svg>
              </button>
            </div>

            {/* Body: Tickets list */}
            <div className="flex-1 overflow-y-auto p-5 space-y-3">
              {smartBatchModal.items.map((item, idx) => {
                const t = item.ticket;
                const plan = t.aiPlan;
                return (
                  <div
                    key={t.id}
                    className={`p-3.5 rounded-xl border transition-all ${
                      item.selected
                        ? 'bg-neutral-50 dark:bg-neutral-800/60 border-neutral-200 dark:border-neutral-700 shadow-2xs'
                        : 'bg-neutral-100/40 dark:bg-neutral-900/40 border-neutral-200/50 dark:border-neutral-800/50 opacity-60'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-start gap-3 flex-1 min-w-0">
                        <input
                          type="checkbox"
                          checked={item.selected}
                          onChange={e => {
                            const checked = e.target.checked;
                            setSmartBatchModal(prev => prev ? {
                              ...prev,
                              items: prev.items.map((it, i) => i === idx ? { ...it, selected: checked } : it),
                            } : null);
                          }}
                          className="w-4 h-4 accent-blue-600 cursor-pointer rounded mt-1 shrink-0"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap mb-1">
                            <a
                              href={`/admin/api/tasks/${t.rawId}/open`}
                              target="_blank"
                              rel="noreferrer"
                              className="font-mono font-bold text-[13px] text-blue-600 dark:text-blue-400 hover:underline"
                            >
                              #{t.rawId}
                            </a>
                            <span className="font-bold text-[13.5px] text-neutral-900 dark:text-neutral-100 truncate max-w-sm">
                              {t.title}
                            </span>
                            {plan && (
                              <span className={`text-[11px] font-bold px-2 py-0.5 rounded border ${plan.badgeClass} inline-flex items-center gap-1`}>
                                <IconSparkles size={11} className="opacity-70" />
                                <span>{plan.actionBadge}</span>
                              </span>
                            )}
                          </div>
                          <div className="text-[12px] text-neutral-500 dark:text-neutral-400 flex items-center gap-2 flex-wrap">
                            <span>{t.requesterName}</span>
                            {t.room && <span>· каб. {t.room}</span>}
                            {t.host && <span className="font-mono font-semibold bg-neutral-200 dark:bg-neutral-700 px-1.5 py-0.2 rounded text-[11px]">{t.host}</span>}
                            <span>· {plan?.targetStatusName || t.statusName} ({item.minutes} мин)</span>
                          </div>
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => {
                          setSmartBatchModal(prev => prev ? {
                            ...prev,
                            items: prev.items.map((it, i) => i === idx ? { ...it, isEditing: !it.isEditing } : it),
                          } : null);
                        }}
                        className="text-[11.5px] text-blue-600 dark:text-blue-400 hover:underline font-bold shrink-0 cursor-pointer px-1.5 py-0.5 inline-flex items-center gap-1"
                      >
                        {item.isEditing ? (
                          'Свернуть'
                        ) : (
                          <>
                            <IconPencil size={11} />
                            <span>Изменить</span>
                          </>
                        )}
                      </button>
                    </div>

                    {/* Editing block */}
                    {item.isEditing ? (
                      <div className="mt-3 pt-3 border-t border-neutral-200 dark:border-neutral-700 space-y-2">
                        <label className="text-[11px] font-bold text-neutral-400 uppercase tracking-wider block">
                          Текст ответа заявителю:
                        </label>
                        <textarea
                          value={item.comment}
                          onChange={e => {
                            const text = e.target.value;
                            setSmartBatchModal(prev => prev ? {
                              ...prev,
                              items: prev.items.map((it, i) => i === idx ? { ...it, comment: text } : it),
                            } : null);
                          }}
                          rows={2}
                          className="w-full px-3 py-2 text-[12.5px] rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 outline-none resize-none"
                        />
                        <div className="flex items-center gap-2 text-[12px] text-neutral-500">
                          <span>Трудозатраты:</span>
                          <input
                            type="number"
                            value={item.minutes}
                            onChange={e => {
                              const m = Number(e.target.value);
                              setSmartBatchModal(prev => prev ? {
                                ...prev,
                                items: prev.items.map((it, i) => i === idx ? { ...it, minutes: m } : it),
                              } : null);
                            }}
                            min={0}
                            max={240}
                            className="w-14 h-6 px-1.5 bg-white dark:bg-neutral-900 border border-neutral-300 dark:border-neutral-700 rounded text-center font-mono font-bold text-[11px]"
                          />
                          <span>мин</span>
                        </div>
                      </div>
                    ) : (
                      <div className="mt-2 text-[12px] text-neutral-600 dark:text-neutral-400 italic bg-neutral-100/70 dark:bg-neutral-900/60 p-2 rounded-md line-clamp-2">
                        «{item.comment}»
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Footer */}
            <div className="px-6 py-4 border-t border-neutral-200 dark:border-neutral-800 flex items-center justify-between shrink-0 bg-neutral-50 dark:bg-neutral-950/60 rounded-b-2xl">
              <div className="text-xs text-neutral-500 dark:text-neutral-400">
                Выбрано к исполнению: <strong className="text-neutral-900 dark:text-neutral-100">{smartBatchModal.items.filter(x => x.selected).length}</strong>
              </div>
              <div className="flex items-center gap-2.5">
                <button
                  onClick={() => setSmartBatchModal(null)}
                  disabled={processingBulk}
                  className="px-4 py-2 text-[13px] font-medium text-neutral-700 dark:text-neutral-300 hover:bg-neutral-200 dark:hover:bg-neutral-800 rounded-lg transition-colors cursor-pointer"
                >
                  Отмена
                </button>
                <button
                  onClick={executeSmartBatch}
                  disabled={processingBulk || smartBatchModal.items.filter(x => x.selected).length === 0}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white text-[13px] font-bold rounded-lg transition-all shadow-md cursor-pointer disabled:opacity-50 flex items-center gap-2"
                >
                  {processingBulk ? (
                    <>
                      <svg className="animate-spin h-4 w-4 text-white" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                      </svg>
                      <span>Выполнение пакета...</span>
                    </>
                  ) : (
                    <>
                      <IconRocket size={14} />
                      <span>Запустить выполнение ({smartBatchModal.items.filter(x => x.selected).length})</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
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
              <div className="p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 rounded-lg text-[12.5px] text-amber-900 dark:text-amber-200 leading-snug flex items-start gap-2">
                <IconAlertTriangle size={16} className="text-amber-600 shrink-0 mt-0.5" />
                <div><strong>Внимание (Verified Execution):</strong> в выборке присутствуют заявки на аппаратный ремонт (Каб. 112). Завершайте их только после физической выдачи устройства заявителю!</div>
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
