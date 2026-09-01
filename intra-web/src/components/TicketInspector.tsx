import { useState, useEffect } from 'react';
import type { Ticket, Status, Priority } from '../data/mock';
import { statusConfig, priorityConfig, operators, categoryLabel } from '../data/mock';
import { fetchDiagnostics, applyTask, enqueueExecution, searchRAG } from '../lib/tasks';
import type { RAGMatchItem } from '../lib/types';

interface Props {
  ticket: Ticket;
  onClose: () => void;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

function formatTime(d: Date) {
  return d.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function getSlaClass(deadline: Date) {
  const ms = deadline.getTime() - Date.now();
  const h = ms / 3600000;
  if (h < 0) return 'text-red-600 dark:text-red-400 font-semibold';
  if (h < 1) return 'text-red-500 dark:text-red-400';
  if (h < 3) return 'text-amber-600 dark:text-amber-400';
  return 'text-green-600 dark:text-green-400';
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
    ok: 'bg-green-50 text-green-700 dark:bg-green-950/50 dark:text-green-300',
    fail: 'bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300',
    checking: 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300',
    idle: 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400',
  }[status];
  const label = { ok: 'ОК', fail: 'Недоступен', checking: 'Проверка...', idle: '—' }[status];
  return <span className={`text-[11px] font-mono px-1.5 py-0.5 rounded ${cls}`}>{label}</span>;
}

export default function TicketInspector({ ticket, onClose, onUpdateTicket, onToast }: Props) {
  const [replyMode, setReplyMode] = useState<'reply' | 'internal'>('reply');
  const [replyText, setReplyText] = useState('');
  const [diagStatus, setDiagStatus] = useState<Record<string, DiagStatus>>({
    ping: 'idle', smb: 'idle', winrm: 'idle',
  });
  const [statusOpen, setStatusOpen] = useState(false);
  const [priorityOpen, setPriorityOpen] = useState(false);
  const [assigneeOpen, setAssigneeOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [executingAction, setExecutingAction] = useState<string | null>(null);
  const [ragLoading, setRagLoading] = useState(false);
  const [ragMatches, setRagMatches] = useState<RAGMatchItem[]>([]);

  const rawId = parseInt(ticket.id.replace(/\D/g, ''), 10);

  // Auto-search RAG on ticket change
  useEffect(() => {
    setRagMatches([]);
  }, [ticket.id]);

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
      onToast({ type: 'info', message: `Диагностика ${ticket.host} завершена` });
    } catch {
      setDiagStatus({ ping: 'fail', smb: 'fail', winrm: 'fail' });
    }
  };

  const loadRagMatches = async () => {
    setRagLoading(true);
    try {
      const res = await searchRAG(ticket.title, 3);
      setRagMatches(res.matches || []);
      if (!res.matches || res.matches.length === 0) {
        onToast({ type: 'info', message: 'В базе знаний RAG нет точных совпадений' });
      }
    } catch (err) {
      console.warn('RAG search error:', err);
    } finally {
      setRagLoading(false);
    }
  };

  const handleQuickAction = async (actionType: string) => {
    setExecutingAction(actionType);
    try {
      if (actionType === 'wlan') {
        await enqueueExecution({
          action: 'grant_wlan',
          task_id: rawId,
          params: { identity: ticket.requesterName },
          auto_close_ticket: true,
        });
        onToast({ type: 'success', message: '⚡ Задача выдачи Wi-Fi поставлена в Execution Broker' });
        onUpdateTicket(ticket.id, { status: 'resolved' });
      } else if (actionType === 'create_user') {
        await enqueueExecution({
          action: 'create_user',
          task_id: rawId,
          params: {},
          auto_close_ticket: true,
        });
        onToast({ type: 'success', message: '⚡ Создание УЗ в AD передано в Execution Broker' });
        onUpdateTicket(ticket.id, { status: 'resolved' });
      } else if (actionType === 'redirect') {
        const comm = ticket.aiSuggestion || `Заявка отменена, т. к. создана не в подходящем разделе. Требуется оставить заявку в подходящем разделе: ${ticket.targetServiceName || 'соответствующий сервис'}.`;
        await applyTask(rawId, {
          status_id: 30,
          comment: comm,
          minutes: 5,
        });
        onToast({ type: 'success', message: `↩️ Заявка перенаправлена в ${ticket.targetServiceName || 'целевой раздел'}` });
        onUpdateTicket(ticket.id, { status: 'resolved' });
      } else if (actionType === 'hardware') {
        const comm = ticket.aiSuggestion || 'Приносите системный блок / ноутбук в АБК-3, каб. 112 на аппаратную диагностику и обслуживание.';
        await applyTask(rawId, {
          status_id: 48,
          comment: comm,
          minutes: 10,
        });
        onToast({ type: 'success', message: '🛠️ Переведено в Статус 48 (Ожидание устройства, каб. 112)' });
        onUpdateTicket(ticket.id, { status: 'waiting' });
      } else if (actionType === 'duplicate') {
        const masterId = ticket.duplicateInfo?.master_task_id || '';
        const comm = `Заявка отменена как повторная (дубликат инцидента #${masterId}). Все работы ведутся в основной заявке.`;
        await applyTask(rawId, {
          status_id: 30,
          comment: comm,
          minutes: 5,
        });
        onToast({ type: 'success', message: `❌ Заявка отменена как дубликат #${masterId}` });
        onUpdateTicket(ticket.id, { status: 'resolved' });
      }
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка выполнения: ${err.message || err}` });
    } finally {
      setExecutingAction(null);
    }
  };

  const handleSend = async (close = false) => {
    if (!replyText.trim()) return;
    const targetStatusId = close ? 4 : (ticket.status === 'new' ? 2 : undefined);

    if (rawId && !isNaN(rawId)) {
      try {
        await applyTask(rawId, {
          status_id: targetStatusId || 2,
          comment: replyText,
          minutes: 15,
          is_private: replyMode === 'internal',
        });
      } catch (err: any) {
        console.warn('API error:', err);
      }
    }

    onUpdateTicket(ticket.id, {
      timeline: [
        ...ticket.timeline,
        {
          id: `t${Date.now()}`,
          type: replyMode,
          author: 'Оператор',
          content: replyText,
          timestamp: new Date(),
        },
      ],
      ...(close ? { status: 'resolved' } : (ticket.status === 'new' ? { status: 'in_progress' } : {})),
    });
    setReplyText('');
    onToast({ type: 'success', message: replyMode === 'reply' ? 'Ответ отправлен заявителю' : 'Внутренняя заметка сохранена' });
    if (close) onUpdateTicket(ticket.id, { status: 'resolved' });
  };

  const copyToClipboard = (v: string) => {
    navigator.clipboard.writeText(v).then(() =>
      onToast({ type: 'info', message: 'Скопировано в буфер' })
    );
  };

  const panelClass = expanded
    ? 'fixed inset-0 z-30 flex flex-col bg-white dark:bg-neutral-950'
    : 'w-[460px] shrink-0 flex flex-col border-l border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950';

  return (
    <div className={panelClass}>
      {/* Header */}
      <div className="px-5 pt-4 pb-3 border-b border-neutral-100 dark:border-neutral-800 shrink-0">
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M10 4l-4 4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            <span className="font-mono text-[12px] text-neutral-400 dark:text-neutral-500">{ticket.id}</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setExpanded(e => !e)}
              className="w-6 h-6 flex items-center justify-center rounded text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors"
            >
              {expanded ? (
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                  <path d="M8.5 1.5v3h3M4.5 11.5v-3h-3M8.5 11.5v-3h3M4.5 1.5v3h-3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              ) : (
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                  <path d="M1.5 4.5h3v-3M11.5 4.5h-3v-3M1.5 8.5h3v3M11.5 8.5h-3v3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
            </button>
          </div>
        </div>
        <h2 className="text-[15px] font-semibold text-neutral-900 dark:text-neutral-50 leading-snug">
          {ticket.title}
        </h2>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Duplicate Banner */}
        {(ticket.isDuplicate || ticket.ruleType === 'duplicate_task') && (
          <div className="mx-5 mt-4 bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 rounded p-3">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-amber-600 dark:text-amber-400 text-sm font-bold">⚠️ Обнаружен дубликат</span>
              {ticket.duplicateInfo?.master_task_id && (
                <span className="text-[11px] font-mono bg-amber-100 dark:bg-amber-900/60 text-amber-800 dark:text-amber-200 px-1.5 py-0.5 rounded">
                  Master #{ticket.duplicateInfo.master_task_id}
                </span>
              )}
            </div>
            <p className="text-[12px] text-amber-800 dark:text-amber-200 mb-2">
              Данная заявка дублирует ранее созданный инцидент от того же заявителя.
            </p>
            <button
              onClick={() => handleQuickAction('duplicate')}
              disabled={executingAction !== null}
              className="px-2.5 py-1 bg-amber-600 hover:bg-amber-700 text-white text-[11px] font-medium rounded transition-colors disabled:opacity-50"
            >
              ❌ Отменить как дубликат (Статус 30)
            </button>
          </div>
        )}

        {/* Smart Actions Bar */}
        <div className="mx-5 mt-3 flex flex-wrap gap-1.5">
          {(ticket.ruleType === 'wlan_access' || ticket.templateKey === 'wifi_access') && (
            <button
              onClick={() => handleQuickAction('wlan')}
              disabled={executingAction !== null}
              className="px-2.5 py-1 bg-green-600 hover:bg-green-700 text-white text-[11px] font-medium rounded flex items-center gap-1.5 shadow-sm transition-colors disabled:opacity-50"
            >
              ⚡ Выдать Wi-Fi в AD (Статус 29)
            </button>
          )}

          {(ticket.ruleType === 'user_created' || ticket.templateKey === 'user_created') && (
            <button
              onClick={() => handleQuickAction('create_user')}
              disabled={executingAction !== null}
              className="px-2.5 py-1 bg-blue-600 hover:bg-blue-700 text-white text-[11px] font-medium rounded flex items-center gap-1.5 shadow-sm transition-colors disabled:opacity-50"
            >
              ⚡ Создать УЗ в AD
            </button>
          )}

          {(ticket.isRedirect || ticket.ruleType?.startsWith('redirect')) && (
            <button
              onClick={() => handleQuickAction('redirect')}
              disabled={executingAction !== null}
              className="px-2.5 py-1 bg-amber-600 hover:bg-amber-700 text-white text-[11px] font-medium rounded flex items-center gap-1.5 shadow-sm transition-colors disabled:opacity-50"
            >
              ↩️ Редирект в {ticket.targetServiceName || 'сервис'}
            </button>
          )}

          {ticket.ruleType === 'hardware_repair' && (
            <button
              onClick={() => handleQuickAction('hardware')}
              disabled={executingAction !== null}
              className="px-2.5 py-1 bg-purple-600 hover:bg-purple-700 text-white text-[11px] font-medium rounded flex items-center gap-1.5 shadow-sm transition-colors disabled:opacity-50"
            >
              🛠️ В каб. 112 (Статус 48)
            </button>
          )}
        </div>

        {/* AI suggestion */}
        {ticket.aiConfidence !== null && ticket.aiSuggestion && (
          <div className="mx-5 mt-3 bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900 rounded p-3">
            <div className="flex items-center justify-between mb-1.5">
              <div className="flex items-center gap-2">
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none" className="text-blue-600 dark:text-blue-400 shrink-0">
                  <path d="M6.5 1.5L8 5H11.5L8.5 7.2 9.5 10.5 6.5 8.5 3.5 10.5 4.5 7.2 1.5 5H5L6.5 1.5Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/>
                </svg>
                <span className="text-[11px] font-semibold text-blue-700 dark:text-blue-300">
                  AI-подсказка · {ticket.ruleType || 'Rule Engine'}
                </span>
              </div>
              <span className="text-[11px] font-mono text-blue-600 dark:text-blue-400">
                {ticket.expenses ? `${ticket.expenses} мин` : ''}
              </span>
            </div>
            <p className="text-[12px] text-blue-700 dark:text-blue-300 leading-relaxed whitespace-pre-wrap">{ticket.aiSuggestion}</p>
            <div className="flex items-center gap-3 mt-2">
              <button
                onClick={() => { setReplyText(ticket.aiSuggestion!); setReplyMode('reply'); }}
                className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline font-medium"
              >
                Вставить в ответ →
              </button>
              <button
                onClick={loadRagMatches}
                disabled={ragLoading}
                className="text-[11px] text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200 font-medium"
              >
                {ragLoading ? 'Поиск RAG...' : '🧠 Найти аналогичные решения'}
              </button>
            </div>
          </div>
        )}

        {/* RAG Matches List */}
        {ragMatches.length > 0 && (
          <div className="mx-5 mt-3 border border-indigo-200 dark:border-indigo-900 bg-indigo-50/50 dark:bg-indigo-950/20 rounded p-3 space-y-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-indigo-700 dark:text-indigo-300">
              Похожие решения в базе знаний RAG
            </span>
            {ragMatches.map(m => (
              <div key={m.task_id} className="text-[12px] border-t border-indigo-100 dark:border-indigo-900/50 pt-1.5">
                <div className="flex items-center justify-between text-indigo-900 dark:text-indigo-200 font-medium mb-0.5">
                  <span>#{m.task_id} · {m.name}</span>
                  <span className="font-mono text-[11px] text-indigo-600 dark:text-indigo-400">{m.similarity_pct}%</span>
                </div>
                <p className="text-neutral-600 dark:text-neutral-400 line-clamp-2 text-[11px]">{m.solution}</p>
                <button
                  onClick={() => { setReplyText(m.solution); setReplyMode('reply'); }}
                  className="mt-1 text-[10px] text-indigo-600 dark:text-indigo-400 hover:underline"
                >
                  Скопировать решение в ответ →
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Properties */}
        <div className="px-5 mt-4">
          <div className="space-y-0.5">
            <PropRow label="Статус">
              <div className="relative">
                <button
                  onClick={() => setStatusOpen(o => !o)}
                  className={`text-[12px] px-2 py-0.5 rounded-sm font-medium ${statusConfig[ticket.status].className}`}
                >
                  {statusConfig[ticket.status].label}
                </button>
                {statusOpen && (
                  <div className="absolute left-0 top-6 z-20 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded shadow-lg py-1 min-w-[140px]">
                    {(['new', 'in_progress', 'waiting', 'resolved'] as Status[]).map(s => (
                      <button
                        key={s}
                        onClick={() => { onUpdateTicket(ticket.id, { status: s }); setStatusOpen(false); }}
                        className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800 text-[12px]"
                      >
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${priorityConfig.critical.dotClass}`} style={{ background: undefined }} />
                        <span className={`px-1.5 py-0.5 rounded-sm font-medium ${statusConfig[s].className}`}>
                          {statusConfig[s].label}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </PropRow>

            <PropRow label="Приоритет">
              <div className="relative">
                <button
                  onClick={() => setPriorityOpen(o => !o)}
                  className={`text-[12px] px-2 py-0.5 rounded-sm font-medium flex items-center gap-1.5 ${priorityConfig[ticket.priority].className}`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${priorityConfig[ticket.priority].dotClass}`} />
                  {priorityConfig[ticket.priority].label}
                </button>
                {priorityOpen && (
                  <div className="absolute left-0 top-6 z-20 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded shadow-lg py-1 min-w-[140px]">
                    {(['critical', 'high', 'medium', 'low'] as Priority[]).map(p => (
                      <button
                        key={p}
                        onClick={() => { onUpdateTicket(ticket.id, { priority: p }); setPriorityOpen(false); }}
                        className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800 text-[12px]"
                      >
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${priorityConfig[p].dotClass}`} />
                        <span>{priorityConfig[p].label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </PropRow>

            <PropRow label="Исполнитель">
              <div className="relative">
                <button
                  onClick={() => setAssigneeOpen(o => !o)}
                  className="text-[12px] text-neutral-700 dark:text-neutral-300 hover:text-neutral-900 dark:hover:text-neutral-100 transition-colors"
                >
                  {ticket.assigneeId
                    ? operators.find(o => o.id === ticket.assigneeId)?.name ?? '—'
                    : <span className="text-blue-600 dark:text-blue-400">+ Назначить</span>
                  }
                </button>
                {assigneeOpen && (
                  <div className="absolute left-0 top-6 z-20 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded shadow-lg py-1 min-w-[160px]">
                    <button
                      onClick={() => { onUpdateTicket(ticket.id, { assigneeId: 'op1' }); setAssigneeOpen(false); onToast({ type: 'success', message: 'Заявка взята в работу' }); }}
                      className="w-full px-3 py-1.5 text-left text-[12px] text-blue-600 hover:bg-neutral-50 dark:hover:bg-neutral-800 font-medium"
                    >
                      Взять себе
                    </button>
                    {operators.map(op => (
                      <button
                        key={op.id}
                        onClick={() => { onUpdateTicket(ticket.id, { assigneeId: op.id }); setAssigneeOpen(false); }}
                        className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800"
                      >
                        <div className="w-5 h-5 bg-neutral-200 dark:bg-neutral-700 rounded-full flex items-center justify-center text-[9px] font-semibold text-neutral-600 dark:text-neutral-300">
                          {op.initials}
                        </div>
                        <span className="text-[12px] text-neutral-700 dark:text-neutral-300">{op.name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </PropRow>

            <PropRow label="Заявитель">
              <span className="text-[12px] text-neutral-700 dark:text-neutral-300">{ticket.requesterName}</span>
            </PropRow>

            <PropRow label="Телефон">
              <span className="text-[12px] font-mono text-neutral-600 dark:text-neutral-400">{ticket.requesterPhone || '—'}</span>
            </PropRow>

            <PropRow label="Хост / IP">
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-[11px] bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 px-1.5 py-0.5 rounded">
                  {ticket.host || 'Не указан'}
                </span>
                {ticket.ip && (
                  <span className="font-mono text-[11px] bg-neutral-100 dark:bg-neutral-800 text-neutral-500 dark:text-neutral-500 px-1.5 py-0.5 rounded">
                    {ticket.ip}
                  </span>
                )}
                {ticket.host && (
                  <button onClick={() => copyToClipboard(ticket.host)} className="text-neutral-300 hover:text-neutral-500 dark:text-neutral-700 dark:hover:text-neutral-400 transition-colors">
                    <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                      <rect x="3.5" y="3.5" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.2"/>
                      <path d="M1.5 7.5V1.5h6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                    </svg>
                  </button>
                )}
              </div>
            </PropRow>

            <PropRow label="Категория">
              <span className="text-[12px] text-neutral-700 dark:text-neutral-300">{categoryLabel[ticket.category]}</span>
            </PropRow>

            <PropRow label="SLA">
              <span className={`text-[12px] font-mono ${getSlaClass(ticket.slaDeadline)}`}>
                {formatSla(ticket.slaDeadline)}
              </span>
            </PropRow>

            <PropRow label="Создана">
              <span className="text-[12px] text-neutral-500 dark:text-neutral-400">{formatTime(ticket.createdAt)}</span>
            </PropRow>
          </div>
        </div>

        {/* Attachments Section */}
        {ticket.attachments && ticket.attachments.length > 0 && (
          <div className="mx-5 mt-4 border border-neutral-200 dark:border-neutral-800 rounded p-3">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600 block mb-2">
              Вложения ({ticket.attachments.length})
            </span>
            <div className="space-y-1.5">
              {ticket.attachments.map(att => (
                <a
                  key={att.id}
                  href={att.url || `/admin/api/attachments/${att.id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between p-2 rounded bg-neutral-50 dark:bg-neutral-900 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors text-[12px]"
                >
                  <span className="truncate font-medium text-blue-600 dark:text-blue-400">{att.name}</span>
                  <span className="text-[11px] text-neutral-400 font-mono shrink-0 ml-2">
                    {att.size ? `${Math.round(att.size / 1024)} КБ` : 'Скачать'}
                  </span>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* Network diagnostics */}
        <div className="mx-5 mt-4 border border-neutral-200 dark:border-neutral-800 rounded p-3">
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">
              Диагностика хоста
            </span>
            <button
              onClick={runDiag}
              className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline font-medium"
            >
              Обновить
            </button>
          </div>
          <div className="space-y-1.5">
            {[
              { name: 'Ping', key: 'ping' },
              { name: 'SMB/445', key: 'smb' },
              { name: 'WinRM/5985', key: 'winrm' },
            ].map(({ name, key }) => (
              <div key={key} className="flex items-center justify-between">
                <span className="font-mono text-[12px] text-neutral-600 dark:text-neutral-400">{name}</span>
                <DiagBadge status={diagStatus[key] as DiagStatus} />
              </div>
            ))}
          </div>
        </div>

        {/* Description */}
        <div className="px-5 mt-5">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600 mb-2">
            Описание
          </p>
          <div className="text-[13px] text-neutral-700 dark:text-neutral-300 leading-relaxed whitespace-pre-wrap font-sans">
            {ticket.description.replace(/[#*`]/g, '').replace(/\n+/g, '\n').trim()}
          </div>
        </div>

        {/* Timeline */}
        <div className="px-5 mt-6 mb-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600 mb-3">
            История
          </p>
          <div className="space-y-3">
            {ticket.timeline.map(event => (
              <div key={event.id} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${
                    event.type === 'reply' ? 'bg-blue-400' :
                    event.type === 'internal' ? 'bg-amber-400' :
                    event.type === 'status_change' ? 'bg-neutral-400' :
                    'bg-neutral-300'
                  }`} />
                  <div className="w-px flex-1 bg-neutral-100 dark:bg-neutral-800 mt-1" />
                </div>
                <div className="flex-1 pb-3 min-w-0">
                  <div className="flex items-baseline gap-2 mb-0.5">
                    <span className="text-[12px] font-medium text-neutral-700 dark:text-neutral-300">{event.author}</span>
                    <span className="text-[11px] text-neutral-400 dark:text-neutral-600">{formatTime(event.timestamp)}</span>
                    {event.type === 'internal' && (
                      <span className="text-[10px] bg-amber-50 text-amber-600 dark:bg-amber-950/40 dark:text-amber-400 px-1 rounded font-medium">внутренняя</span>
                    )}
                  </div>
                  <p className="text-[12px] text-neutral-600 dark:text-neutral-400 leading-relaxed">{event.content}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Reply editor */}
      <div className="border-t border-neutral-200 dark:border-neutral-800 p-4 shrink-0 bg-white dark:bg-neutral-950">
        <div className="flex gap-1 mb-2.5">
          {(['reply', 'internal'] as const).map(mode => (
            <button
              key={mode}
              onClick={() => setReplyMode(mode)}
              className={`px-2.5 py-1 rounded text-[12px] font-medium transition-colors ${
                replyMode === mode
                  ? 'bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900'
                  : 'text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800'
              }`}
            >
              {mode === 'reply' ? 'Ответ заявителю' : 'Внутренняя заметка'}
            </button>
          ))}
        </div>
        <textarea
          value={replyText}
          onChange={e => setReplyText(e.target.value)}
          placeholder={replyMode === 'reply' ? 'Напишите ответ заявителю...' : 'Внутренняя заметка для команды...'}
          rows={3}
          className={`w-full px-3 py-2 text-[13px] rounded border bg-neutral-50 dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 dark:placeholder-neutral-600 focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500 transition-colors resize-none ${
            replyMode === 'internal'
              ? 'border-amber-200 dark:border-amber-900 bg-amber-50/50 dark:bg-amber-950/20'
              : 'border-neutral-200 dark:border-neutral-800'
          }`}
        />
        <div className="flex items-center gap-2 mt-2">
          <button
            onClick={() => handleSend(false)}
            disabled={!replyText.trim()}
            className="px-3 py-1.5 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-[12px] font-medium rounded hover:bg-neutral-700 dark:hover:bg-neutral-300 disabled:opacity-40 transition-colors"
          >
            Отправить
          </button>
          <button
            onClick={() => handleSend(true)}
            disabled={!replyText.trim()}
            className="px-3 py-1.5 text-[12px] font-medium text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100 border border-neutral-200 dark:border-neutral-700 rounded hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40 transition-colors"
          >
            Отправить и закрыть
          </button>
        </div>
      </div>
    </div>
  );
}

function PropRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center min-h-7">
      <span className="w-28 text-[12px] text-neutral-400 dark:text-neutral-600 shrink-0">{label}</span>
      <div className="flex-1">{children}</div>
    </div>
  );
}
