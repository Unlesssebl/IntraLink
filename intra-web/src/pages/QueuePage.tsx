import { useState, useCallback, useEffect, useMemo } from 'react';

import type { Ticket, Status } from '../data/mock';

import { statusConfig, priorityConfig, getStatusDotClass, scenarioBadgeConfigs } from '../data/mock';

import {

  applyTask,

  bulkApplyTasks,

  smartBulkApplyTasks,

  mapStatusToStatusId,

  fetchActiveOutages,

  triggerQueueAnalysis,

  analyzeTask,

} from '../lib/tasks';

import type { AnalysisCounts, SmartBulkApplyItemPayload, OutageIncident } from '../lib/types';

import type { ServiceSelection } from '../components/Sidebar';

import TicketInspector from '../components/TicketInspector';

import OutageAlertBanner from '../components/queue/OutageAlertBanner';

import {

  IconWifi,

  IconDuplicate,

  IconRedirect,

  IconWrench,

  IconUser,

  IconPlay,

  IconPaperclip,

  IconSparkles,

  IconAlertTriangle,

  IconChevronDown,

  IconChevronRight,

  IconBookOpen,

  IconArrowRight,

  IconRefresh,

  IconCheck,

  IconCheckCircle,

} from '../components/Icons';

import SmartBatchModal, { type SmartBatchItem } from '../components/queue/SmartBatchModal';

import BulkConfirmModal, { type BulkConfirmModalState } from '../components/queue/BulkConfirmModal';

import { fetchTicketRuns, type TicketRun, type ActiveExecutionStatus } from '../lib/ticketRuns';

interface Props {

  tickets: Ticket[];

  analysisCounts?: AnalysisCounts | null;

  selectedTicketId: string | null;

  onSelectTicket: (id: string | null) => void;

  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;

  onRefresh: () => void;

  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;

  selectedService: ServiceSelection;

  onResetService: () => void;

  searchQuery?: string;

  activeExecution?: ActiveExecutionStatus | null;

  onSelectActiveTask?: (taskId: number) => void;
  activeBatch?: import('../App').ActiveBatchState | null;
  onStartBatch?: (batchState: import('../App').ActiveBatchState) => void;
  onCancelBatch?: (batchId: string) => Promise<void>;

}

type ViewMode = 'table' | 'kanban';

type ProcessedTab = 'processed' | 'unprocessed';

interface SmartBatchModalState {

  open: boolean;

  items: SmartBatchItem[];

}

function getRunBadgeConfig(state: string, mode: string) {

  switch (state) {

    case 'running':

      return {

        label: mode === 'autopilot' ? 'Автопилот: в работе' : 'В работе',

        className: 'bg-blue-50 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-900',

        dotClass: 'bg-blue-500 animate-pulse',

      };

    case 'waiting_approval':

      return {

        label: 'Ждёт подтверждения',

        className: 'bg-amber-50 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',

        dotClass: 'bg-amber-500 animate-ping',

      };

    case 'waiting_answer':

      return {

        label: 'Ждёт ответа',

        className: 'bg-sky-50 dark:bg-sky-950/60 text-sky-700 dark:text-sky-300 border-sky-200 dark:border-sky-900',

        dotClass: 'bg-sky-500',

      };

    case 'paused':

      return {

        label: 'На паузе',

        className: 'bg-orange-50 dark:bg-orange-950/60 text-orange-700 dark:text-orange-300 border-orange-200 dark:border-orange-900',

        dotClass: 'bg-orange-500',

      };

    case 'system_error':

      return {

        label: 'Ошибка связи',

        className: 'bg-rose-50 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-900',

        dotClass: 'bg-rose-500',

      };

    case 'completed':

      return {

        label: 'Завершён',

        className: 'bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700',

        dotClass: 'bg-neutral-400',

      };

    default:

      return {

        label: mode === 'autopilot' ? 'Автопилот' : 'Ожидает',

        className: 'bg-neutral-50 dark:bg-neutral-850 text-neutral-600 dark:text-neutral-400 border-neutral-200 dark:border-neutral-750',

        dotClass: 'bg-neutral-400',

      };

  }

}

function renderTicketRunPill(run: TicketRun) {

  const cfg = getRunBadgeConfig(run.state, run.mode);

  return (

    <span

      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-medium border ${cfg.className}`}

      title={`Цикл #${run.id} · шаг: ${run.current_step || '—'}`}

    >

      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${cfg.dotClass}`} />

      <span>{cfg.label}</span>

    </span>

  );

}

function getAnalysisBadge(ticket: Ticket) {

  const analysis = ticket.analysis;

  if (!analysis?.has_result) {

    return {

      label: analysis?.state === 'analyzing' ? 'Анализируется' : 'Ожидает анализа',

      className: 'bg-neutral-100 dark:bg-neutral-850 text-neutral-500 dark:text-neutral-400 border-neutral-200 dark:border-neutral-800',

    };

  }

  if (analysis.disposition === 'applied') {

    return {

      label: 'Решение применено',

      className: 'bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',

    };

  }

  if (analysis.state === 'failed') {

    return {

      label: 'Ошибка анализа',

      className: 'bg-rose-50 dark:bg-rose-950/50 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-900',

    };

  }

  if (analysis.freshness === 'stale') {

    return {

      label: 'Устарело',

      className: 'bg-amber-50 dark:bg-amber-950/50 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',

    };

  }

  if (analysis.freshness === 'unknown') {
    return {
      label: 'Актуальность не проверена',
      className: 'bg-neutral-100 dark:bg-neutral-850 text-neutral-600 dark:text-neutral-300 border-neutral-300 dark:border-neutral-700',
    };
  }

  const scenarioKey = ticket.scenarioKey || ticket.envelope?.scenario_key || ticket.ruleType;
  const scenarioConfig = scenarioKey ? scenarioBadgeConfigs[scenarioKey] : undefined;
  const label = scenarioConfig?.label || (ticket.isDuplicate ? 'Дубликат' : (scenarioKey ? String(scenarioKey) : 'Готово'));
  const className = scenarioConfig?.badgeClass || 'bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-900';

  return {
    label,
    className,
  };

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

  analysisCounts,

  selectedTicketId,

  onSelectTicket,

  onUpdateTicket,

  onRefresh,

  onToast,

  selectedService,

  onResetService,

  searchQuery = '',

  activeExecution,

  onSelectActiveTask,
  activeBatch,
  onStartBatch,
  onCancelBatch,

}: Props) {

  const [view, setView] = useState<ViewMode>('table');

  const [processedTab, setProcessedTab] = useState<ProcessedTab>('processed');

  const [isAnalyzing, setIsAnalyzing] = useState(false);

  const [analyzingTaskIds, setAnalyzingTaskIds] = useState<Set<number>>(new Set());

  const [selected, setSelected] = useState<Set<string>>(new Set());

  const [inlineStatusTicketId, setInlineStatusTicketId] = useState<string | null>(null);

  const [openHostTicketId, setOpenHostTicketId] = useState<string | null>(null);

  const [dragOver, setDragOver] = useState<Status | null>(null);

  const [sortCol, setSortCol] = useState<'sla' | 'created' | null>(null);

  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const [processingBulk, setProcessingBulk] = useState(false);

  const [bulkModal, setBulkModal] = useState<BulkConfirmModalState | null>(null);

  const [smartBatchModal, setSmartBatchModal] = useState<SmartBatchModalState | null>(null);

  const [outages, setOutages] = useState<OutageIncident[]>([]);

  const [activeOutageFilterIds, setActiveOutageFilterIds] = useState<number[] | null>(null);

  useEffect(() => {

    fetchActiveOutages().then(setOutages);

  }, []);

  const [ticketRuns, setTicketRuns] = useState<Record<number, TicketRun>>({});

  const [ticketRunsStaleAt, setTicketRunsStaleAt] = useState<Date | null>(null);

  const ticketIdsKey = tickets.map(ticket => ticket.rawId).join(',');

  useEffect(() => {

    const taskIds = tickets.map(ticket => ticket.rawId).filter(Boolean).slice(0, 200);

    if (taskIds.length === 0) {

      setTicketRuns({});

      return;

    }

    let cancelled = false;

    const loadRuns = () => {

      if (document.hidden) return;

      void fetchTicketRuns(taskIds)

        .then(({ items }) => {

          if (!cancelled && Array.isArray(items)) {

            setTicketRuns(prev => ({

              ...prev,

              ...Object.fromEntries(items.map(run => [run.task_id, run])),

            }));

            setTicketRunsStaleAt(null);

          }

        })

        .catch(() => {

          if (!cancelled) setTicketRunsStaleAt(new Date());

        });

    };

    loadRuns();

    const refreshTimer = window.setInterval(loadRuns, 30000);

    return () => {

      cancelled = true;

      window.clearInterval(refreshTimer);

    };

  }, [ticketIdsKey]);

  const selectedTicket = tickets.find(t => t.id === selectedTicketId) ?? null;

  // Фильтрация по выбранному сервису из сайдбара

  const scopedTickets = useMemo(() => {

    return tickets.filter(t => {

      if (selectedService.serviceId !== null) {

        return t.serviceId === selectedService.serviceId;

      }

      if (selectedService.rootId !== null) {

        return t.rootServiceId === selectedService.rootId || t.serviceId === selectedService.rootId;

      }

      return true;

    });

  }, [tickets, selectedService]);

  // Глобальное разделение на Обработанные и Не обработанные

  const processedTickets = useMemo(() => {

    return scopedTickets.filter(t => Boolean(t.isProcessed));

  }, [scopedTickets]);

  const unprocessedTickets = useMemo(() => {

    return scopedTickets.filter(t => !t.isProcessed);

  }, [scopedTickets]);

  const useServerCounts = selectedService.rootId === null && selectedService.serviceId === null;

  const countProcessed = useServerCounts && analysisCounts

    ? analysisCounts.analyzed

    : processedTickets.length;

  const countUnprocessed = useServerCounts && analysisCounts

    ? analysisCounts.not_analyzed

    : unprocessedTickets.length;

  // Автоматический выбор таба при пустых обработанных

  useEffect(() => {

    if (countProcessed === 0 && countUnprocessed > 0 && processedTab === 'processed') {

      setProcessedTab('unprocessed');

    }

  }, [countProcessed, countUnprocessed, processedTab]);

  // Запуск фонового анализа очереди или конкретных заявок

  const handleTriggerAnalysis = async (specificTaskIds?: number[]) => {
    setIsAnalyzing(true);
    if (specificTaskIds && specificTaskIds.length > 0) {
      setAnalyzingTaskIds(new Set(specificTaskIds));
    }
    try {
      const targetIds = specificTaskIds?.length
        ? specificTaskIds
        : unprocessedTickets.map(ticket => ticket.rawId);

      onToast({
        type: 'info',
        message: specificTaskIds?.length
          ? `Запуск анализа ${specificTaskIds.length} заявок...`
          : 'Запуск фонового анализа очереди...',
      });

      const res = await triggerQueueAnalysis(targetIds);

      if (res.already_running) {
        onToast({
          type: 'info',
          message: 'Пакетный анализ уже выполняется в фоновом режиме',
        });
      } else {
        onToast({
          type: 'success',
          message: `Анализ ${res.total} заявок запущен в фоне`,
        });
      }

      if (onStartBatch) {
        onStartBatch({
          batchId: res.batch_id,
          total: res.total,
          processed: 0,
          failed: 0,
          pct: 0,
          status: 'running',
        });
      }
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка запуска анализа: ${err.message || err}` });
    } finally {
      setIsAnalyzing(false);
      setAnalyzingTaskIds(new Set());
    }
  };

  const handleSingleReanalyze = async (ticket: Ticket) => {

    setAnalyzingTaskIds(prev => new Set(prev).add(ticket.rawId));

    try {

      onToast({ type: 'info', message: `Анализ заявки #${ticket.rawId}...` });

      await analyzeTask(ticket.rawId);

      onToast({ type: 'success', message: `Заявка #${ticket.rawId} обработана сценарием` });

      onRefresh();

    } catch (err: any) {

      onToast({ type: 'error', message: `Ошибка анализа #${ticket.rawId}: ${err.message || err}` });

    } finally {

      setAnalyzingTaskIds(prev => {

        const next = new Set(prev);

        next.delete(ticket.rawId);

        return next;

      });

    }

  };

  // Базовый набор тикетов по текущему сегменту

  const currentTabTickets = processedTab === 'processed' ? processedTickets : unprocessedTickets;

  const filtered = currentTabTickets.filter(t => {

    if (activeOutageFilterIds && !activeOutageFilterIds.includes(t.rawId)) return false;

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

  const handleApplyTicketPlan = async (t: Ticket) => {

    if (!t.analysis?.can_quick_apply || !t.analysis.decision_id || !t.analysis.decision_version) {

      onToast({

        type: 'warning',

        message: t.analysis?.blocked_reason || 'Результат нужно проверить в карточке заявки',

      });

      return;

    }

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

        decision_id: t.analysis.decision_id,

        decision_version: t.analysis.decision_version,

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

    const applicableTickets = ticketsToProcess.filter(

      ticket =>

        ticket.analysis?.can_quick_apply &&

        ticket.analysis.decision_id &&

        ticket.analysis.decision_version

    );

    if (applicableTickets.length !== ticketsToProcess.length) {

      onToast({

        type: 'warning',

        message: 'Устаревшие или непроверенные решения исключены из пакетного применения',

      });

    }

    if (applicableTickets.length === 0) return;

    const items: SmartBatchItem[] = applicableTickets.map(t => ({

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

          decision_id: item.ticket.analysis!.decision_id!,

          decision_version: item.ticket.analysis!.decision_version!,

        };

      });

      const res = await smartBulkApplyTasks(payload);

      const failedIds = new Set(res.errors.map(e => Number(e.task_id)));

      activeItems.forEach(item => {

        if (failedIds.has(item.ticket.rawId)) return;

        const plan = item.ticket.aiPlan;

        const targetStatusId = plan?.targetStatusId || 27;

        const newStatus = targetStatusId === 29 || targetStatusId === 30 ? 'resolved' : (targetStatusId === 35 || targetStatusId === 48 ? 'waiting' : 'in_progress');

        onUpdateTicket(item.ticket.id, {

          status: newStatus,

          statusId: targetStatusId,

          statusName: plan?.targetStatusName || 'В работе',

        });

      });

      if (res.failed_count > 0) {

        const errorDetails = res.errors.map(e => `#${e.task_id}: ${e.error}`).join('; ');

        onToast({

          type: 'warning',

          message: `Частичное выполнение: ${res.success_count} успешно, ${res.failed_count} ошибок (${errorDetails})`,

        });

        setSelected(new Set(res.errors.map(e => String(e.task_id))));

      } else {

        onToast({

          type: 'success',

          message: `Пакетное выполнение: ${res.success_count} успешно`,

        });

        setSelected(new Set());

      }

      setSmartBatchModal(null);

    } catch (err: any) {

      onToast({ type: 'error', message: `Ошибка пакетного выполнения: ${err.message || err}` });

    } finally {

      setProcessingBulk(false);

    }

  };

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

      const appliedSet = new Set((res.applied || []).map(a => Number(a.task_id)));

      const newStatus = targetStatusId === 29 || targetStatusId === 30 ? 'resolved' : (targetStatusId === 35 ? 'waiting' : 'in_progress');

      selectedTickets.forEach(t => {

        if (appliedSet.has(t.rawId)) {

          onUpdateTicket(t.id, { status: newStatus, statusId: targetStatusId, statusName: statusLabelName });

        }

      });

      if (res.failed_count > 0) {

        const errorDetails = (res.failed || []).map(f => `#${f.task_id}: ${f.error}`).join('; ');

        onToast({

          type: 'warning',

          message: `Частично выполнено: ${res.success_count} успешно, ${res.failed_count} с ошибкой (${errorDetails})`,

        });

        setSelected(new Set((res.failed || []).map(f => String(f.task_id))));

      } else {

        onToast({

          type: 'success',

          message: `Успешно обработано: ${res.success_count} из ${payload.length} заявок`,

        });

        setSelected(new Set());

      }

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

  const handleKanbanDrop = useCallback(

    async (status: Status, ticketId: string) => {

      const t = tickets.find(x => x.id === ticketId);

      if (!t) return;

      const prevStatus = t.status;

      const prevStatusId = t.statusId;

      const prevStatusName = t.statusName;

      const targetStatusId = mapStatusToStatusId(status);

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

      {/* Главная панель очереди */}

      <div className="flex-1 flex flex-col min-w-0 bg-white dark:bg-neutral-950">

        {/* Строгий тулбар по Swiss Grid (Zero-Emoji, лаконичное разделение) */}

        <div className="shrink-0 flex items-center justify-between gap-3 px-4 py-2.5 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 flex-wrap">

          <div className="flex items-center gap-3 flex-wrap">

            {/* Переключатель вида */}

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

            {/* Глобальные сегменты: Обработанные / Не обработанные */}

            <div className="flex items-center gap-1.5 p-0.5 bg-neutral-100 dark:bg-neutral-900 rounded-lg border border-neutral-200 dark:border-neutral-800">

              <button

                type="button"

                onClick={() => setProcessedTab('processed')}

                className={`flex items-center gap-2 px-3 py-1 rounded-md text-[12.5px] font-semibold transition-all cursor-pointer ${processedTab === 'processed'

                    ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900 shadow-2xs'

                    : 'text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100'

                  }`}

              >

                <span>Проанализированные AI</span>

                <span

                  className={`text-[11px] tabular-nums font-mono px-1.5 py-0.2 rounded-full font-bold ${processedTab === 'processed'

                      ? 'bg-white/20 text-white dark:bg-neutral-900/20 dark:text-neutral-900'

                      : 'bg-neutral-200 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400'

                    }`}

                >

                  {countProcessed}

                </span>

              </button>

              <button

                type="button"

                onClick={() => setProcessedTab('unprocessed')}

                className={`flex items-center gap-2 px-3 py-1 rounded-md text-[12.5px] font-semibold transition-all cursor-pointer ${processedTab === 'unprocessed'

                    ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900 shadow-2xs'

                    : 'text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100'

                  }`}

              >

                <span>Не проанализированные AI</span>

                <span

                  className={`text-[11px] tabular-nums font-mono px-1.5 py-0.2 rounded-full font-bold ${processedTab === 'unprocessed'

                      ? 'bg-white/20 text-white dark:bg-neutral-900/20 dark:text-neutral-900'

                      : countUnprocessed > 0

                        ? 'bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300 font-semibold'

                        : 'bg-neutral-200 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400'

                    }`}

                >

                  {countUnprocessed}

                </span>

              </button>

            </div>

            {/* Кнопка запуска анализа очереди */}

            <button

              type="button"

              onClick={() => handleTriggerAnalysis()}

              disabled={isAnalyzing}

              className="flex items-center gap-1.5 px-3 py-1 rounded-md text-[12.5px] font-semibold transition-all cursor-pointer border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-850 text-neutral-800 dark:text-neutral-200 hover:bg-neutral-50 dark:hover:bg-neutral-800 shadow-2xs disabled:opacity-60"

              title="Принудительно запустить анализ входящих заявок через сценарный пайплайн"

            >

              <IconRefresh

                size={13}

                className={`text-neutral-500 dark:text-neutral-400 ${isAnalyzing ? 'animate-spin' : ''}`}

              />

              <span>{isAnalyzing ? 'Анализ очереди...' : 'Запустить анализ'}</span>

            </button>

            {activeOutageFilterIds && (

              <div className="flex items-center gap-1.5 px-2.5 py-1 bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded-lg text-xs ml-1 animate-in fade-in">

                <span>Фильтр инцидента ({activeOutageFilterIds.length} заявок)</span>

                <button

                  type="button"

                  onClick={() => setActiveOutageFilterIds(null)}

                  className="text-rose-400 hover:text-rose-100 cursor-pointer ml-1 font-bold"

                  title="Сбросить фильтр инцидента"

                >

                  ✕

                </button>

              </div>

            )}

          </div>

          <div className="flex items-center gap-2">

            {selectedService.name && (

              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[12px] bg-neutral-100 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 text-neutral-700 dark:text-neutral-300">

                <span className="text-neutral-400 dark:text-neutral-500">Сервис:</span>

                <span className="font-semibold truncate max-w-[180px]">{selectedService.name}</span>

                <button

                  type="button"

                  onClick={onResetService}

                  className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 cursor-pointer ml-1 font-bold"

                  title="Показать все сервисы"

                >

                  ✕

                </button>

              </div>

            )}

            {ticketRunsStaleAt && (

              <span

                className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"

                title={ticketRunsStaleAt.toLocaleString('ru-RU')}

              >

                Состояния циклов временно недоступны

              </span>

            )}

          </div>

        </div>

        {/* Индикатор прогресса пакетного анализа очереди */}
        {activeBatch && (
          <div className="px-4 py-2 bg-blue-50/90 dark:bg-blue-950/40 border-b border-blue-200 dark:border-blue-900/60 flex items-center justify-between gap-4 animate-in fade-in transition-all">
            <div className="flex items-center gap-3 min-w-0 flex-1">
              <div className="relative flex items-center justify-center shrink-0">
                <svg className={`h-4 w-4 text-blue-600 dark:text-blue-400 ${activeBatch.status === 'running' ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                </svg>
              </div>
              <div className="flex flex-col min-w-0 flex-1">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="font-semibold text-neutral-800 dark:text-neutral-200">
                    {activeBatch.status === 'cancelling'
                      ? 'Остановка пакетного анализа...'
                      : activeBatch.status === 'cancelled'
                      ? 'Пакетный анализ остановлен'
                      : activeBatch.status === 'completed'
                      ? 'Пакетный анализ завершён'
                      : 'Пакетный сценарный анализ очереди'}
                  </span>
                  <span className="font-mono text-[11px] text-neutral-600 dark:text-neutral-400">
                    {activeBatch.processed + activeBatch.failed} / {activeBatch.total} ({activeBatch.pct}%)
                  </span>
                </div>
                <div className="w-full bg-neutral-200 dark:bg-neutral-800 h-1.5 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all duration-300 rounded-full ${
                      activeBatch.status === 'cancelled'
                        ? 'bg-amber-500'
                        : activeBatch.status === 'completed'
                        ? 'bg-emerald-500'
                        : 'bg-blue-600 dark:bg-blue-500'
                    }`}
                    style={{ width: `${Math.min(100, Math.max(2, activeBatch.pct))}%` }}
                  />
                </div>
              </div>
            </div>
            {activeBatch.status === 'running' && onCancelBatch && (
              <button
                type="button"
                onClick={() => onCancelBatch(activeBatch.batchId)}
                className="px-2.5 py-1 text-xs font-semibold text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/50 border border-rose-200 dark:border-rose-900/60 rounded transition-colors cursor-pointer shrink-0"
              >
                Остановить
              </button>
            )}
          </div>
        )}

        {/* Аварийный баннер AIOps */}

        {outages.length > 0 && (

          <div className="px-4 pt-3">

            <OutageAlertBanner

              outages={outages}

              onSelectTicket={id => onSelectTicket(id)}

              onFilterTicketIds={ids => setActiveOutageFilterIds(ids)}

              onToast={onToast}

              onOutageResolved={outageId => {

                setOutages(prev => prev.filter(o => o.id !== outageId));

                setActiveOutageFilterIds(null);

              }}

            />

          </div>

        )}

        {/* Индикатор активного выполнения ассистента */}

        {activeExecution?.has_active && activeExecution.task_id && (

          <div

            className={`shrink-0 px-4 py-2 border-b flex items-center justify-between gap-3 text-xs transition-colors ${activeExecution.state === 'waiting_approval'

                ? 'bg-amber-50/90 dark:bg-amber-950/40 border-amber-200/80 dark:border-amber-900/60 text-amber-900 dark:text-amber-200'

                : 'bg-blue-50/90 dark:bg-blue-950/40 border-blue-200/80 dark:border-blue-900/60 text-blue-900 dark:text-blue-200'

              }`}

          >

            <div className="flex items-center gap-2.5 min-w-0">

              <span className="relative flex h-2 w-2 shrink-0">

                <span

                  className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${activeExecution.state === 'waiting_approval' ? 'bg-amber-400' : 'bg-blue-400'

                    }`}

                />

                <span

                  className={`relative inline-flex rounded-full h-2 w-2 ${activeExecution.state === 'waiting_approval' ? 'bg-amber-500' : 'bg-blue-500'

                    }`}

                />

              </span>

              <div className="flex items-center gap-2 min-w-0 font-medium">

                <span className="text-neutral-500 dark:text-neutral-400 shrink-0">Ассистент:</span>

                <span className="font-mono font-bold shrink-0">#{activeExecution.task_id}</span>

                <span className="opacity-40 shrink-0">·</span>

                <span className="truncate">{activeExecution.status_text}</span>

              </div>

            </div>

            <button

              type="button"

              onClick={() =>

                onSelectActiveTask

                  ? onSelectActiveTask(activeExecution.task_id!)

                  : onSelectTicket(String(activeExecution.task_id))

              }

              className={`shrink-0 px-3 py-1 rounded-md text-[11.5px] font-semibold transition-colors cursor-pointer border ${activeExecution.state === 'waiting_approval'

                  ? 'bg-amber-600 hover:bg-amber-500 text-white border-amber-500 shadow-2xs'

                  : 'bg-white dark:bg-neutral-900 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800 hover:bg-blue-50 dark:hover:bg-blue-950/60'

                }`}

            >

              {activeExecution.state === 'waiting_approval' ? 'Подтвердить действие →' : 'Открыть карточку →'}

            </button>

          </div>

        )}

        {/* Табличный вид */}

        {view === 'table' && (

          <div className="flex-1 overflow-auto bg-white dark:bg-neutral-950">

            <table className="w-full min-w-[1360px] text-[14px] border-collapse table-fixed">

              <thead className="sticky top-0 z-10 bg-white dark:bg-neutral-950 border-b border-neutral-200 dark:border-neutral-800">

                <tr>

                  <th className="w-12 px-3.5 py-3 text-center">

                    <input

                      type="checkbox"

                      checked={selected.size === sorted.length && sorted.length > 0}

                      onChange={e => setSelected(e.target.checked ? new Set(sorted.map(t => t.id)) : new Set())}

                      className="w-4 h-4 accent-neutral-900 dark:accent-neutral-100 cursor-pointer rounded"

                    />

                  </th>

                  <th className="w-40 px-3.5 py-3 text-left text-[11.5px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">

                    СТАТУС

                  </th>

                  <th className="w-48 px-3.5 py-3 text-left text-[11.5px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">

                    {processedTab === 'processed' ? 'СЦЕНАРИЙ / РЕШЕНИЕ' : 'СОСТОЯНИЕ АНАЛИЗА'}

                  </th>

                  <th className="w-[360px] px-3.5 py-3 text-left text-[11.5px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">

                    ЗАЯВКА

                  </th>

                  <th className="w-48 px-3.5 py-3 text-left text-[11.5px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">

                    СЕРВИС

                  </th>

                  <th className="w-36 px-3.5 py-3 text-left text-[11.5px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">

                    ХОСТ

                  </th>

                  <th className="w-40 px-3.5 py-3 text-left text-[11.5px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">

                    ИСПОЛНИТЕЛЬ

                  </th>

                  <th

                    className="w-28 px-3.5 py-3 text-left text-[11.5px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500 cursor-pointer select-none group whitespace-nowrap"

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

                  const isTicketAnalyzing = analyzingTaskIds.has(ticket.rawId);

                  const rowBg = isActive

                    ? '!bg-blue-100/90 dark:!bg-blue-950/80 border-l-4 border-l-blue-600 dark:border-l-blue-500'

                    : isSelected

                      ? '!bg-neutral-200/90 dark:!bg-neutral-800'

                      : isEven

                        ? 'bg-[#f8fafc] dark:bg-neutral-900/50'

                        : 'bg-white dark:bg-neutral-950';

                  const hostList = parseHostList(ticket.host);

                  const primaryHost = hostList[0];

                  const otherHosts = hostList.slice(1);

                  const ticketRun = ticketRuns[ticket.rawId];

                  const scenarioKey = ticket.scenarioKey || ticket.envelope?.scenario_key || ticket.ruleType;

                  const scenarioConfig = scenarioKey ? scenarioBadgeConfigs[scenarioKey] : undefined;

                  const scenarioLabel = scenarioConfig?.label || (ticket.isDuplicate ? 'Дубликат' : (scenarioKey || '—'));

                  const analysisBadge = getAnalysisBadge(ticket);

                  return (

                    <tr

                      key={ticket.id}

                      onClick={() => onSelectTicket(isActive ? null : ticket.id)}

                      className={`cursor-pointer transition-colors outline-none hover:!bg-neutral-100/70 dark:hover:!bg-neutral-850/80 h-[66px] border-b border-neutral-100 dark:border-neutral-850 ${rowBg}`}

                    >

                      {/* Чекбокс */}

                      <td className="w-12 px-3.5 py-3 text-center" onClick={e => e.stopPropagation()}>

                        <input

                          type="checkbox"

                          checked={isSelected}

                          onChange={() => toggleSelect(ticket.id)}

                          className="w-4 h-4 accent-neutral-900 dark:accent-neutral-100 cursor-pointer rounded"

                        />

                      </td>

                      {/* Статус заявки */}

                      <td

                        className="w-40 px-3.5 py-3 whitespace-nowrap"

                        onClick={e => {

                          e.stopPropagation();

                          setInlineStatusTicketId(inlineStatusTicketId === ticket.id ? null : ticket.id);

                        }}

                      >

                        <div className="relative inline-block">

                          <button

                            type="button"

                            className="group h-7.5 max-w-full inline-flex items-center gap-1.5 px-2.5 rounded-md text-[12px] font-medium border border-neutral-200/90 dark:border-neutral-750 bg-neutral-50/80 hover:bg-neutral-100/90 dark:bg-neutral-850 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 transition-all cursor-pointer shadow-2xs"

                            title="Нажмите для изменения статуса"

                          >

                            <span

                              className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusConfig[ticket.status].dotClass}`}

                            />

                            <span className="truncate">{ticket.statusName || statusConfig[ticket.status].label}</span>

                            <IconChevronDown

                              size={10}

                              className="opacity-40 group-hover:opacity-100 transition-opacity ml-0.5"

                            />

                          </button>

                          {activeExecution?.has_active && activeExecution.task_id === ticket.rawId ? (

                            <div className="mt-1.5">

                              <span

                                className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-medium border ${activeExecution.state === 'waiting_approval'

                                    ? 'bg-amber-50 dark:bg-amber-950/50 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800'

                                    : 'bg-blue-50 dark:bg-blue-950/50 text-blue-800 dark:text-blue-200 border-blue-300 dark:border-blue-800'

                                  }`}

                                title={activeExecution.status_text}

                              >

                                <span

                                  className={`w-1.5 h-1.5 rounded-full shrink-0 animate-ping ${activeExecution.state === 'waiting_approval' ? 'bg-amber-500' : 'bg-blue-500'

                                    }`}

                                />

                                <span className="truncate max-w-[130px]">

                                  {activeExecution.phase_title ||

                                    (activeExecution.state === 'waiting_approval'

                                      ? 'Ожидает одобрения'

                                      : 'Выполняется')}

                                </span>

                              </span>

                            </div>

                          ) : ticketRun ? (

                            <div className="mt-1.5">{renderTicketRunPill(ticketRun)}</div>

                          ) : null}

                          {inlineStatusTicketId === ticket.id && (

                            <div className="absolute left-0 top-8 z-30 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg shadow-xl py-1.5 min-w-[170px] animate-in fade-in zoom-in-95 duration-100">

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

                                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${sc.dotClass}`} />

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

                                className="w-full px-2.5 py-1.5 text-left text-[12px] font-semibold text-neutral-800 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 cursor-pointer flex items-center justify-between"

                              >

                                <div className="flex items-center gap-2">

                                  <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-emerald-500" />

                                  <span>Выполнить / Отменить</span>

                                </div>

                                <IconChevronRight size={12} />

                              </button>

                            </div>

                          )}

                        </div>

                      </td>

                      {/* Сценарий / Решение (или статус анализа) */}

                      <td className="w-48 overflow-hidden px-3.5 py-3 whitespace-nowrap" onClick={e => e.stopPropagation()}>

                        {ticket.isProcessed ? (

                          <div className="flex flex-col gap-1 items-start">

                            {/* Операторский статус анализа; сценарий остаётся в title для отладки. */}

                            <span

                              className={`px-2 py-0.5 rounded text-[11px] font-medium border inline-flex items-center gap-1 max-w-[190px] truncate ${analysisBadge.className}`}

                              title={`${analysisBadge.label} · сценарий: ${scenarioLabel}`}

                            >

                              <span className="truncate">{analysisBadge.label}</span>

                            </span>

                            {/* Кнопка быстрого применения целевого статуса */}

                            {ticket.statusId === 27 ? (

                              <span className="text-[11px] text-neutral-400 dark:text-neutral-500 font-medium">

                                В работе

                              </span>

                            ) : (

                              <div className="flex flex-col gap-0.5 items-start">

                                <button

                                  type="button"

                                  onClick={() => handleApplyTicketPlan(ticket)}

                                  disabled={!ticket.analysis?.can_quick_apply}

                                  className="group h-6 max-w-full inline-flex items-center gap-1 px-2 rounded text-[11px] font-medium border border-neutral-200/90 dark:border-neutral-750 bg-neutral-50 hover:bg-neutral-100 dark:bg-neutral-850 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 transition-all cursor-pointer shadow-2xs disabled:cursor-not-allowed disabled:opacity-50"

                                  title={

                                    !ticket.analysis?.can_quick_apply

                                      ? ticket.analysis?.blocked_reason || 'Результат недоступен для применения'

                                      : ticket.aiPlan

                                      ? `${ticket.aiPlan.actionTitle}\nОтвет: «${ticket.aiPlan.comment}»`

                                      : 'Принять в работу'

                                  }

                                >

                                <span

                                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${getStatusDotClass(

                                    ticket.aiPlan?.targetStatusId ?? 27

                                  )}`}

                                />

                                <span className="truncate max-w-[120px]">

                                  {ticket.aiPlan?.targetStatusName || 'В работу'}

                                </span>

                                <IconArrowRight

                                  size={9}

                                  className="text-neutral-400 group-hover:text-neutral-700 dark:group-hover:text-neutral-200 transition-colors ml-0.5 shrink-0"

                                />

                              </button>

                              {ticket.analysis && !ticket.analysis.can_quick_apply && ticket.analysis.blocked_reason && (

                                <span

                                  tabIndex={0}

                                  className="text-[10px] text-neutral-400 dark:text-neutral-500 truncate max-w-[130px] cursor-help focus-visible:ring-1 focus-visible:ring-blue-500 rounded outline-none"

                                  title={ticket.analysis.blocked_reason}

                                  aria-label={`Причина блокировки: ${ticket.analysis.blocked_reason}`}

                                >

                                  {ticket.analysis.blocked_reason}

                                </span>

                              )}

                              </div>

                            )}

                          </div>

                        ) : (

                          <div className="flex flex-col gap-1 items-start">

                            <span className="px-2 py-0.5 rounded text-[11px] font-medium border bg-neutral-100 dark:bg-neutral-850 text-neutral-500 dark:text-neutral-400 border-neutral-200 dark:border-neutral-800">

                              Ожидает анализа

                            </span>

                            <button

                              type="button"

                              disabled={isTicketAnalyzing}

                              onClick={() => handleSingleReanalyze(ticket)}

                              className="h-6 inline-flex items-center gap-1 px-2 rounded text-[11px] font-medium border border-neutral-200/80 dark:border-neutral-750 bg-white hover:bg-neutral-50 dark:bg-neutral-900 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 transition-colors cursor-pointer disabled:opacity-50"

                              title="Запустить сценарный анализ для этой заявки"

                            >

                              <IconRefresh

                                size={10}

                                className={`text-neutral-400 ${isTicketAnalyzing ? 'animate-spin' : ''}`}

                              />

                              <span>{isTicketAnalyzing ? 'Анализ...' : 'Анализ'}</span>

                            </button>

                          </div>

                        )}

                      </td>

                      {/* Заявка (Тема, ID, Заявитель) */}

                      <td className="w-[360px] min-w-0 overflow-hidden px-3.5 py-3">

                        <div className="flex min-w-0 items-center gap-1.5 overflow-hidden">

                          <span className="min-w-0 flex-1 truncate text-[14.5px] font-semibold text-neutral-900 dark:text-neutral-100">

                            {ticket.title}

                          </span>

                          {ticket.hasKbMatches && (

                            <span

                              className="px-1.5 py-0.2 rounded text-[10px] font-medium border border-neutral-200 dark:border-neutral-800 bg-neutral-100/80 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 inline-flex items-center gap-1 shrink-0"

                              title="Найдено прецедентное решение в базе знаний"

                            >

                              <IconBookOpen size={10} className="shrink-0 text-neutral-500" />

                              <span>RAG</span>

                            </span>

                          )}

                          {ticket.circuit === 'red' && (

                            <span

                              className="px-1 py-0.2 rounded text-[9.5px] font-mono font-bold border border-rose-200 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/60 text-rose-600 dark:text-rose-400 shrink-0"

                              title="Контур RED: повышенные требования безопасности"

                            >

                              RED

                            </span>

                          )}

                          {ticket.hasAttachments && (

                            <span

                              className="text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors shrink-0"

                              title="Есть вложения"

                            >

                              <IconPaperclip size={13} />

                            </span>

                          )}

                        </div>

                        <div className="mt-1 flex min-w-0 items-center gap-1.5 overflow-hidden text-[12.5px] font-normal text-neutral-500 dark:text-neutral-400">

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

                          <span className="min-w-0 truncate">{ticket.requesterName}</span>

                          {ticket.room && <span className="shrink-0">· каб. {ticket.room}</span>}

                          {ticket.department && <span className="min-w-0 truncate">· {ticket.department}</span>}

                        </div>

                      </td>

                      {/* Сервис / Услуга */}

                      <td className="w-48 px-3.5 py-3 whitespace-nowrap">

                        <span

                          className="text-[13px] text-neutral-700 dark:text-neutral-300 font-normal truncate block max-w-[190px]"

                          title={ticket.servicePath || ticket.serviceName}

                        >

                          {ticket.serviceName}

                        </span>

                      </td>

                      {/* Хост рабочей станции */}

                      <td className="w-36 px-3.5 py-3 whitespace-nowrap">

                        {primaryHost ? (

                          <div className="relative inline-flex items-center gap-1.5">

                            <span

                              onClick={e => {

                                e.stopPropagation();

                                navigator.clipboard.writeText(primaryHost);

                                onToast({ type: 'info', message: `Хост ${primaryHost} скопирован в буфер` });

                              }}

                              className="max-w-[105px] truncate font-mono font-semibold text-[11.5px] bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 border border-neutral-200/80 dark:border-neutral-700/80 px-2 py-0.5 rounded cursor-pointer hover:border-neutral-300 dark:hover:border-neutral-600 transition-colors"

                              title="Нажмите, чтобы скопировать хост"

                            >

                              {primaryHost}

                            </span>

                            {otherHosts.length > 0 && (

                              <div className="relative">

                                <button

                                  type="button"

                                  onClick={e => {

                                    e.stopPropagation();

                                    setOpenHostTicketId(openHostTicketId === ticket.id ? null : ticket.id);

                                  }}

                                  className="px-1.5 py-0.5 bg-neutral-100 hover:bg-neutral-200 dark:bg-neutral-800 dark:hover:bg-neutral-700 text-neutral-600 dark:text-neutral-300 border border-neutral-200 dark:border-neutral-700 rounded text-[10.5px] font-mono font-bold cursor-pointer transition-colors"

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

                                        onClick={e => {

                                          e.stopPropagation();

                                          navigator.clipboard.writeText(h);

                                          onToast({ type: 'info', message: `Хост ${h} скопирован` });

                                          setOpenHostTicketId(null);

                                        }}

                                        className="flex items-center justify-between px-2 py-1 bg-neutral-50 dark:bg-neutral-800 hover:bg-neutral-100 dark:hover:bg-neutral-700 rounded cursor-pointer transition-colors"

                                      >

                                        <span className="font-mono font-bold text-[12px] text-neutral-800 dark:text-neutral-200">

                                          {h}

                                        </span>

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

                      {/* Исполнитель */}

                      <td className="w-40 px-3.5 py-3 whitespace-nowrap">

                        {ticket.executors ? (

                          <span

                            className="text-[12.5px] text-neutral-700 dark:text-neutral-300 font-medium truncate block max-w-[145px]"

                            title={ticket.executors}

                          >

                            {ticket.executors}

                          </span>

                        ) : (

                          <span className="text-neutral-300 dark:text-neutral-700 font-mono text-[12px]">—</span>

                        )}

                      </td>

                      {/* SLA */}

                      <td className="w-28 px-3.5 py-3 whitespace-nowrap">

                        {ticket.slaDeadline.getTime() < Date.now() ? (

                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold bg-rose-50 text-rose-700 border border-rose-200 dark:bg-rose-950/50 dark:text-rose-300 dark:border-rose-900/60">

                            Просрочена

                          </span>

                        ) : (

                          <span className="text-neutral-500 dark:text-neutral-400 font-mono text-[12px] tabular-nums font-medium">

                            {formatSla(ticket.slaDeadline)}

                          </span>

                        )}

                      </td>

                    </tr>

                  );

                })}

              </tbody>

            </table>

            {/* Состояния отсутствия заявок (Zero-Emoji, строгий Swiss Grid) */}

            {sorted.length === 0 && (

              <div className="flex flex-col items-center justify-center py-20 text-neutral-400 dark:text-neutral-600">

                <div className="w-10 h-10 rounded-full border border-neutral-200 dark:border-neutral-800 flex items-center justify-center mb-3 bg-neutral-50 dark:bg-neutral-900">

                  <IconCheck size={18} className="text-neutral-400 dark:text-neutral-500" />

                </div>

                <p className="text-[14px] font-semibold text-neutral-700 dark:text-neutral-300">

                  {processedTab === 'processed'

                    ? countProcessed === 0

                      ? 'Нет проанализированных заявок'

                      : 'Нет заявок по текущему фильтру'

                    : countUnprocessed === 0

                      ? 'Все заявки в выбранной области проанализированы AI'

                      : 'Нет не проанализированных заявок по фильтру'}

                </p>

                <p className="text-[12px] text-neutral-400 dark:text-neutral-500 mt-1 max-w-[340px] text-center">

                  {processedTab === 'processed' && countProcessed === 0 && countUnprocessed > 0 ? (

                    <span>

                      В очереди находится {countUnprocessed} заявок без анализа.{' '}

                      <button

                        type="button"

                        onClick={() => setProcessedTab('unprocessed')}

                        className="text-neutral-900 dark:text-neutral-100 font-semibold underline cursor-pointer"

                      >

                        Перейти к не проанализированным

                      </button>

                    </span>

                  ) : processedTab === 'unprocessed' && countUnprocessed === 0 ? (

                    <span>Все обращения в выбранной области очереди успешно проанализированы AI</span>

                  ) : (

                    <span>Попробуйте сбросить поисковый запрос или фильтр сервиса</span>

                  )}

                </p>

              </div>

            )}

          </div>

        )}

        {/* Канбан вид */}

        {view === 'kanban' && (

          <div className="flex-1 overflow-x-auto p-4">

            <div className="flex gap-4 h-full min-w-max">

              {kanbanCols.map(col => {

                const colTickets = sorted.filter(t => t.status === col.status);

                return (

                  <div

                    key={col.status}

                    className={`w-80 shrink-0 flex flex-col rounded-lg border transition-colors ${dragOver === col.status

                        ? 'border-neutral-900 bg-neutral-100/50 dark:border-neutral-100 dark:bg-neutral-900/50'

                        : 'border-neutral-200 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-900/40'

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

                    <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-neutral-200 dark:border-neutral-800 shrink-0">

                      <span className={`w-2 h-2 rounded-full shrink-0 ${statusConfig[col.status].dotClass}`} />

                      <span className="text-[13px] font-semibold text-neutral-800 dark:text-neutral-200">

                        {col.label}

                      </span>

                      <span className="ml-auto text-[11px] font-mono bg-neutral-200 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 px-1.5 py-0.2 rounded font-semibold">

                        {colTickets.length}

                      </span>

                    </div>

                    <div className="flex-1 overflow-y-auto p-2.5 space-y-2">

                      {colTickets.map(t => (

                        <div

                          key={t.id}

                          draggable

                          onDragStart={e => e.dataTransfer.setData('ticketId', t.id)}

                          onClick={() => onSelectTicket(selectedTicketId === t.id ? null : t.id)}

                          className={`bg-white dark:bg-neutral-850 rounded-md border p-3 cursor-pointer transition-all shadow-2xs hover:border-neutral-300 dark:hover:border-neutral-700 ${selectedTicketId === t.id

                              ? 'border-neutral-900 dark:border-neutral-100 ring-1 ring-neutral-900 dark:ring-neutral-100'

                              : 'border-neutral-200 dark:border-neutral-800'

                            }`}

                        >

                          <div className="flex items-start justify-between gap-2 mb-1.5">

                            <span className="font-mono font-semibold text-[11.5px] text-neutral-400 dark:text-neutral-500">

                              #{t.rawId}

                            </span>

                            <span

                              className={`text-[10.5px] font-semibold flex items-center gap-1 ${priorityConfig[t.priority].textClass

                                }`}

                            >

                              <span className={`w-1.5 h-1.5 rounded-full ${priorityConfig[t.priority].dotClass}`} />

                              {priorityConfig[t.priority].label}

                            </span>

                          </div>

                          <p className="text-[13px] font-medium text-neutral-900 dark:text-neutral-100 leading-snug mb-2">

                            {t.title}

                          </p>

                          <div className="flex items-center justify-between text-[11.5px] text-neutral-400">

                            <span className="truncate max-w-[140px]">{t.requesterName}</span>

                            <span className={`font-mono font-medium ${getSlaClass(t.slaDeadline)}`}>

                              {formatSla(t.slaDeadline)}

                            </span>

                          </div>

                          {activeExecution?.has_active && activeExecution.task_id === t.rawId ? (

                            <div className="mt-2 pt-1.5 border-t border-neutral-100 dark:border-neutral-750">

                              <span

                                className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium border ${activeExecution.state === 'waiting_approval'

                                    ? 'bg-amber-50 dark:bg-amber-950/50 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800'

                                    : 'bg-blue-50 dark:bg-blue-950/50 text-blue-800 dark:text-blue-200 border-blue-300 dark:border-blue-800'

                                  }`}

                              >

                                <span

                                  className={`w-1.5 h-1.5 rounded-full shrink-0 animate-ping ${activeExecution.state === 'waiting_approval' ? 'bg-amber-500' : 'bg-blue-500'

                                    }`}

                                />

                                <span className="truncate max-w-[140px]">

                                  {activeExecution.phase_title || 'Выполняется'}

                                </span>

                              </span>

                            </div>

                          ) : ticketRuns[t.rawId] ? (

                            <div className="mt-2 pt-1.5 border-t border-neutral-100 dark:border-neutral-750">

                              {renderTicketRunPill(ticketRuns[t.rawId])}

                            </div>

                          ) : null}

                        </div>

                      ))}

                      {colTickets.length === 0 && (

                        <div className="flex flex-col items-center justify-center h-24 border border-dashed border-neutral-200 dark:border-neutral-800 rounded-md p-3 text-center">

                          <span className="text-[11.5px] text-neutral-400 dark:text-neutral-500">Нет заявок</span>

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

      {/* Инспектор карточки заявки */}

      {selectedTicket && (

        <TicketInspector

          ticket={selectedTicket}

          onClose={() => onSelectTicket(null)}

          onUpdateTicket={onUpdateTicket}

          onToast={onToast}

        />

      )}

      {/* Плавающая панель пакетных действий */}

      {selected.size > 0 && (

        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 px-3.5 py-2 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded-lg shadow-2xl border border-neutral-700 dark:border-neutral-300 animate-in fade-in slide-in-from-bottom-2 duration-150 flex-wrap justify-center">

          <span className="text-[12.5px] font-semibold mr-1 shrink-0 font-mono">

            Выбрано: {selected.size}

          </span>

          <div className="w-px h-4 bg-neutral-700 dark:bg-neutral-300 shrink-0" />

          {processedTab === 'processed' ? (

            <button

              type="button"

              onClick={() => openSmartBatchModal(tickets.filter(t => selected.has(t.id)))}

              disabled={processingBulk}

              className="text-[12px] font-semibold bg-neutral-100 text-neutral-900 hover:bg-white dark:bg-neutral-900 dark:text-neutral-100 dark:hover:bg-neutral-800 px-3 py-1 rounded transition-colors cursor-pointer disabled:opacity-50 flex items-center gap-1.5 shrink-0"

            >

              <IconSparkles size={12} />

              <span>Применить решения ({selected.size})</span>

            </button>

          ) : (

            <button

              type="button"

              onClick={() => {

                const selectedTaskIds = tickets.filter(t => selected.has(t.id)).map(t => t.rawId);

                handleTriggerAnalysis(selectedTaskIds);

              }}

              disabled={isAnalyzing || processingBulk}

              className="text-[12px] font-semibold bg-neutral-100 text-neutral-900 hover:bg-white dark:bg-neutral-900 dark:text-neutral-100 dark:hover:bg-neutral-800 px-3 py-1 rounded transition-colors cursor-pointer disabled:opacity-50 flex items-center gap-1.5 shrink-0"

            >

              <IconRefresh size={12} className={isAnalyzing ? 'animate-spin' : ''} />

              <span>Проанализировать выбранные ({selected.size})</span>

            </button>

          )}

          <button

            type="button"

            onClick={() => initiateBulkAction('take')}

            disabled={processingBulk}

            className="text-[12px] font-medium text-neutral-300 hover:text-white dark:text-neutral-700 dark:hover:text-neutral-900 transition-colors px-2 py-1 cursor-pointer disabled:opacity-50 shrink-0"

          >

            В работу

          </button>

          <button

            type="button"

            onClick={() => initiateBulkAction('cancel')}

            disabled={processingBulk}

            className="text-[12px] font-medium text-neutral-300 hover:text-white dark:text-neutral-700 dark:hover:text-neutral-900 transition-colors px-2 py-1 cursor-pointer disabled:opacity-50 shrink-0"

          >

            Отменить

          </button>

          <div className="w-px h-4 bg-neutral-700 dark:bg-neutral-300 shrink-0" />

          <button

            type="button"

            onClick={() => setSelected(new Set())}

            className="text-neutral-400 hover:text-white dark:hover:text-neutral-900 transition-colors cursor-pointer p-0.5 shrink-0"

            title="Снять выделение"

          >

            ✕

          </button>

        </div>

      )}

      {/* Модальное окно умного подтверждения пакета (100% HITL) */}

      {smartBatchModal && (

        <SmartBatchModal

          items={smartBatchModal.items}

          processingBulk={processingBulk}

          onClose={() => setSmartBatchModal(null)}

          onUpdateItems={setSmartBatchModal}

          onExecute={executeSmartBatch}

        />

      )}

      {/* Модальное окно подтверждения массового действия */}

      {bulkModal && (

        <BulkConfirmModal

          modal={bulkModal}

          processingBulk={processingBulk}

          onClose={() => setBulkModal(null)}

          onConfirm={executeBulkAction}

        />

      )}

    </div>

  );

}

