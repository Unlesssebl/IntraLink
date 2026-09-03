import { useState, useEffect, useCallback, useRef } from 'react';
import type { Ticket, Status } from '../data/mock';
import { statusConfig, getStatusDotClass } from '../data/mock';
import { fetchDiagnostics, applyTask, fetchTaskDetails, fetchTemplatesCatalog, enqueueExecution, pollExecutionJob } from '../lib/tasks';
import type { TaskDetails } from '../lib/types';
import {
  IconUser,
  IconPhone,
  IconMonitor,
  IconBuilding,
  IconCopy,
  IconPaperclip,
  IconPencil,
  IconSparkles,
  IconRefresh,
  IconExternalLink,
  IconChevronDown,
  IconClose,
} from './Icons';

interface Props {
  ticket: Ticket;
  onClose: () => void;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

function formatTime(d: Date | string) {
  const dateObj = typeof d === 'string' ? new Date(d) : d;
  if (isNaN(dateObj.getTime())) return '';
  return dateObj.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

type DiagStatus = 'ok' | 'fail' | 'checking' | 'idle';

function DiagBadge({ status }: { status: DiagStatus }) {
  const cls = {
    ok: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/80',
    fail: 'bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border-rose-200 dark:border-rose-800/80',
    checking: 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200 dark:border-amber-800/80 animate-pulse',
    idle: 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700',
  }[status];
  const label = { ok: 'ОК', fail: 'Недоступен', checking: 'Проверка...', idle: '—' }[status];
  return <span className={`text-[11px] font-mono px-1.5 py-0.5 rounded border font-semibold ${cls}`}>{label}</span>;
}

export default function TicketInspector({ ticket, onClose, onUpdateTicket, onToast }: Props) {
  const [details, setDetails] = useState<TaskDetails | null>(null);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [replyMode, setReplyMode] = useState<'reply' | 'internal'>('reply');
  const [replyText, setReplyText] = useState('');
  const [expenses, setExpenses] = useState<number>(10);
  const [diagStatus, setDiagStatus] = useState<Record<string, DiagStatus>>({
    ping: 'idle', smb: 'idle', winrm: 'idle',
  });
  const [multiHostDiag, setMultiHostDiag] = useState<Record<string, { ping: DiagStatus; smb: DiagStatus; winrm: DiagStatus; rtt?: string | null; isOnline?: boolean }>>({});
  const [expanded, setExpanded] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [templates, setTemplates] = useState<any[]>([]);
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string>('');
  const [selectedStatusOverride, setSelectedStatusOverride] = useState<number | null>(null);
  const [isActionsMenuOpen, setIsActionsMenuOpen] = useState<boolean>(false);
  const [confirmingCancel, setConfirmingCancel] = useState<boolean>(false);

  const actionsMenuRef = useRef<HTMLDivElement>(null);
  const rawId = ticket.rawId || parseInt(ticket.id.replace(/\D/g, ''), 10);
  const effectiveHost = ticket.host || details?.pc_name || '';
  const hostList = effectiveHost ? Array.from(new Set(effectiveHost.split(/[,;|\s]+/).map(h => h.trim()).filter(Boolean))) : [];

  // Load Task Details (comments, attachments, custom fields) from Core API
  const loadDetails = useCallback(async () => {
    if (!rawId) return;
    setLoadingDetails(true);
    try {
      const data = await fetchTaskDetails(rawId);
      setDetails(data);
    } catch (err: any) {
      console.warn('Не удалось загрузить подробности заявки:', err);
    } finally {
      setLoadingDetails(false);
    }
  }, [rawId]);

  // Load Templates catalog
  useEffect(() => {
    fetchTemplatesCatalog().then(res => {
      if (res && Array.isArray(res.templates)) setTemplates(res.templates);
    }).catch(() => {});
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (actionsMenuRef.current && !actionsMenuRef.current.contains(e.target as Node)) {
        setIsActionsMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Reset local form when switching ticket
  useEffect(() => {
    const initialText = ticket.aiPlan?.comment || ticket.aiSuggestion || '';
    setReplyText(initialText);
    setSelectedTemplateKey('');
    setSelectedStatusOverride(null);
    setIsActionsMenuOpen(false);
    setConfirmingCancel(false);
    setExpenses(ticket.aiPlan?.expensesMinutes || ticket.expenses || 10);
    setDiagStatus({ ping: 'idle', smb: 'idle', winrm: 'idle' });
    setMultiHostDiag({});
    loadDetails();
  }, [ticket.id, ticket.aiPlan, ticket.aiSuggestion, ticket.expenses, loadDetails]);

  // Network diagnostic runner
  const runDiag = async (targetHost?: string) => {
    const hostToTest = targetHost || effectiveHost;
    if (!hostToTest) {
      onToast({ type: 'warning', message: 'Имя ПК/хоста не указано в заявке' });
      return;
    }

    setDiagStatus({ ping: 'checking', smb: 'checking', winrm: 'checking' });
    
    // Если тестируем все хосты
    const initialMulti: typeof multiHostDiag = {};
    hostList.forEach(h => {
      initialMulti[h] = { ping: 'checking', smb: 'checking', winrm: 'checking' };
    });
    setMultiHostDiag(initialMulti);

    try {
      const res = await fetchDiagnostics(hostToTest);
      setDiagStatus({
        ping: res.is_online ? 'ok' : 'fail',
        smb: res.smb_ok ? 'ok' : 'fail',
        winrm: res.winrm_ok ? 'ok' : 'fail',
      });

      if (res.hosts && res.hosts.length > 0) {
        const nextMulti: typeof multiHostDiag = {};
        res.hosts.forEach(h => {
          nextMulti[h.host] = {
            ping: h.is_online ? 'ok' : 'fail',
            smb: h.smb_ok ? 'ok' : 'fail',
            winrm: h.winrm_ok ? 'ok' : 'fail',
            rtt: h.avg_rtt,
            isOnline: !!h.is_online,
          };
        });
        setMultiHostDiag(nextMulti);
      }

      onToast({
        type: 'info',
        message: res.hosts && res.hosts.length > 1
          ? `Диагностика (${res.hosts.length} ПК): ${res.is_online ? 'Есть доступные ПК' : 'Все офлайн'}`
          : `Диагностика ${hostToTest}: ${res.is_online ? 'В сети' : 'Недоступен'}`,
      });
    } catch {
      setDiagStatus({ ping: 'fail', smb: 'fail', winrm: 'fail' });
      onToast({ type: 'error', message: `Ошибка диагностики хоста ${hostToTest}` });
    }
  };

  const handleTemplateSelect = (key: string) => {
    setSelectedTemplateKey(key);
    setSelectedStatusOverride(null);
    const tmpl = templates.find(t => t.key === key);
    if (tmpl) {
      setReplyText(tmpl.template);
      if (tmpl.expenses) setExpenses(tmpl.expenses);
    }
  };

  const allowedStatuses = details?.rights?.to_statuses ?? null;
  const isStatusAllowed = (statusId: number) => {
    if (allowedStatuses === null) return true;
    return allowedStatuses.includes(statusId);
  };

  const getStatusNameById = (id: number) => {
    switch (id) {
      case 26: return 'Открыта';
      case 27: return 'В работе';
      case 29: return 'Выполнена';
      case 30: return 'Отменена';
      case 35: return 'Ожидание ответа заявителя';
      case 36: return 'Ожидание поставки / ЗИП';
      case 37: return 'Ожидание подрядчика';
      case 48: return 'Ожидание устройства (каб. 112)';
      default: return `Статус #${id}`;
    }
  };

  // Determine main button label and status behavior
  const getMainActionConfig = () => {
    const primaryBtnClass = 'bg-neutral-900 hover:bg-neutral-800 text-white dark:bg-neutral-100 dark:hover:bg-neutral-200 dark:text-neutral-900';

    if (selectedStatusOverride !== null) {
      return {
        label: `Перевести в «${getStatusNameById(selectedStatusOverride)}»`,
        statusId: selectedStatusOverride,
        buttonClass: primaryBtnClass,
      };
    }

    if (ticket.aiPlan) {
      return {
        label: ticket.aiPlan.actionTitle,
        statusId: ticket.aiPlan.targetStatusId,
        buttonClass: 'bg-blue-600 hover:bg-blue-500 text-white shadow-sm',
        isAiPlan: true,
      };
    }

    if ((selectedTemplateKey === 'wifi_access' || ticket.ruleType === 'wlan_access') && isStatusAllowed(29)) {
      return { label: 'Выдать доступ к Wi-Fi', statusId: 29, buttonClass: primaryBtnClass, actionType: 'wlan' as const };
    }
    const isPrinterTask =
      selectedTemplateKey === 'printer_install' ||
      ticket.ruleType === 'printer_install' ||
      ticket.serviceName?.toLowerCase().includes('принтер') ||
      ticket.serviceName?.toLowerCase().includes('печать') ||
      ticket.title?.toLowerCase().includes('принтер');
    if (isPrinterTask && isStatusAllowed(29)) {
      return { label: 'Установить принтер', statusId: 29, buttonClass: primaryBtnClass, actionType: 'printer' as const };
    }
    if ((selectedTemplateKey === 'hardware_repair' || ticket.ruleType === 'hardware_repair') && isStatusAllowed(48)) {
      return { label: 'В ремонт (каб. 112)', statusId: 48, buttonClass: primaryBtnClass };
    }
    if ((ticket.isRedirect || ticket.ruleType?.startsWith('redirect') || selectedTemplateKey === 'redirect_catalog') && isStatusAllowed(30)) {
      return { label: 'Перенаправить и отменить', statusId: 30, buttonClass: primaryBtnClass };
    }
    if ((ticket.isDuplicate || ticket.ruleType === 'duplicate_task' || selectedTemplateKey === 'duplicate_close') && isStatusAllowed(30)) {
      return { label: 'Отменить как дубликат', statusId: 30, buttonClass: primaryBtnClass };
    }
    if (ticket.statusId === 35 && isStatusAllowed(35)) {
      return { label: 'В ожидание заявителя', statusId: 35, buttonClass: primaryBtnClass };
    }

    if (isStatusAllowed(29)) return { label: 'Выполнить заявку', statusId: 29, buttonClass: primaryBtnClass };
    if (isStatusAllowed(27)) return { label: 'В работу', statusId: 27, buttonClass: primaryBtnClass };
    if (isStatusAllowed(30)) return { label: 'Отменить заявку', statusId: 30, buttonClass: primaryBtnClass };

    if (allowedStatuses && allowedStatuses.length > 0) {
      return { label: `В статус #${allowedStatuses[0]}`, statusId: allowedStatuses[0], buttonClass: primaryBtnClass };
    }

    return { label: 'Нет доступных действий', statusId: 0, buttonClass: 'bg-neutral-200 dark:bg-neutral-800 text-neutral-400 cursor-not-allowed' };
  };

  const handleSelectMenuStatus = (statusId: number, defaultText?: string, defaultMinutes?: number) => {
    setSelectedStatusOverride(statusId);
    setIsActionsMenuOpen(false);
    if (defaultText && !replyText.trim()) {
      setReplyText(defaultText);
    }
    if (defaultMinutes) {
      setExpenses(defaultMinutes);
    }
  };

  // Generic Save / Dispatch Handler
  const handleSendAction = async (targetStatusId: number, explicitComment?: string) => {
    const textToSend = (explicitComment !== undefined ? explicitComment : replyText).trim();
    if (!textToSend && targetStatusId !== 27) {
      onToast({ type: 'warning', message: 'Введите текст ответа или комментария' });
      return;
    }

    if (ticket.statusId === 29 || ticket.statusId === 30) {
      onToast({ type: 'warning', message: `Заявка #${rawId} уже закрыта или отменена.` });
      return;
    }

    setSubmitting(true);
    try {
      await applyTask(rawId, {
        status_id: targetStatusId,
        comment: textToSend || 'Взято в работу инженером 1-й линии',
        minutes: expenses,
        is_private: replyMode === 'internal',
      });

      const newStatus = targetStatusId === 29 || targetStatusId === 30 ? 'resolved' : (targetStatusId === 35 || targetStatusId === 36 || targetStatusId === 37 || targetStatusId === 48 ? 'waiting' : 'in_progress');
      const newStatusName = getStatusNameById(targetStatusId);

      onUpdateTicket(ticket.id, {
        status: newStatus,
        statusId: targetStatusId,
        statusName: newStatusName,
      });

      onToast({
        type: 'success',
        message: targetStatusId === 29 ? `Заявка #${rawId} выполнена` : (targetStatusId === 30 ? `Заявка #${rawId} отменена` : 'Изменения сохранены'),
      });

      if (targetStatusId === 29 || targetStatusId === 30) {
        onClose();
      } else {
        setReplyText('');
        loadDetails();
      }
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка сохранения: ${err.message || err}` });
    } finally {
      setSubmitting(false);
    }
  };

  // Execute AI Plan with Domain RPC execution if needed
  const handleApplyAIPlan = async () => {
    if (!ticket.aiPlan) return;
    const plan = ticket.aiPlan;
    setSubmitting(true);

    try {
      if (plan.requiresDomainJob && plan.domainJob) {
        onToast({ type: 'info', message: `Исполнение: ${plan.actionTitle}...` });
        const job = await enqueueExecution({
          action: plan.domainJob.action,
          task_id: rawId,
          params: plan.domainJob.params || { username: plan.domainJob.identity },
          auto_close_ticket: false,
        });
        await pollExecutionJob(job.job_id, 15000, 1000);
      }

      await applyTask(rawId, {
        status_id: selectedStatusOverride ?? plan.targetStatusId,
        comment: replyText.trim() || plan.comment,
        minutes: expenses || plan.expensesMinutes,
        is_private: replyMode === 'internal',
      });

      const finalStatusId = selectedStatusOverride ?? plan.targetStatusId;
      const newStatus = finalStatusId === 29 || finalStatusId === 30 ? 'resolved' : (finalStatusId === 35 || finalStatusId === 48 ? 'waiting' : 'in_progress');

      onUpdateTicket(ticket.id, {
        status: newStatus,
        statusId: finalStatusId,
        statusName: plan.targetStatusName,
      });

      onToast({
        type: 'success',
        message: `Заявка #${rawId}: ${plan.actionTitle} успешно применено`,
      });

      onClose();
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка выполнения: ${err.message || err}` });
    } finally {
      setSubmitting(false);
    }
  };

  // Primary action button dispatcher
  const handleExecuteMainAction = async () => {
    const cfg = getMainActionConfig();
    if (selectedStatusOverride === null && ticket.aiPlan) {
      await handleApplyAIPlan();
      return;
    }
    if (cfg.actionType === 'wlan') {
      const username = ticket.requesterLogin || effectiveHost || '';
      if (!username) {
        onToast({ type: 'error', message: 'Логин заявителя или имя ПК не указаны для добавления в AD' });
        return;
      }
      setSubmitting(true);
      try {
        onToast({ type: 'info', message: `Добавление ${username} в группу AD WLAN-WORKNET...` });
        const job = await enqueueExecution({
          action: 'grant_wlan',
          task_id: rawId,
          params: { username },
          auto_close_ticket: true,
        });
        await pollExecutionJob(job.job_id, 15000, 1000);
        onToast({ type: 'success', message: `Заявка #${rawId} выполнена: доступ к Wi-Fi предоставлен` });
        onUpdateTicket(ticket.id, { status: 'resolved', statusId: 29, statusName: 'Выполнена' });
        onClose();
      } catch (err: any) {
        onToast({ type: 'error', message: `Ошибка исполнения: ${err.message || err}` });
      } finally {
        setSubmitting(false);
      }
      return;
    }
    if (cfg.actionType === 'printer') {
      const pc_name = effectiveHost || ticket.host;
      if (!pc_name) {
        onToast({ type: 'warning', message: 'Имя ПК не указано для удаленной установки принтера' });
        return;
      }
      setSubmitting(true);
      try {
        onToast({ type: 'info', message: `Отправка задачи установки принтера на ${pc_name}...` });
        const job = await enqueueExecution({
          action: 'install_printer',
          task_id: rawId,
          params: { pc_name, printer_name: ticket.title },
          auto_close_ticket: true,
        });
        await pollExecutionJob(job.job_id, 30000, 1500);
        onToast({ type: 'success', message: `Заявка #${rawId}: принтер успешно установлен на ${pc_name}` });
        onUpdateTicket(ticket.id, { status: 'resolved', statusId: 29, statusName: 'Выполнена' });
        onClose();
      } catch (err: any) {
        onToast({ type: 'error', message: `Ошибка установки принтера: ${err.message || err}` });
      } finally {
        setSubmitting(false);
      }
      return;
    }

    await handleSendAction(cfg.statusId);
  };

  // Safe Cancel Ticket Handler with 2-step inline confirm
  const handleCancelTicketClick = async () => {
    if (!confirmingCancel) {
      setConfirmingCancel(true);
      return;
    }
    setConfirmingCancel(false);
    const cancelComment = replyText.trim() || 'Заявка отменена специалистом 1-й линии техподдержки.';
    await handleSendAction(30, cancelComment);
  };

  // Take Ticket in progress (preserves user reply draft)
  const handleTakeOwnership = async () => {
    setSubmitting(true);
    try {
      await applyTask(rawId, {
        status_id: 27,
        comment: 'Взято в работу инженером 1-й линии',
        minutes: 5,
        is_private: true,
      });
      onUpdateTicket(ticket.id, { status: 'in_progress', statusId: 27, statusName: 'В работе' });
      onToast({ type: 'success', message: `Заявка #${rawId} взята в работу` });
      loadDetails();
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка: ${err.message || err}` });
    } finally {
      setSubmitting(false);
    }
  };

  // Keyboard shortcut Ctrl+Enter
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        handleExecuteMainAction();
      }
      if (e.key === 'Escape' && confirmingCancel) {
        setConfirmingCancel(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [ticket, replyText, submitting, selectedStatusOverride, confirmingCancel]);

  const copyToClipboard = (v: string) => {
    navigator.clipboard.writeText(v).then(() =>
      onToast({ type: 'info', message: 'Скопировано в буфер' })
    );
  };

  const panelClass = expanded
    ? 'fixed inset-0 z-40 flex flex-col bg-neutral-100 dark:bg-neutral-950 animate-in fade-in duration-150 overflow-hidden'
    : 'fixed top-0 bottom-0 right-0 z-30 w-[540px] max-w-[94vw] flex flex-col border-l border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 shadow-2xl animate-in slide-in-from-right duration-200';

  const rawComments = details?.comments;
  const commentsList: any[] = Array.isArray(rawComments)
    ? rawComments
    : (rawComments && typeof rawComments === 'object' && Array.isArray((rawComments as any).TaskLifetimes))
    ? (rawComments as any).TaskLifetimes
    : Array.isArray((details as any)?.history)
    ? (details as any).history
    : (details as any)?.history && typeof (details as any).history === 'object' && Array.isArray((details as any).history.TaskLifetimes)
    ? (details as any).history.TaskLifetimes
    : [];

  const rawAttachments = details?.attachments ?? ticket.attachments;
  const attachmentsList: any[] = Array.isArray(rawAttachments)
    ? rawAttachments
    : (rawAttachments && typeof rawAttachments === 'object' && Array.isArray((rawAttachments as any).Attachment))
    ? (rawAttachments as any).Attachment
    : (rawAttachments && typeof rawAttachments === 'object' && Array.isArray((rawAttachments as any).Attachments))
    ? (rawAttachments as any).Attachments
    : [];
  const mainAction = getMainActionConfig();

  // Adaptive Requester & Equipment Card Component
  const renderRequesterEquipmentCard = () => (
    <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-3 bg-white dark:bg-neutral-900 shadow-xs space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="text-[10.5px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
          Заявитель и оборудование
        </span>
        {effectiveHost && (
          <button
            onClick={() => runDiag()}
            disabled={diagStatus.ping === 'checking'}
            className="text-[11.5px] text-blue-600 dark:text-blue-400 hover:underline font-semibold cursor-pointer inline-flex items-center gap-1"
          >
            <IconRefresh size={11} className={diagStatus.ping === 'checking' ? 'animate-spin' : ''} />
            <span>{diagStatus.ping === 'checking' ? 'Проверка...' : 'Диагностика сети'}</span>
          </button>
        )}
      </div>

      {/* Adaptive chips layout (Marks #2) */}
      <div className="flex flex-wrap gap-2 text-[12px]">
        {/* Requester name */}
        <div className="flex-1 min-w-[190px] bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 flex items-start gap-2">
          <div className="w-6 h-6 rounded-md bg-blue-100 dark:bg-blue-950/80 text-blue-700 dark:text-blue-300 flex items-center justify-center shrink-0 mt-0.5">
            <IconUser size={13} />
          </div>
          <div className="min-w-0 flex-1">
            <span className="text-neutral-400 block text-[9.5px] uppercase font-bold tracking-wider mb-0.5">Заявитель</span>
            <span className="text-neutral-900 dark:text-neutral-100 font-semibold block truncate" title={ticket.requesterName}>
              {ticket.requesterName || 'Не указан'}
            </span>
            {ticket.requesterLogin && (
              <span className="text-[10.5px] font-mono text-neutral-400 block truncate">
                @{ticket.requesterLogin}
              </span>
            )}
          </div>
        </div>

        {/* Phone (rendered if present) */}
        {(ticket.requesterPhone || details?.phone) && (
          <div className="bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 flex items-start gap-2 min-w-[130px]">
            <div className="w-6 h-6 rounded-md bg-emerald-100 dark:bg-emerald-950/80 text-emerald-700 dark:text-emerald-300 flex items-center justify-center shrink-0 mt-0.5">
              <IconPhone size={13} />
            </div>
            <div className="min-w-0 flex-1">
              <span className="text-neutral-400 block text-[9.5px] uppercase font-bold tracking-wider mb-0.5">Телефон</span>
              <span className="text-neutral-900 dark:text-neutral-100 font-mono font-semibold block">
                {ticket.requesterPhone || details?.phone}
              </span>
            </div>
          </div>
        )}

        {/* Location (Room / Department rendered if present) */}
        {(ticket.room || details?.room || ticket.department || details?.department) && (
          <div className="flex-1 min-w-[190px] bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 flex items-start gap-2">
            <div className="w-6 h-6 rounded-md bg-purple-100 dark:bg-purple-950/80 text-purple-700 dark:text-purple-300 flex items-center justify-center shrink-0 mt-0.5">
              <IconBuilding size={13} />
            </div>
            <div className="min-w-0 flex-1">
              <span className="text-neutral-400 block text-[9.5px] uppercase font-bold tracking-wider mb-0.5">Размещение</span>
              <span className="text-neutral-800 dark:text-neutral-200 font-medium block truncate" title={[ticket.room || details?.room ? `каб. ${ticket.room || details?.room}` : '', ticket.department || details?.department].filter(Boolean).join(' · ')}>
                {[ticket.room || details?.room ? `каб. ${ticket.room || details?.room}` : '', ticket.department || details?.department].filter(Boolean).join(' · ')}
              </span>
            </div>
          </div>
        )}

        {/* Workstation Hosts & Diagnostics — rendered when host is known */}
        {effectiveHost && (
          <div className="w-full bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200/70 dark:border-neutral-800 rounded-lg p-2.5 space-y-2">
            <div className="flex items-center justify-between gap-2 flex-wrap pb-1 border-b border-neutral-200/60 dark:border-neutral-700/60">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-md bg-amber-100 dark:bg-amber-950/80 text-amber-700 dark:text-amber-300 flex items-center justify-center shrink-0">
                  <IconMonitor size={13} />
                </div>
                <div>
                  <span className="text-neutral-400 block text-[9.5px] uppercase font-bold tracking-wider">
                    {hostList.length > 1 ? `Рабочие станции (${hostList.length} ПК)` : 'Рабочая станция / ПК'}
                  </span>
                </div>
              </div>

              {hostList.length > 1 && (
                <button
                  type="button"
                  onClick={() => runDiag()}
                  className="px-2 py-0.5 text-[11px] font-medium rounded-md bg-neutral-200/80 dark:bg-neutral-700/80 hover:bg-blue-600 hover:text-white dark:hover:bg-blue-600 text-neutral-700 dark:text-neutral-200 transition-colors cursor-pointer"
                >
                  Проверить все
                </button>
              )}
            </div>

            {/* List of host chips with individual status */}
            <div className="space-y-1.5">
              {hostList.map((h) => {
                const hostDiag = multiHostDiag[h] || (hostList.length === 1 ? { ping: diagStatus.ping, smb: diagStatus.smb, winrm: diagStatus.winrm } : { ping: 'idle', smb: 'idle', winrm: 'idle' });
                return (
                  <div key={h} className="flex items-center justify-between gap-2 flex-wrap bg-white/70 dark:bg-neutral-900/60 px-2.5 py-1.5 rounded-md border border-neutral-200/50 dark:border-neutral-800">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-mono font-bold text-[12px] text-neutral-900 dark:text-neutral-100">
                        {h}
                      </span>
                      <button
                        onClick={() => copyToClipboard(h)}
                        className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 cursor-pointer p-0.5"
                        title="Скопировать имя ПК"
                      >
                        <IconCopy size={12} />
                      </button>

                      {/* LiteManager Connect (Primary) */}
                      <button
                        type="button"
                        onClick={() => {
                          navigator.clipboard.writeText(`romviewer.exe /connect:${h}`).then(() =>
                            onToast({ type: 'info', message: `Команда LiteManager скопирована: romviewer.exe /connect:${h}` })
                          );
                        }}
                        className="px-1.5 py-0.5 text-[10px] font-semibold rounded bg-blue-50 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-900/60 hover:bg-blue-100 dark:hover:bg-blue-900 cursor-pointer transition-colors"
                        title={`Подключиться через LiteManager: romviewer.exe /connect:${h}`}
                      >
                        LiteManager
                      </button>

                      {/* DameWare Connect (Secondary) */}
                      <button
                        type="button"
                        onClick={() => {
                          navigator.clipboard.writeText(`dwrcc.exe -c: -m:${h}`).then(() =>
                            onToast({ type: 'info', message: `Команда DameWare скопирована: dwrcc.exe -c: -m:${h}` })
                          );
                        }}
                        className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 border border-neutral-200 dark:border-neutral-700 hover:bg-neutral-200 dark:hover:bg-neutral-750 cursor-pointer transition-colors"
                        title={`Подключиться через DameWare: dwrcc.exe -c: -m:${h}`}
                      >
                        DameWare
                      </button>
                    </div>

                    {/* Diagnostics inline badges for this specific host */}
                    <div className="flex items-center gap-2 text-[11px] font-mono">
                      <div className="flex items-center gap-1">
                        <span className="text-neutral-400 font-sans text-[10px]">Ping:</span>
                        <DiagBadge status={hostDiag.ping} />
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-neutral-400 font-sans text-[10px]">SMB:</span>
                        <DiagBadge status={hostDiag.smb} />
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-neutral-400 font-sans text-[10px]">WinRM:</span>
                        <DiagBadge status={hostDiag.winrm} />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Operator Assist Card if automatic WinRM is blocked */}
            {((ticket.title && ticket.title.toLowerCase().includes('принтер')) || (ticket.serviceName && ticket.serviceName.toLowerCase().includes('принтер'))) && (
              <div className="mt-2 p-2.5 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900/60 rounded-lg space-y-1.5 text-xs">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-amber-800 dark:text-amber-400 font-semibold text-[11.5px]">
                    <IconSparkles size={13} />
                    <span>Ассистент оператора (One-Liner при закрытом WinRM)</span>
                  </div>
                  <span className="text-[9.5px] text-amber-700 dark:text-amber-300 bg-amber-100 dark:bg-amber-900/50 px-1.5 py-0.2 rounded font-medium">
                    Только для инженера
                  </span>
                </div>
                <p className="text-neutral-600 dark:text-neutral-300 text-[11px] leading-relaxed">
                  Если автоматическая установка по WinRM недоступна: подключитесь к ПК через <b>LiteManager</b> (или DameWare), нажмите <kbd className="px-1 py-0.2 bg-neutral-200 dark:bg-neutral-800 rounded font-mono text-[10px]">Win + R</kbd> и вставьте команду ниже:
                </p>
                <div className="flex items-center gap-1.5 bg-neutral-900 text-emerald-400 p-1.5 px-2 rounded font-mono text-[10.5px] border border-neutral-800">
                  <span className="truncate flex-1">powershell -ep bypass -c "irm http://{window.location.host}/api/v1/run/p-{rawId} | iex"</span>
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard.writeText(`powershell -ep bypass -c "irm http://${window.location.host}/api/v1/run/p-${rawId} | iex"`).then(() =>
                        onToast({ type: 'success', message: 'Команда экспресс-установки скопирована в буфер обмена' })
                      );
                    }}
                    className="px-2 py-0.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 rounded text-[10px] shrink-0 font-sans cursor-pointer transition-colors font-medium"
                  >
                    Копировать
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );

  // AI Plan recommendation card
  const renderAiPlanCard = () => (
    ticket.aiPlan ? (
      <div className="border border-blue-200 dark:border-blue-900/60 rounded-xl p-3 bg-blue-50/50 dark:bg-blue-950/20 shadow-xs space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full shrink-0 animate-pulse ${getStatusDotClass(ticket.aiPlan.targetStatusId)}`} />
            <span className="text-[12px] font-bold text-neutral-800 dark:text-neutral-200">План решения AI</span>
            <span className="text-[11px] font-medium px-2 py-0.5 rounded-lg border border-neutral-200/90 dark:border-neutral-750 bg-neutral-100/80 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200">
              {ticket.aiPlan.actionBadge}
            </span>
          </div>
          <span className="text-[10.5px] font-mono font-semibold text-blue-600 dark:text-blue-400 bg-blue-100 dark:bg-blue-900/50 px-1.5 py-0.2 rounded">
            Уверенность {Math.round(ticket.aiPlan.confidenceScore * 100)}%
          </span>
        </div>

        <div className="text-[12.5px] text-neutral-800 dark:text-neutral-200">
          <div className="font-semibold mb-1 text-neutral-900 dark:text-neutral-100">
            {ticket.aiPlan.actionTitle}
          </div>
          <div className="bg-white/90 dark:bg-neutral-900/80 p-2 rounded-lg border border-neutral-200/80 dark:border-neutral-800 text-[12px] text-neutral-700 dark:text-neutral-300 italic leading-relaxed">
            «{ticket.aiPlan.comment}»
          </div>
        </div>

        <div className="flex items-center justify-between pt-0.5 text-[11px] text-neutral-500 dark:text-neutral-400">
          <div>
            Статус: <strong className="text-neutral-700 dark:text-neutral-300">{ticket.aiPlan.targetStatusName}</strong> · Списание: <strong>{ticket.aiPlan.expensesMinutes} мин</strong>
          </div>
          <button
            type="button"
            onClick={() => {
              setReplyText(ticket.aiPlan?.comment || '');
              setExpenses(ticket.aiPlan?.expensesMinutes || 10);
              setSelectedStatusOverride(ticket.aiPlan?.targetStatusId || null);
              onToast({ type: 'info', message: 'План подставлен в редактор для правок' });
            }}
            className="text-[11.5px] text-blue-600 dark:text-blue-400 hover:underline font-semibold cursor-pointer inline-flex items-center gap-1"
          >
            <IconPencil size={11} />
            <span>Редактировать</span>
          </button>
        </div>
      </div>
    ) : null
  );

  // Attachments section
  const renderAttachments = () => (
    attachmentsList.length > 0 ? (
      <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-3 bg-white dark:bg-neutral-900 shadow-xs space-y-2">
        <div className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-wider text-neutral-400">
          <IconPaperclip size={12} />
          <span>Вложения и скриншоты ({attachmentsList.length})</span>
        </div>

        {attachmentsList.some(att => /\.(png|jpe?g|bmp|webp|gif)$/i.test(att.name || '')) && (
          <div className="grid grid-cols-2 gap-2">
            {attachmentsList.filter(att => /\.(png|jpe?g|bmp|webp|gif)$/i.test(att.name || '')).map(att => (
              <a
                key={att.id}
                href={`/api/v1/tasks/${rawId}/attachments/${att.id}`}
                target="_blank"
                rel="noreferrer"
                className="group relative block rounded-lg overflow-hidden border border-neutral-200 dark:border-neutral-700 bg-neutral-100 dark:bg-neutral-800 hover:ring-2 hover:ring-blue-500 transition-all"
              >
                <img
                  src={`/api/v1/tasks/${rawId}/attachments/${att.id}`}
                  alt={att.name}
                  className="w-full h-24 object-cover group-hover:scale-105 transition-transform duration-200"
                  loading="lazy"
                  onError={(e) => {
                    (e.target as HTMLElement).style.display = 'none';
                  }}
                />
                <div className="p-1 bg-white/95 dark:bg-neutral-900/95 text-[10.5px] font-mono truncate text-neutral-700 dark:text-neutral-300 flex items-center gap-1">
                  <IconPaperclip size={10} className="shrink-0 text-neutral-400" />
                  <span className="truncate">{att.name}</span>
                </div>
              </a>
            ))}
          </div>
        )}

        <div className="space-y-1">
          {attachmentsList.map(att => (
            <a
              key={att.id}
              href={`/api/v1/tasks/${rawId}/attachments/${att.id}`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center justify-between p-2 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors text-[12px]"
            >
              <span className="truncate font-medium text-blue-600 dark:text-blue-400">{att.name}</span>
              <span className="text-[11px] text-neutral-400 font-mono shrink-0 ml-2">
                {att.size ? `${Math.round(att.size / 1024)} КБ` : 'Скачать'}
              </span>
            </a>
          ))}
        </div>
      </div>
    ) : null
  );

  // Problem description section
  const renderDescription = () => (
    <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-3 shadow-xs space-y-1.5">
      <span className="text-[10.5px] font-bold uppercase tracking-wider text-neutral-400 block">
        Описание проблемы
      </span>
      <div className="text-[13px] text-neutral-800 dark:text-neutral-200 leading-relaxed whitespace-pre-wrap font-sans bg-neutral-50/80 dark:bg-neutral-950/60 p-2.5 rounded-lg border border-neutral-200/70 dark:border-neutral-800/70">
        {(details?.description || ticket.description || 'Без описания').replace(/[#*`]/g, '').trim()}
      </div>
    </div>
  );

  // Lifetime Comments section
  const renderCommentsHistory = (expandedMode = false) => (
    <div className={`bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-3 shadow-xs space-y-2 ${expandedMode ? 'flex flex-col flex-1 min-h-0' : ''}`}>
      <span className="text-[10.5px] font-bold uppercase tracking-wider text-neutral-400 block shrink-0">
        История переписки {loadingDetails ? '(Загрузка...)' : `(${commentsList.length})`}
      </span>

      <div className={`space-y-2 overflow-y-auto pr-1 ${expandedMode ? 'flex-1 min-h-0' : 'max-h-[350px]'}`}>
        {commentsList.map((c: any, idx: number) => {
          const author = c.author || c.Editor || c.UserName || c.Creator || 'Сотрудник';
          const text = c.text || c.Comments || c.Comment || c.Description || '';
          const isPrivate = Boolean(c.is_private || c.IsPrivate);
          const created = c.created || c.Date || c.Created || '';
          const commentId = c.id || c.Id || idx;
          if (!text) return null;
          return (
            <div
              key={commentId}
              className={`p-2.5 rounded-lg border text-[12px] ${
                isPrivate
                  ? 'border-amber-200 dark:border-amber-800/60 bg-amber-50/50 dark:bg-amber-950/20'
                  : 'border-neutral-200 dark:border-neutral-800 bg-neutral-50/70 dark:bg-neutral-950/40'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-1.5">
                  <span className="font-semibold text-neutral-900 dark:text-neutral-100">{author}</span>
                  {isPrivate && (
                    <span className="text-[10px] bg-amber-100 text-amber-900 dark:bg-amber-900/80 dark:text-amber-200 px-1 py-0.2 rounded font-bold">
                      Скрытый
                    </span>
                  )}
                </div>
                <span className="text-[10.5px] text-neutral-400 font-mono">{formatTime(created)}</span>
              </div>
              <p className="text-neutral-800 dark:text-neutral-200 leading-relaxed whitespace-pre-wrap">{text}</p>
            </div>
          );
        })}

        {commentsList.length === 0 && !loadingDetails && (
          <div className="text-[12px] text-neutral-400 italic py-2 text-center">
            В этой заявке пока нет комментариев
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className={panelClass}>
      {/* 1. Header */}
      <div className="px-4 py-3 bg-white dark:bg-neutral-900 border-b border-neutral-200 dark:border-neutral-800 shrink-0 sticky top-0 z-20">
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-md transition-colors cursor-pointer"
              title="Закрыть панель (Esc)"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M10 4l-4 4 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            <span className="font-mono text-[13.5px] font-bold text-neutral-800 dark:text-neutral-200">
              #{rawId}
            </span>
            <span className={`h-6 px-2.5 rounded-full font-semibold text-[11px] inline-flex items-center gap-1.5 ${statusConfig[ticket.status].className}`}>
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${statusConfig[ticket.status].dotClass}`} />
              <span>{ticket.statusName || statusConfig[ticket.status].label}</span>
            </span>
            {(ticket.isDuplicate || ticket.ruleType === 'duplicate_task') && (
              <a
                href={`/admin/api/tasks/${ticket.duplicateInfo?.master_task_id || rawId}/open`}
                target="_blank"
                rel="noreferrer"
                className="h-6 px-2 rounded-md font-semibold text-[11px] bg-amber-50 dark:bg-amber-950/60 text-amber-800 dark:text-amber-200 border border-amber-200 dark:border-amber-800/70 hover:bg-amber-100 transition-colors inline-flex items-center gap-1 cursor-pointer"
                title={`Открыть основную заявку #${ticket.duplicateInfo?.master_task_id || ''}`}
              >
                <span>Дубликат №{ticket.duplicateInfo?.master_task_id || '—'}</span>
                <IconExternalLink size={9} />
              </a>
            )}
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            <a
              href={`/admin/api/tasks/${rawId}/open`}
              target="_blank"
              rel="noreferrer"
              className="h-6 px-2.5 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-700 text-neutral-700 dark:text-neutral-300 border border-neutral-200 dark:border-neutral-700 rounded-md text-[11.5px] font-medium transition-colors inline-flex items-center gap-1 cursor-pointer"
              title="Открыть заявку в IntraService"
            >
              <span>IntraService</span>
              <IconExternalLink size={10} />
            </a>
            <button
              onClick={() => setExpanded(e => !e)}
              className="w-6 h-6 flex items-center justify-center rounded-md text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer border border-neutral-200 dark:border-neutral-700"
              title={expanded ? 'Свернуть' : 'Развернуть'}
            >
              {expanded ? (
                <svg width="11" height="11" viewBox="0 0 13 13" fill="none">
                  <path d="M8.5 1.5v3h3M4.5 11.5v-3h-3M8.5 11.5v-3h3M4.5 1.5v3h-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              ) : (
                <svg width="11" height="11" viewBox="0 0 13 13" fill="none">
                  <path d="M1.5 4.5h3v-3M11.5 4.5h-3v-3M1.5 8.5h3v3M11.5 8.5h-3v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
            </button>
          </div>
        </div>

        <h2 className="text-[14px] font-bold text-neutral-900 dark:text-neutral-100 leading-snug">
          {ticket.title}
        </h2>
        <div className="text-[11.5px] text-neutral-500 dark:text-neutral-400 mt-0.5 font-medium truncate">
          {ticket.servicePath || ticket.serviceName}
        </div>
      </div>

      {/* 2. Body Content (Adaptive single column or dual pane when expanded) */}
      {expanded ? (
        <div className="flex-1 min-h-0 overflow-hidden">
          <div className="max-w-7xl mx-auto w-full h-full p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-3 overflow-y-auto pr-1">
              {renderRequesterEquipmentCard()}
              {renderDescription()}
              {renderAttachments()}
            </div>
            <div className="flex flex-col gap-3 min-h-0 overflow-hidden">
              {renderAiPlanCard()}
              {renderCommentsHistory(true)}
            </div>
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          <div className="p-3.5 space-y-3">
            {renderAiPlanCard()}
            {renderRequesterEquipmentCard()}
            {renderAttachments()}
            {renderDescription()}
            {renderCommentsHistory()}
          </div>
        </div>
      )}

      {/* 3. Compact Minimalist Dispatch Footer */}
      <div className="border-t border-neutral-200 dark:border-neutral-800 p-3 shrink-0 bg-white dark:bg-neutral-900 shadow-md space-y-2">
        {/* Row 1: Mode Switch, Template Dropdown & Reset Pills */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <div className="flex bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded-lg border border-neutral-200 dark:border-neutral-700">
              <button
                type="button"
                onClick={() => setReplyMode('reply')}
                className={`px-2.5 py-0.5 rounded-md text-[11.5px] font-semibold transition-colors cursor-pointer ${
                  replyMode === 'reply'
                    ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                    : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
                }`}
              >
                Пользователю
              </button>
              <button
                type="button"
                onClick={() => setReplyMode('internal')}
                className={`px-2.5 py-0.5 rounded-md text-[11.5px] font-semibold transition-colors cursor-pointer ${
                  replyMode === 'internal'
                    ? 'bg-amber-100 dark:bg-amber-900/80 text-amber-900 dark:text-amber-100 shadow-2xs'
                    : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
                }`}
              >
                Скрытый
              </button>
            </div>

            {selectedStatusOverride !== null && (
              <div className="flex items-center gap-1.5 px-2 py-0.5 bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md text-[11px] font-medium text-neutral-800 dark:text-neutral-200">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${getStatusDotClass(selectedStatusOverride)}`} />
                <span>Статус: {getStatusNameById(selectedStatusOverride)}</span>
                <button
                  type="button"
                  onClick={() => setSelectedStatusOverride(null)}
                  className="hover:text-rose-600 font-bold ml-1 cursor-pointer p-0.5"
                  title="Сбросить статус к стандартному"
                >
                  <svg width="8" height="8" viewBox="0 0 10 10" fill="none">
                    <path d="M1.5 1.5l7 7M8.5 1.5l-7 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
                  </svg>
                </button>
              </div>
            )}
          </div>

          <div className="flex items-center gap-1.5">
            {templates.length > 0 && (
              <select
                value={selectedTemplateKey}
                onChange={e => handleTemplateSelect(e.target.value)}
                className="text-[11.5px] bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg px-2 py-0.5 text-neutral-700 dark:text-neutral-300 outline-none max-w-[170px] font-medium cursor-pointer"
              >
                <option value="">Шаблоны ответов...</option>
                {templates.map(t => (
                  <option key={t.key} value={t.key}>
                    {t.name}
                  </option>
                ))}
              </select>
            )}

            {replyText && (
              <button
                type="button"
                onClick={() => setReplyText('')}
                className="text-[11px] text-neutral-400 hover:text-rose-600 dark:hover:text-rose-400 font-medium px-1 cursor-pointer"
                title="Очистить текст"
              >
                Очистить
              </button>
            )}
          </div>
        </div>

        {/* Row 2: Textarea */}
        <textarea
          value={replyText}
          onChange={e => setReplyText(e.target.value)}
          placeholder={replyMode === 'reply' ? 'Напишите комментарий для пользователя...' : 'Скрытый комментарий (только для инженеров)...'}
          rows={2}
          className={`w-full px-3 py-2 text-[13px] rounded-lg border text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-colors resize-none ${
            replyMode === 'internal'
              ? 'border-amber-300 dark:border-amber-800 bg-amber-50/20 dark:bg-amber-950/20'
              : 'border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-900'
          }`}
        />

        {/* Row 3: Action Controls Bar */}
        <div className="flex items-center justify-between gap-2 pt-0.5 flex-wrap">
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 text-[11.5px] text-neutral-500 dark:text-neutral-400 font-medium">
              <span>Списание:</span>
              <input
                type="number"
                value={expenses}
                onChange={e => setExpenses(Number(e.target.value))}
                min={0}
                max={240}
                className="w-12 h-6 px-1 bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded text-neutral-900 dark:text-neutral-100 text-center font-mono font-bold text-[11.5px]"
              />
              <span>мин</span>
            </div>

            {/* Other Statuses Menu */}
            <div className="relative" ref={actionsMenuRef}>
              <button
                type="button"
                onClick={() => setIsActionsMenuOpen(!isActionsMenuOpen)}
                className="h-6 px-2 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-700 text-neutral-700 dark:text-neutral-300 border border-neutral-200 dark:border-neutral-700 rounded text-[11px] font-semibold transition-colors cursor-pointer flex items-center gap-1"
              >
                <span>Статус</span>
                <svg width="8" height="8" viewBox="0 0 10 10" fill="none" className="text-neutral-400">
                  <path d="M2.5 3.5l2.5 2.5 2.5-2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>

              {isActionsMenuOpen && (
                <div className="absolute left-0 bottom-7 z-30 w-64 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-xl shadow-xl p-1.5 space-y-1.5 animate-in fade-in zoom-in-95 duration-100">
                  {[35, 36, 37, 48].some(s => isStatusAllowed(s)) && (
                    <div>
                      <span className="text-[9.5px] uppercase font-bold text-neutral-400 dark:text-neutral-500 block px-2 mb-0.5">
                        Статусы ожидания
                      </span>
                      <div className="space-y-0.5">
                        {isStatusAllowed(35) && (
                          <button
                            type="button"
                            onClick={() => handleSelectMenuStatus(35, 'Запрошена дополнительная информация у заявителя. Ожидаем ответа.', 5)}
                            className="w-full text-left px-2 py-1.5 rounded-md text-[11.5px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                          >
                            <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-amber-500" />
                            <span>Ожидание заявителя</span>
                          </button>
                        )}
                        {isStatusAllowed(36) && (
                          <button
                            type="button"
                            onClick={() => handleSelectMenuStatus(36, 'Заявка переведена в ожидание поставки оборудования / ЗИП.', 5)}
                            className="w-full text-left px-2 py-1.5 rounded-md text-[11.5px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                          >
                            <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-amber-500" />
                            <span>Ожидание поставки / ЗИП</span>
                          </button>
                        )}
                        {isStatusAllowed(37) && (
                          <button
                            type="button"
                            onClick={() => handleSelectMenuStatus(37, 'Заявка передана на исполнение сторонней организации / подрядчику.', 5)}
                            className="w-full text-left px-2 py-1.5 rounded-md text-[11.5px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                          >
                            <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-amber-500" />
                            <span>Ожидание подрядчика</span>
                          </button>
                        )}
                        {isStatusAllowed(48) && (
                          <button
                            type="button"
                            onClick={() => handleSelectMenuStatus(48, 'Приносите системный блок / ноутбук в АБК 3, 112 каб. на аппаратную диагностику и обслуживание.', 10)}
                            className="w-full text-left px-2 py-1.5 rounded-md text-[11.5px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                          >
                            <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-amber-500" />
                            <span>Ожидание устройства (каб. 112)</span>
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  {isStatusAllowed(26) && (
                    <div className="border-t border-neutral-100 dark:border-neutral-800 pt-1">
                      <span className="text-[9.5px] uppercase font-bold text-neutral-400 dark:text-neutral-500 block px-2 mb-0.5">
                        Перераспределение
                      </span>
                      <button
                        type="button"
                        onClick={() => handleSelectMenuStatus(26, 'Заявка возвращена в статус Открыта для перераспределения.', 5)}
                        className="w-full text-left px-2 py-1.5 rounded-md text-[11.5px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                      >
                        <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-blue-500" />
                        <span>Вернуть в статус «Открыта»</span>
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-1.5">
            {isStatusAllowed(30) && (
              <button
                type="button"
                onClick={handleCancelTicketClick}
                disabled={submitting || ticket.statusId === 30 || ticket.statusId === 29}
                className={`h-8 px-2.5 text-[11.5px] font-bold rounded-lg border transition-colors cursor-pointer whitespace-nowrap disabled:opacity-40 ${
                  confirmingCancel
                    ? 'bg-rose-600 text-white border-rose-600 hover:bg-rose-700 animate-pulse'
                    : 'text-rose-700 dark:text-rose-400 border-rose-200 dark:border-rose-900/60 hover:bg-rose-50 dark:hover:bg-rose-950/40'
                }`}
                title={confirmingCancel ? 'Кликните еще раз для подтверждения отмены' : 'Отменить заявку'}
              >
                {confirmingCancel ? 'Подтвердить отмену?' : 'Отменить'}
              </button>
            )}

            {isStatusAllowed(27) && ticket.statusId !== 27 && (
              <button
                type="button"
                onClick={handleTakeOwnership}
                disabled={submitting}
                className="h-8 px-2.5 text-[11.5px] font-semibold text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-lg transition-colors disabled:opacity-40 cursor-pointer whitespace-nowrap"
              >
                В работу
              </button>
            )}

            <button
              type="button"
              onClick={handleExecuteMainAction}
              disabled={submitting || ticket.statusId === 29 || ticket.statusId === 30 || mainAction.statusId === 0 || !isStatusAllowed(mainAction.statusId)}
              className={`h-8 px-3.5 text-[12px] font-bold rounded-lg transition-all disabled:opacity-40 cursor-pointer flex items-center justify-center gap-1.5 shadow-xs whitespace-nowrap ${mainAction.buttonClass}`}
            >
              <span>{submitting ? 'Сохранение...' : mainAction.label}</span>
              <span className="text-[10px] opacity-70 font-mono font-normal">Ctrl+Enter</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}



