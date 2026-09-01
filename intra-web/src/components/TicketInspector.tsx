import { useState, useEffect, useCallback } from 'react';
import type { Ticket, Status } from '../data/mock';
import { statusConfig, categoryLabel } from '../data/mock';
import { fetchDiagnostics, applyTask, fetchTaskDetails, fetchTemplatesCatalog } from '../lib/tasks';
import type { TaskDetails } from '../lib/types';

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

function getSlaClass(deadline: Date) {
  const ms = deadline.getTime() - Date.now();
  const h = ms / 3600000;
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

type DiagStatus = 'ok' | 'fail' | 'checking' | 'idle';

function DiagBadge({ status }: { status: DiagStatus }) {
  const cls = {
    ok: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800',
    fail: 'bg-rose-100 text-rose-800 dark:bg-rose-950/60 dark:text-rose-300 border border-rose-300 dark:border-rose-800',
    checking: 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300 border border-amber-300 dark:border-amber-800',
    idle: 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400 border border-neutral-200 dark:border-neutral-700',
  }[status];
  const label = { ok: 'ОК', fail: 'Недоступен', checking: 'Проверка...', idle: '—' }[status];
  return <span className={`text-[11.5px] font-mono px-2 py-0.5 rounded-md font-bold ${cls}`}>{label}</span>;
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
  const [expanded, setExpanded] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [templates, setTemplates] = useState<any[]>([]);
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string>('');

  const rawId = ticket.rawId || parseInt(ticket.id.replace(/\D/g, ''), 10);

  // Load Task Details (comments, attachments, custom fields) from real API
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
      if (res && res.templates) setTemplates(res.templates);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setReplyText('');
    setDiagStatus({ ping: 'idle', smb: 'idle', winrm: 'idle' });
    loadDetails();
  }, [ticket.id, loadDetails]);

  // Auto-fill suggested comment if available
  useEffect(() => {
    if (ticket.aiSuggestion && !replyText) {
      setReplyText(ticket.aiSuggestion);
    }
  }, [ticket.id, ticket.aiSuggestion]);

  const runDiag = async () => {
    if (!ticket.host) {
      onToast({ type: 'warning', message: 'Хост/ПК не указан в заявке' });
      return;
    }
    setDiagStatus({ ping: 'checking', smb: 'checking', winrm: 'checking' });
    try {
      const res = await fetchDiagnostics(ticket.host);
      setDiagStatus({
        ping: res.is_online ? 'ok' : 'fail',
        smb: res.smb_ok ? 'ok' : 'fail',
        winrm: res.winrm_ok ? 'ok' : 'fail',
      });
      onToast({ type: 'info', message: `Диагностика ${ticket.host}: ${res.is_online ? 'В сети' : 'Недоступен'}` });
    } catch {
      setDiagStatus({ ping: 'fail', smb: 'fail', winrm: 'fail' });
      onToast({ type: 'error', message: `Ошибка диагностики хоста ${ticket.host}` });
    }
  };

  const handleTemplateSelect = (key: string) => {
    setSelectedTemplateKey(key);
    const tmpl = templates.find(t => t.key === key);
    if (tmpl) {
      setReplyText(tmpl.template);
      if (tmpl.expenses) setExpenses(tmpl.expenses);
    }
  };

  // Quick Action with Guard (Audit C-1, Marks #6, #7)
  const handleQuickAction = async (actionType: string) => {
    if (ticket.statusId === 29 || ticket.statusId === 30) {
      onToast({ type: 'warning', message: `Заявка #${rawId} уже закрыта или отменена.` });
      return;
    }

    setSubmitting(true);
    try {
      if (actionType === 'redirect') {
        const comm = ticket.aiSuggestion || `Заявка отменена, т. к. создана не в подходящем разделе. Требуется оставить заявку в подходящем разделе: ${ticket.targetServiceName || 'соответствующий сервис'}. По вопросам звоните на 49-87.`;
        await applyTask(rawId, {
          status_id: 30,
          comment: comm,
          minutes: 5,
        });
        onToast({ type: 'success', message: `Заявка #${rawId} перенаправлена в ${ticket.targetServiceName || 'целевой раздел'}` });
        onUpdateTicket(ticket.id, { status: 'resolved', statusId: 30, statusName: 'Отменена' });
        onClose();
      } else if (actionType === 'duplicate') {
        const masterId = ticket.duplicateInfo?.master_task_id || '';
        const comm = `Заявка отменена как повторная (дубликат инцидента #${masterId}). Все работы ведутся в основной заявке. По вопросам звоните на 49-87.`;
        await applyTask(rawId, {
          status_id: 30,
          comment: comm,
          minutes: 5,
        });
        onToast({ type: 'success', message: `Заявка #${rawId} отменена как дубликат #${masterId}` });
        onUpdateTicket(ticket.id, { status: 'resolved', statusId: 30, statusName: 'Отменена' });
        onClose();
      } else if (actionType === 'hardware') {
        const comm = ticket.aiSuggestion || 'Приносите системный блок / ноутбук в АБК 3, 112 каб. на аппаратную диагностику и обслуживание.';
        await applyTask(rawId, {
          status_id: 48,
          comment: comm,
          minutes: 10,
        });
        onToast({ type: 'success', message: `Заявка #${rawId}: статус обновлен («Ожидание устройства», каб. 112)` });
        onUpdateTicket(ticket.id, { status: 'waiting', statusId: 48, statusName: 'Ожидание устройства' });
      } else if (actionType === 'wlan') {
        const comm = ticket.aiSuggestion || 'Доступ к беспроводной корпоративной сети WLAN-WORKNET успешно предоставлен. Используйте логин и пароль от вашей учетной записи на ПК.';
        await applyTask(rawId, {
          status_id: 29,
          comment: comm,
          minutes: 10,
        });
        onToast({ type: 'success', message: `Заявка #${rawId} выполнена (Доступ к Wi-Fi предоставлен)` });
        onUpdateTicket(ticket.id, { status: 'resolved', statusId: 29, statusName: 'Выполнена' });
        onClose();
      }
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка: ${err.message || err}` });
    } finally {
      setSubmitting(false);
    }
  };

  // Send action with Guard (Audit C-1, Marks #6, #7)
  const handleSendAction = async (targetStatusId: number) => {
    if (!replyText.trim()) {
      onToast({ type: 'warning', message: 'Введите текст комментария или ответа' });
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
        comment: replyText.trim(),
        minutes: expenses,
        is_private: replyMode === 'internal',
      });

      const newStatus = targetStatusId === 29 || targetStatusId === 30 ? 'resolved' : (targetStatusId === 35 || targetStatusId === 48 ? 'waiting' : 'in_progress');
      const newStatusName = targetStatusId === 29 ? 'Выполнена' : (targetStatusId === 30 ? 'Отменена' : (targetStatusId === 27 ? 'В работе' : 'Ожидание'));

      onUpdateTicket(ticket.id, {
        status: newStatus,
        statusId: targetStatusId,
        statusName: newStatusName,
      });

      onToast({
        type: 'success',
        message: targetStatusId === 29 ? 'Заявка закрыта (Выполнена)' : (targetStatusId === 30 ? 'Заявка отменена' : 'Комментарий успешно сохранен'),
      });

      setReplyText('');
      loadDetails();
      if (targetStatusId === 29 || targetStatusId === 30) {
        onClose();
      }
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка сохранения: ${err.message || err}` });
    } finally {
      setSubmitting(false);
    }
  };

  const handleTakeOwnership = async () => {
    setSubmitting(true);
    try {
      await applyTask(rawId, {
        status_id: 27,
        comment: 'Взято в работу инженером 1-й линии',
        minutes: 5,
        executor_ids: '8664,10502',
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

  const copyToClipboard = (v: string) => {
    navigator.clipboard.writeText(v).then(() =>
      onToast({ type: 'info', message: 'Скопировано в буфер' })
    );
  };

  const panelClass = expanded
    ? 'fixed inset-0 z-40 flex flex-col bg-slate-100 dark:bg-neutral-950 animate-in fade-in duration-150'
    : 'fixed top-0 bottom-0 right-0 z-30 w-[540px] max-w-[90vw] flex flex-col border-l-2 border-neutral-300 dark:border-neutral-700 bg-slate-100/98 dark:bg-neutral-950 shadow-2xl animate-in slide-in-from-right duration-200';

  const commentsList = details?.comments || [];
  const attachmentsList = details?.attachments || ticket.attachments || [];

  return (
    <div className={panelClass}>
      {/* Header */}
      <div className="px-5 pt-4 pb-3.5 bg-white dark:bg-neutral-900 border-b border-neutral-200 dark:border-neutral-800 shrink-0 shadow-2xs">
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors cursor-pointer p-1"
              title="Закрыть инспектор"
            >
              <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
                <path d="M10 4l-4 4 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            <span className="font-mono text-[14px] font-bold text-neutral-600 dark:text-neutral-300">
              #{rawId}
            </span>
            <span className={`text-[12px] px-2.5 py-0.5 rounded-md font-bold ${statusConfig[ticket.status].className}`}>
              {ticket.statusName || statusConfig[ticket.status].label}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleTakeOwnership}
              disabled={submitting || ticket.statusId === 27}
              className="px-3 py-1.5 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-700 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-700 rounded-lg text-[12px] font-bold transition-colors disabled:opacity-50 cursor-pointer"
              title="Назначить на себя и перевести в работу"
            >
              Взять в работу
            </button>
            <button
              onClick={() => setExpanded(e => !e)}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer border border-neutral-200/80 dark:border-neutral-800"
              title={expanded ? 'Свернуть панель' : 'Развернуть на весь экран'}
            >
              {expanded ? (
                <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
                  <path d="M8.5 1.5v3h3M4.5 11.5v-3h-3M8.5 11.5v-3h3M4.5 1.5v3h-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
                  <path d="M1.5 4.5h3v-3M11.5 4.5h-3v-3M1.5 8.5h3v3M11.5 8.5h-3v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
            </button>
          </div>
        </div>

        <h2 className="text-[15px] font-bold text-neutral-900 dark:text-neutral-50 leading-snug">
          {ticket.title}
        </h2>
        <div className="text-[12px] text-neutral-500 dark:text-neutral-400 mt-1 flex items-center gap-1.5 flex-wrap font-medium">
          <span>{ticket.servicePath || ticket.serviceName}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3.5">
        {/* Duplicate Banner (Marks #7: words instead of status ID) */}
        {(ticket.isDuplicate || ticket.ruleType === 'duplicate_task') && (
          <div className="border border-amber-300 dark:border-amber-700 bg-amber-50/90 dark:bg-amber-950/40 rounded-xl p-3.5 space-y-2 shadow-xs">
            <div className="flex items-center justify-between">
              <span className="text-amber-900 dark:text-amber-200 text-[13px] font-bold">
                Повторная заявка (Дубликат)
              </span>
              {ticket.duplicateInfo?.master_task_id && (
                <span className="text-[11.5px] font-mono font-bold bg-amber-100 dark:bg-amber-900/80 text-amber-900 dark:text-amber-200 px-2 py-0.5 rounded-md border border-amber-300 dark:border-amber-700">
                  Master #{ticket.duplicateInfo.master_task_id}
                </span>
              )}
            </div>
            <p className="text-[12.5px] text-neutral-800 dark:text-neutral-200 leading-relaxed">
              Заявитель уже имеет открытую заявку по аналогичной проблеме.
            </p>
            <button
              onClick={() => handleQuickAction('duplicate')}
              disabled={submitting}
              className="px-3 py-1.5 bg-neutral-900 hover:bg-neutral-800 text-white dark:bg-neutral-100 dark:hover:bg-neutral-200 dark:text-neutral-900 text-[12px] font-bold rounded-lg transition-colors disabled:opacity-50 cursor-pointer"
            >
              Отменить как дубликат («Отменена»)
            </button>
          </div>
        )}

        {/* 1-Click Smart Actions Bar (Marks #6, #7: Statuses in words) */}
        <div className="flex flex-wrap gap-2">
          {(ticket.isRedirect || ticket.ruleType?.startsWith('redirect')) && (
            <button
              onClick={() => handleQuickAction('redirect')}
              disabled={submitting}
              className="px-3 py-1.5 bg-white dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-750 text-slate-900 dark:text-slate-100 border border-slate-300 dark:border-slate-700 text-[12px] font-bold rounded-lg flex items-center gap-1.5 transition-colors disabled:opacity-50 cursor-pointer shadow-xs"
            >
              Редирект в {ticket.targetServiceName || 'соответствующий сервис'} («Отменена»)
            </button>
          )}

          {ticket.ruleType === 'hardware_repair' && (
            <button
              onClick={() => handleQuickAction('hardware')}
              disabled={submitting}
              className="px-3 py-1.5 bg-purple-100 dark:bg-purple-950/60 hover:bg-purple-200 dark:hover:bg-purple-900/80 text-purple-950 dark:text-purple-200 border border-purple-300 dark:border-purple-800 text-[12px] font-bold rounded-lg flex items-center gap-1.5 transition-colors disabled:opacity-50 cursor-pointer shadow-xs"
            >
              В аппаратный ремонт («Ожидание устройства»)
            </button>
          )}

          {(ticket.ruleType === 'wlan_access' || ticket.templateKey === 'wifi_access') && (
            <button
              onClick={() => handleQuickAction('wlan')}
              disabled={submitting}
              className="px-3 py-1.5 bg-emerald-100 dark:bg-emerald-950/60 hover:bg-emerald-200 dark:hover:bg-emerald-900/80 text-emerald-950 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800 text-[12px] font-bold rounded-lg flex items-center gap-1.5 transition-colors disabled:opacity-50 cursor-pointer shadow-xs"
            >
              Выдать доступ к Wi-Fi («Выполнена»)
            </button>
          )}
        </div>

        {/* AI / Rule Recommendation Card (Audit M-1: dynamic expenses) */}
        {ticket.aiSuggestion && (
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-3.5 space-y-1.5 shadow-xs">
            <div className="flex items-center justify-between">
              <span className="text-[12px] font-bold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-blue-500" />
                Рекомендация триажа ({ticket.ruleType || 'Auto-Rule'})
              </span>
              <span className="text-[11px] font-mono text-neutral-500 dark:text-neutral-400 font-semibold">
                Трудозатраты: ~{expenses} мин
              </span>
            </div>
            <p className="text-[13px] text-neutral-800 dark:text-neutral-200 leading-relaxed whitespace-pre-wrap">{ticket.aiSuggestion}</p>
            <button
              onClick={() => setReplyText(ticket.aiSuggestion!)}
              className="text-[12px] text-blue-600 dark:text-blue-400 hover:underline font-bold cursor-pointer inline-block pt-1"
            >
              Вставить в форму ответа →
            </button>
          </div>
        )}

        {/* Requester & PC Card with Network Diag (Marks #4: Larger fonts) */}
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-3.5 space-y-2.5 bg-white dark:bg-neutral-900 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
              Заявитель и рабочее место
            </span>
            {ticket.host && (
              <button
                onClick={runDiag}
                disabled={diagStatus.ping === 'checking'}
                className="text-[12px] text-blue-600 dark:text-blue-400 hover:underline font-bold cursor-pointer"
              >
                {diagStatus.ping === 'checking' ? 'Проверка...' : 'Диагностика сети'}
              </button>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3 text-[13px]">
            <div>
              <span className="text-neutral-400 block text-[11px] font-medium">ФИО заявителя</span>
              <span className="text-neutral-900 dark:text-neutral-100 font-semibold">{ticket.requesterName}</span>
            </div>
            <div>
              <span className="text-neutral-400 block text-[11px] font-medium">Телефон</span>
              <span className="text-neutral-900 dark:text-neutral-100 font-mono font-semibold">{ticket.requesterPhone || details?.phone || '—'}</span>
            </div>
            <div>
              <span className="text-neutral-400 block text-[11px] font-medium">Кабинет / Отдел</span>
              <span className="text-neutral-800 dark:text-neutral-200 font-medium">
                {[ticket.room || details?.room, ticket.department || details?.department].filter(Boolean).join(' · ') || '—'}
              </span>
            </div>
            <div>
              <span className="text-neutral-400 block text-[11px] font-medium">Имя ПК / Хост</span>
              <div className="flex items-center gap-1.5">
                <span className="font-mono font-bold bg-neutral-100 dark:bg-neutral-800 px-2 py-0.5 rounded-md text-[12px] text-neutral-900 dark:text-neutral-100">
                  {ticket.host || details?.pc_name || 'Не указан'}
                </span>
                {ticket.host && (
                  <button
                    onClick={() => copyToClipboard(ticket.host)}
                    className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 cursor-pointer p-0.5"
                    title="Скопировать имя ПК"
                  >
                    <svg width="13" height="13" viewBox="0 0 11 11" fill="none">
                      <rect x="3.5" y="3.5" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
                      <path d="M1.5 7.5V1.5h6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
                    </svg>
                  </button>
                )}
              </div>
            </div>
            <div className="col-span-2">
              <span className="text-neutral-400 block text-[11px] font-medium">Исполнители</span>
              <span className="text-neutral-800 dark:text-neutral-200 font-medium text-[12.5px]">
                {ticket.executors || 'Не назначен'}
              </span>
            </div>
          </div>

          {/* Network diagnostic results */}
          {ticket.host && (
            <div className="pt-2.5 border-t border-neutral-100 dark:border-neutral-800 flex items-center justify-between text-[12px]">
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5 font-mono">
                  <span className="text-neutral-400 font-sans">Ping:</span>
                  <DiagBadge status={diagStatus.ping} />
                </div>
                <div className="flex items-center gap-1.5 font-mono">
                  <span className="text-neutral-400 font-sans">SMB:445:</span>
                  <DiagBadge status={diagStatus.smb} />
                </div>
                <div className="flex items-center gap-1.5 font-mono">
                  <span className="text-neutral-400 font-sans">WinRM:</span>
                  <DiagBadge status={diagStatus.winrm} />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Attachments Section */}
        {attachmentsList.length > 0 && (
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-3.5 bg-white dark:bg-neutral-900 shadow-xs">
            <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 block mb-2">
              Вложения и скриншоты ({attachmentsList.length})
            </span>
            <div className="space-y-1.5">
              {attachmentsList.map(att => (
                <a
                  key={att.id}
                  href={`/admin/api/tasks/${rawId}/attachments/${att.id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between p-2.5 rounded-lg bg-neutral-50 dark:bg-neutral-800/80 hover:bg-neutral-100 dark:hover:bg-neutral-700 transition-colors text-[13px]"
                >
                  <span className="truncate font-semibold text-blue-600 dark:text-blue-400">{att.name}</span>
                  <span className="text-[11.5px] text-neutral-400 font-mono shrink-0 ml-2">
                    {att.size ? `${Math.round(att.size / 1024)} КБ` : 'Скачать'}
                  </span>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* Description */}
        <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-3.5 shadow-xs space-y-2">
          <p className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 block">
            Описание проблемы
          </p>
          <div className="text-[14px] text-neutral-900 dark:text-neutral-100 leading-relaxed whitespace-pre-wrap font-sans bg-neutral-50/80 dark:bg-neutral-950/60 p-3 rounded-lg border border-neutral-200/70 dark:border-neutral-800/70">
            {(details?.description || ticket.description || 'Без описания').replace(/[#*`]/g, '').trim()}
          </div>
        </div>

        {/* Real Comments History (Lifetime) */}
        <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl p-3.5 shadow-xs space-y-3">
          <p className="text-[11px] font-bold uppercase tracking-wider text-neutral-400 block">
            История переписки {loadingDetails ? '(Загрузка...)' : `(${commentsList.length})`}
          </p>

          <div className="space-y-2.5">
            {commentsList.map(c => (
              <div
                key={c.id}
                className={`p-3.5 rounded-xl border text-[13px] ${
                  c.is_private
                    ? 'border-amber-300 dark:border-amber-800/60 bg-amber-50/60 dark:bg-amber-950/30'
                    : 'border-neutral-200 dark:border-neutral-800 bg-neutral-50/70 dark:bg-neutral-950/50'
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-neutral-900 dark:text-neutral-100">{c.author}</span>
                    {c.is_private && (
                      <span className="text-[11px] bg-amber-100 text-amber-900 dark:bg-amber-900/80 dark:text-amber-200 px-1.5 py-0.5 rounded font-bold">
                        Скрытый комментарий
                      </span>
                    )}
                  </div>
                  <span className="text-[11.5px] text-neutral-400 font-mono">{formatTime(c.created)}</span>
                </div>
                <p className="text-neutral-800 dark:text-neutral-200 leading-relaxed whitespace-pre-wrap">{c.text}</p>
              </div>
            ))}

            {commentsList.length === 0 && !loadingDetails && (
              <div className="text-[13px] text-neutral-400 italic py-2">
                В этой заявке пока нет комментариев
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Reply and Close Form (Marks #6: Helpdesk Terminology) */}
      <div className="border-t border-neutral-200 dark:border-neutral-800 p-4 shrink-0 bg-white dark:bg-neutral-900 shadow-lg space-y-3">
        {/* Top Controls: Mode & Template Selector */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex gap-1.5 bg-neutral-100 dark:bg-neutral-900 p-0.5 rounded-lg border border-neutral-200 dark:border-neutral-800">
            {(['reply', 'internal'] as const).map(mode => (
              <button
                key={mode}
                onClick={() => setReplyMode(mode)}
                className={`px-3 py-1 rounded-md text-[12px] font-semibold transition-colors cursor-pointer ${
                  replyMode === mode
                    ? 'bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                    : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
                }`}
              >
                {mode === 'reply' ? 'Комментарий для пользователя' : 'Скрытый комментарий'}
              </button>
            ))}
          </div>

          {/* Quick template dropdown */}
          {templates.length > 0 && (
            <select
              value={selectedTemplateKey}
              onChange={e => handleTemplateSelect(e.target.value)}
              className="text-[12px] bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg px-2.5 py-1 text-neutral-800 dark:text-neutral-200 outline-none max-w-[200px] font-medium"
            >
              <option value="">Выбрать шаблон...</option>
              {templates.map(t => (
                <option key={t.key} value={t.key}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* AI Draft Badge Indicator (Audit E-1) */}
        {ticket.aiSuggestion && (
          <div className="flex items-center justify-between px-3 py-1 bg-blue-50/80 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 rounded-lg text-[12px] text-blue-900 dark:text-blue-200">
            <div className="flex items-center gap-1.5 font-medium">
              <span className="text-blue-600 dark:text-blue-400 font-bold">✦</span>
              <span>Подставлен черновик от AI / Rule Engine</span>
            </div>
            <button
              type="button"
              onClick={() => setReplyText('')}
              className="text-[11.5px] text-blue-600 dark:text-blue-400 hover:underline cursor-pointer font-bold"
            >
              Очистить
            </button>
          </div>
        )}

        {/* Text Area */}
        <textarea
          value={replyText}
          onChange={e => setReplyText(e.target.value)}
          placeholder={replyMode === 'reply' ? 'Напишите комментарий для пользователя...' : 'Скрытый комментарий (только для инженеров)...'}
          rows={3}
          className={`w-full px-3.5 py-2.5 text-[13.5px] rounded-xl border bg-neutral-50 dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 focus:outline-none focus:ring-2 focus:ring-blue-500/50 transition-colors resize-none ${
            replyMode === 'internal'
              ? 'border-amber-300 dark:border-amber-800 bg-amber-50/30 dark:bg-amber-950/20'
              : 'border-neutral-200 dark:border-neutral-800'
          }`}
        />

        {/* Bottom Actions Bar (Marks #6, #7) */}
        <div className="flex items-center justify-between gap-2 pt-1">
          <div className="flex items-center gap-2 text-[12px] text-neutral-600 dark:text-neutral-400 font-medium">
            <span>Трудозатраты (мин):</span>
            <input
              type="number"
              value={expenses}
              onChange={e => setExpenses(Number(e.target.value))}
              min={0}
              max={240}
              className="w-16 px-2 py-1 bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg text-neutral-900 dark:text-neutral-100 text-center font-mono font-bold text-[12px]"
            />
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => handleSendAction(27)}
              disabled={submitting || !replyText.trim()}
              className="px-3.5 py-2 text-[12.5px] font-bold text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 border border-neutral-300 dark:border-neutral-700 rounded-lg transition-colors disabled:opacity-40 cursor-pointer"
            >
              В работу
            </button>
            <button
              onClick={() => handleSendAction(29)}
              disabled={submitting || !replyText.trim()}
              className="px-4 py-2 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-[12.5px] font-bold rounded-lg hover:bg-neutral-800 dark:hover:bg-neutral-200 transition-colors disabled:opacity-40 cursor-pointer shadow-xs"
            >
              Выполнить заявку
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
