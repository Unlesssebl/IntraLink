import { useState, useEffect, useCallback, useMemo } from 'react';
import type { Ticket } from '../data/mock';
import {
  fetchDiagnostics,
  applyTask,
  fetchTaskDetails,
  reanalyzeTask,
  fetchTemplatesCatalog,
  enqueueExecution,
  pollExecutionJob,
  fetchTicketSummary,
} from '../lib/tasks';
import type { TaskDetails, TicketSummaryResult } from '../lib/types';
import InspectorHeader from './inspector/InspectorHeader';
import RequesterCard from './inspector/RequesterCard';
import { type DiagStatus } from './inspector/DiagnosticsSection';
import AiTriageCard from './inspector/AiTriageCard';
import AiSummarySection from './inspector/AiSummarySection';
import RagMatchesSection from './inspector/RagMatchesSection';
import AttachmentsSection from './inspector/AttachmentsSection';
import CommentsTimeline from './inspector/CommentsTimeline';
import ReplyActionForm, { type MainActionConfig } from './inspector/ReplyActionForm';

interface Props {
  ticket: Ticket;
  onClose: () => void;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
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
  const [expanded, setExpanded] = useState<boolean>(() => {
    return localStorage.getItem('intralink_inspector_expanded') === 'true';
  });

  const toggleExpanded = () => {
    setExpanded(prev => {
      const next = !prev;
      localStorage.setItem('intralink_inspector_expanded', String(next));
      return next;
    });
  };

  const [submitting, setSubmitting] = useState(false);
  const [templates, setTemplates] = useState<any[]>([]);
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string>('');
  const [selectedStatusOverride, setSelectedStatusOverride] = useState<number | null>(null);
  const [isActionsMenuOpen, setIsActionsMenuOpen] = useState<boolean>(false);
  const [confirmingCancel, setConfirmingCancel] = useState<boolean>(false);

  // AI & RAG States
  const [aiSummary, setAiSummary] = useState<TicketSummaryResult | null>(null);
  const [loadingAiSummary, setLoadingAiSummary] = useState(false);
  const [isAiSummaryExpanded, setIsAiSummaryExpanded] = useState(true);
  const [isRagExpanded, setIsRagExpanded] = useState(false);
  const [isCommentsExpanded, setIsCommentsExpanded] = useState(false);
  const [reanalyzing, setReanalyzing] = useState(false);

  // Resizing state
  const [inspectorWidth, setInspectorWidth] = useState<number>(() => {
    const saved = localStorage.getItem('intralink_inspector_width');
    const parsed = saved ? parseInt(saved, 10) : 560;
    return !isNaN(parsed) && parsed >= 420 ? parsed : 560;
  });
  const [isResizing, setIsResizing] = useState(false);

  // Drag-to-resize listener
  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      const maxW = Math.min(1200, window.innerWidth * 0.92);
      const minW = 420;
      const newWidth = Math.max(minW, Math.min(maxW, window.innerWidth - e.clientX));
      setInspectorWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      localStorage.setItem('intralink_inspector_width', String(inspectorWidth));
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing, inspectorWidth]);

  const rawId = ticket.rawId || parseInt(ticket.id.replace(/\D/g, ''), 10);
  const effectiveHost = ticket.host || details?.pc_name || '';
  const hostList = effectiveHost
    ? Array.from(new Set(effectiveHost.split(/[,;]+/).map(h => h.trim().replace(/\s+/g, '')).filter(Boolean)))
    : [];

  const handleReanalyze = async () => {
    if (!rawId || reanalyzing) return;
    setReanalyzing(true);
    try {
      const updated = await reanalyzeTask(rawId);
      setDetails(updated);
      if (updated.ai_suggested_resolution && updated.ai_suggested_resolution.trim().length > 10) {
        setReplyText(updated.ai_suggested_resolution);
      }
      onToast({
        type: 'success',
        message: `Заявка #${rawId} переанализирована по актуальным правилам`,
      });
    } catch (err: any) {
      onToast({
        type: 'error',
        message: `Ошибка переанализа: ${err.message || err}`,
      });
    } finally {
      setReanalyzing(false);
    }
  };

  // Load Task Details from Core API
  const loadDetails = useCallback(async () => {
    if (!rawId) return;
    setLoadingDetails(true);
    try {
      const data = await fetchTaskDetails(rawId);
      setDetails(data);
      if (data.ai_suggested_resolution && data.ai_suggested_resolution.trim().length > 10) {
        setReplyText(prev => {
          const defaultInit = ticket.aiPlan?.comment || ticket.aiSuggestion || '';
          if (!prev || prev === defaultInit) {
            return data.ai_suggested_resolution!;
          }
          return prev;
        });
      }
    } catch (err: any) {
      console.warn('Не удалось загрузить подробности заявки:', err);
    } finally {
      setLoadingDetails(false);
    }
  }, [rawId, ticket.aiPlan?.comment, ticket.aiSuggestion]);

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

  const handleGenerateAiSummary = async () => {
    if (!rawId || loadingAiSummary) return;
    setLoadingAiSummary(true);
    try {
      const res = await fetchTicketSummary(
        rawId,
        ticket.title,
        ticket.description || details?.description || '',
        commentsList,
        false
      );
      setAiSummary(res);
      setIsAiSummaryExpanded(true);
      onToast({ type: 'success', message: 'Сводка переписки успешно сформирована AI Hub' });
    } catch (err: any) {
      console.error('Ошибка суммаризации переписки:', err);
      onToast({ type: 'error', message: 'Не удалось сгенерировать AI-сводку переписки' });
    } finally {
      setLoadingAiSummary(false);
    }
  };

  // Load Templates catalog
  useEffect(() => {
    fetchTemplatesCatalog().then(res => {
      if (res && Array.isArray(res.templates)) setTemplates(res.templates);
    }).catch(() => {});
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
    setAiSummary(null);
    setLoadingAiSummary(false);
    setIsAiSummaryExpanded(true);
    setIsRagExpanded(false);
    setIsCommentsExpanded(false);
    loadDetails();
  }, [ticket.id, loadDetails]);

  // Network diagnostic runner
  const runDiag = async (targetHost?: string) => {
    const hostToTest = targetHost || effectiveHost;
    if (!hostToTest) {
      onToast({ type: 'warning', message: 'Имя ПК/хоста не указано в заявке' });
      return;
    }

    setDiagStatus({ ping: 'checking', smb: 'checking', winrm: 'checking' });
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

  // Main button label & status config
  const getMainActionConfig = (): MainActionConfig => {
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
      return { label: 'Выдать доступ к Wi-Fi', statusId: 29, buttonClass: primaryBtnClass, actionType: 'wlan' };
    }
    const isPrinterTask =
      selectedTemplateKey === 'printer_install' ||
      ticket.ruleType === 'printer_install' ||
      ticket.serviceName?.toLowerCase().includes('принтер') ||
      ticket.serviceName?.toLowerCase().includes('печать') ||
      ticket.title?.toLowerCase().includes('принтер');
    if (isPrinterTask && isStatusAllowed(29)) {
      return { label: 'Установить принтер', statusId: 29, buttonClass: primaryBtnClass, actionType: 'printer' };
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

  const mainAction = getMainActionConfig();

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

    setSubmitting(true);
    try {
      const res = await applyTask(rawId, {
        status_id: targetStatusId,
        comment: textToSend || 'Взято в работу инженером 1-й линии',
        minutes: expenses,
        is_private: replyMode === 'internal',
      });

      const firstRes = res?.results?.[0];
      if (firstRes && firstRes.update_ok === false) {
        throw new Error(firstRes.error || 'IntraService отклонил изменение заявки');
      }

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

      const res = await applyTask(rawId, {
        status_id: selectedStatusOverride ?? plan.targetStatusId,
        comment: replyText.trim() || plan.comment,
        minutes: expenses || plan.expensesMinutes,
        is_private: replyMode === 'internal',
      });

      const firstRes = res?.results?.[0];
      if (firstRes && firstRes.update_ok === false) {
        throw new Error(firstRes.error || 'IntraService отклонил изменение заявки');
      }

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
          auto_close_ticket: false,
        });
        await pollExecutionJob(job.job_id, 15000, 1000);

        const res = await applyTask(rawId, {
          status_id: 29,
          comment: `Добрый день! Доступ к сети Wi-Fi успешно предоставлен для учетной записи ${username}.`,
          minutes: 10,
        });
        const firstRes = res?.results?.[0];
        if (firstRes && firstRes.update_ok === false) {
          throw new Error(firstRes.error || 'IntraService отклонил изменение заявки');
        }

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

  // Cancel Ticket Handler with 2-step inline confirm
  const handleCancelTicketClick = async () => {
    if (!confirmingCancel) {
      setConfirmingCancel(true);
      return;
    }
    setConfirmingCancel(false);
    const cancelComment = replyText.trim() || 'Заявка отменена специалистом 1-й линии техподдержки.';
    await handleSendAction(30, cancelComment);
  };

  // Take Ticket in progress
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

  // Keyboard shortcut Ctrl+Enter & Esc
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

  const panelClass = expanded
    ? 'fixed inset-0 z-40 flex flex-col bg-neutral-100 dark:bg-neutral-950 animate-in fade-in duration-150 overflow-hidden'
    : 'fixed top-0 bottom-0 right-0 z-30 flex flex-col border-l border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 shadow-2xl animate-in slide-in-from-right duration-200';

  // Check WinRM Assistant condition
  const hasFailedWorkerAttempt = useMemo(() => {
    if (!details?.comments || !Array.isArray(details.comments)) return false;
    return details.comments.some((c: any) => {
      const text = (c.Comment || c.comment || c.text || '').toLowerCase();
      return (
        text.includes('ошибка подключения') ||
        (text.includes('winrm') && (text.includes('сбой') || text.includes('не удалось') || text.includes('таймаут') || text.includes('отказано'))) ||
        text.includes('worker error') ||
        text.includes('failed')
      );
    });
  }, [details?.comments]);

  const isWinRMBlockedWhileOnline = useMemo(() => {
    const d = multiHostDiag[effectiveHost] || diagStatus;
    const isOnline = d.ping === 'ok' || d.smb === 'ok';
    const isWinRMClosed = d.winrm === 'fail';
    return isOnline && isWinRMClosed;
  }, [multiHostDiag, effectiveHost, diagStatus]);

  const showWinRMAssistant = Boolean(
    (ticket.title?.toLowerCase().includes('принтер') || ticket.serviceName?.toLowerCase().includes('принтер')) &&
    (hasFailedWorkerAttempt || isWinRMBlockedWhileOnline)
  );

  const renderDescription = () => (
    <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-3.5 shadow-xs space-y-2">
      <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 block">
        Описание проблемы
      </span>
      <div className="text-[13.5px] text-neutral-800 dark:text-neutral-200 leading-relaxed whitespace-pre-wrap font-sans bg-neutral-50/80 dark:bg-neutral-950/60 p-3 rounded-lg border border-neutral-200/70 dark:border-neutral-800/70">
        {(details?.description || ticket.description || 'Без описания').replace(/[#*`]/g, '').trim()}
      </div>
    </div>
  );

  return (
    <div
      className={panelClass}
      style={expanded ? undefined : { width: `${inspectorWidth}px`, maxWidth: '94vw' }}
    >
      {/* Draggable resize handle on left border */}
      {!expanded && (
        <div
          onMouseDown={(e) => {
            e.preventDefault();
            setIsResizing(true);
          }}
          className={`absolute -left-1.5 top-0 bottom-0 w-3 cursor-col-resize z-30 transition-colors group flex items-center justify-center ${
            isResizing ? 'bg-blue-500/20' : 'hover:bg-blue-500/20'
          }`}
          title="Потяните для изменения ширины панели (дважды кликните для сброса к 560px)"
          onDoubleClick={() => {
            setInspectorWidth(560);
            localStorage.setItem('intralink_inspector_width', '560');
          }}
        >
          <div className="w-0.5 h-12 rounded-full bg-neutral-300 dark:bg-neutral-700 group-hover:bg-blue-500 transition-colors" />
        </div>
      )}

      {/* 1. Header */}
      <InspectorHeader
        rawId={rawId}
        ticket={ticket}
        expanded={expanded}
        onToggleExpanded={toggleExpanded}
        onClose={onClose}
        onToast={onToast}
      />

      {/* 2. Body Content (Adaptive single column or dual pane when expanded) */}
      {expanded ? (
        <div className="flex-1 min-h-0 overflow-hidden">
          <div className="max-w-7xl mx-auto w-full h-full p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-3.5 overflow-y-auto pr-1">
              <RequesterCard
                ticket={ticket}
                details={details}
                effectiveHost={effectiveHost}
                hostList={hostList}
                rawId={rawId}
                diagStatus={diagStatus}
                multiHostDiag={multiHostDiag}
                showWinRMAssistant={showWinRMAssistant}
                onRunDiag={runDiag}
                onToast={onToast}
              />
              {renderDescription()}
              <AttachmentsSection attachments={attachmentsList} rawId={rawId} />
              <RagMatchesSection
                kbMatches={details?.kb_matches || []}
                isRagExpanded={isRagExpanded}
                onToggleRagExpanded={() => setIsRagExpanded(prev => !prev)}
                onInsertSolution={(solution, taskId) => {
                  setReplyText(solution);
                  onToast({ type: 'info', message: `Решение из заявки #${taskId} подставлено в редактор` });
                }}
              />
            </div>
            <div className="flex flex-col gap-3 min-h-0 overflow-hidden">
              <CommentsTimeline
                commentsList={commentsList}
                loadingDetails={loadingDetails}
                isCommentsExpanded={isCommentsExpanded}
                onToggleCommentsExpanded={() => setIsCommentsExpanded(prev => !prev)}
                expandedMode={true}
                aiSummarySlot={
                  <AiSummarySection
                    commentsCount={commentsList.length}
                    aiSummary={aiSummary}
                    loadingAiSummary={loadingAiSummary}
                    isAiSummaryExpanded={isAiSummaryExpanded}
                    onToggleAiSummaryExpanded={() => setIsAiSummaryExpanded(prev => !prev)}
                    onGenerateAiSummary={handleGenerateAiSummary}
                  />
                }
              />
            </div>
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          <div className="p-4 space-y-3.5">
            <RequesterCard
              ticket={ticket}
              details={details}
              effectiveHost={effectiveHost}
              hostList={hostList}
              rawId={rawId}
              diagStatus={diagStatus}
              multiHostDiag={multiHostDiag}
              showWinRMAssistant={showWinRMAssistant}
              onRunDiag={runDiag}
              onToast={onToast}
            />
            {renderDescription()}
            <AttachmentsSection attachments={attachmentsList} rawId={rawId} />
            <RagMatchesSection
              kbMatches={details?.kb_matches || []}
              isRagExpanded={isRagExpanded}
              onToggleRagExpanded={() => setIsRagExpanded(prev => !prev)}
              onInsertSolution={(solution, taskId) => {
                setReplyText(solution);
                onToast({ type: 'info', message: `Решение из заявки #${taskId} подставлено в редактор` });
              }}
            />
            <CommentsTimeline
              commentsList={commentsList}
              loadingDetails={loadingDetails}
              isCommentsExpanded={isCommentsExpanded}
              onToggleCommentsExpanded={() => setIsCommentsExpanded(prev => !prev)}
              expandedMode={false}
              aiSummarySlot={
                <AiSummarySection
                  commentsCount={commentsList.length}
                  aiSummary={aiSummary}
                  loadingAiSummary={loadingAiSummary}
                  isAiSummaryExpanded={isAiSummaryExpanded}
                  onToggleAiSummaryExpanded={() => setIsAiSummaryExpanded(prev => !prev)}
                  onGenerateAiSummary={handleGenerateAiSummary}
                />
              }
            />
          </div>
        </div>
      )}

      {/* 3. Consolidated Action Footer */}
      <div className="border-t border-neutral-200 dark:border-neutral-800 p-3.5 shrink-0 bg-white dark:bg-neutral-900 shadow-md space-y-3">
        <AiTriageCard
          ticket={ticket}
          details={details}
          targetStatusId={mainAction.statusId}
          targetStatusName={getStatusNameById(mainAction.statusId)}
          selectedStatusOverride={selectedStatusOverride}
          onResetStatusOverride={() => setSelectedStatusOverride(null)}
          reanalyzing={reanalyzing}
          onReanalyze={handleReanalyze}
          replyText={replyText}
          onInsertAiSynthesis={(txt) => {
            setReplyText(txt);
            onToast({ type: 'info', message: 'Синтез AI подставлен в ответ' });
          }}
          expenses={expenses}
          onChangeExpenses={setExpenses}
        />

        <ReplyActionForm
          ticket={ticket}
          replyMode={replyMode}
          onSetReplyMode={setReplyMode}
          replyText={replyText}
          onChangeReplyText={setReplyText}
          templates={templates}
          selectedTemplateKey={selectedTemplateKey}
          onSelectTemplate={handleTemplateSelect}
          isStatusAllowed={isStatusAllowed}
          isActionsMenuOpen={isActionsMenuOpen}
          onToggleActionsMenu={() => setIsActionsMenuOpen(!isActionsMenuOpen)}
          onSelectMenuStatus={handleSelectMenuStatus}
          confirmingCancel={confirmingCancel}
          onCancelTicketClick={handleCancelTicketClick}
          onTakeOwnership={handleTakeOwnership}
          onExecuteMainAction={handleExecuteMainAction}
          mainAction={mainAction}
          submitting={submitting}
        />
      </div>
    </div>
  );
}
