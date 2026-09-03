import { useState } from 'react';
import type { OutageIncident } from '../../lib/types';
import { resolveOutage, broadcastOutageComment } from '../../lib/tasks';
import { IconShield, IconSparkles } from '../Icons';

interface OutageAlertBannerProps {
  outages: OutageIncident[];
  onSelectTicket: (id: string) => void;
  onFilterTicketIds: (ids: number[]) => void;
  onToast: (toast: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  onOutageResolved: (outageId: string) => void;
}

export default function OutageAlertBanner({
  outages,
  onSelectTicket,
  onFilterTicketIds,
  onToast,
  onOutageResolved,
}: OutageAlertBannerProps) {
  const [broadcastModalOutage, setBroadcastModalOutage] = useState<OutageIncident | null>(null);
  const [broadcastComment, setBroadcastComment] = useState(
    'Добрый день! По данному сервису зафиксирован массовый инцидент. Технические специалисты уже занимаются восстановлением штатной работы.'
  );
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!outages || outages.length === 0) return null;

  const handleResolve = async (outage: OutageIncident) => {
    const ok = await resolveOutage(outage.id);
    if (ok) {
      onToast({ type: 'success', message: `Инцидент "${outage.title}" успешно снят` });
      onOutageResolved(outage.id);
    } else {
      onToast({ type: 'error', message: 'Не удалось снять инцидент' });
    }
  };

  const handleBroadcast = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!broadcastModalOutage || !broadcastComment.trim()) return;

    setIsSubmitting(true);
    try {
      const res = await broadcastOutageComment(broadcastModalOutage.id, broadcastComment.trim());
      onToast({
        type: 'success',
        message: `Массовое оповещение отправлено в ${res.affected_count} заявок`,
      });
      setBroadcastModalOutage(null);
    } catch (err: any) {
      onToast({
        type: 'error',
        message: err.message || 'Ошибка массовой рассылки комментария',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <div className="space-y-2 mb-3">
        {outages.map((outage) => {
          const isCritical = outage.severity === 'critical';
          return (
            <div
              key={outage.id}
              className={`px-4 py-3 rounded-xl border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 shadow-xs transition-colors ${
                isCritical
                  ? 'bg-rose-950/20 dark:bg-rose-950/30 border-rose-500/40 text-rose-200'
                  : 'bg-amber-950/20 dark:bg-amber-950/30 border-amber-500/40 text-amber-200'
              }`}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <span
                  className={`w-2.5 h-2.5 rounded-full shrink-0 animate-pulse ${
                    isCritical ? 'bg-rose-500 shadow-rose-500/50 shadow-sm' : 'bg-amber-500 shadow-amber-500/50 shadow-sm'
                  }`}
                />
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap text-xs">
                    <span className="font-semibold text-[13px] text-neutral-100">
                      {outage.title}
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-mono uppercase tracking-wider ${
                        isCritical
                          ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                          : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                      }`}
                    >
                      {isCritical ? 'Критическая авария' : 'Массовый сбой'}
                    </span>
                    <button
                      type="button"
                      onClick={() => onSelectTicket(String(outage.master_ticket_id))}
                      className="text-neutral-400 hover:text-neutral-200 underline font-mono text-[11px] cursor-pointer"
                      title="Открыть мастер-тикет"
                    >
                      Мастер-тикет #{outage.master_ticket_id}
                    </button>
                  </div>
                  {outage.root_cause_hypothesis && (
                    <p className="text-[11.5px] text-neutral-400 mt-0.5 truncate max-w-2xl">
                      {outage.root_cause_hypothesis}
                    </p>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0 self-end sm:self-center">
                <button
                  type="button"
                  onClick={() => onFilterTicketIds(outage.ticket_ids)}
                  className="px-2.5 py-1 text-xs bg-neutral-800/80 hover:bg-neutral-700 text-neutral-200 rounded-lg border border-neutral-700/80 transition-colors cursor-pointer"
                  title="Отфильтровать только заявки этой аварии"
                >
                  Показать заявки ({outage.ticket_ids.length})
                </button>
                <button
                  type="button"
                  onClick={() => setBroadcastModalOutage(outage)}
                  className="px-2.5 py-1 text-xs bg-blue-600/20 hover:bg-blue-600/30 text-blue-300 border border-blue-500/40 rounded-lg transition-colors cursor-pointer"
                  title="Отправить оповещение заявителям всех связанных заявок"
                >
                  Массовый ответ
                </button>
                <button
                  type="button"
                  onClick={() => handleResolve(outage)}
                  className="px-2.5 py-1 text-xs bg-neutral-900 hover:bg-neutral-800 text-neutral-400 hover:text-neutral-200 border border-neutral-700 rounded-lg transition-colors cursor-pointer"
                  title="Снять аварийный статус"
                >
                  Снять аварию
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Broadcast Comment Modal */}
      {broadcastModalOutage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
          <div className="w-full max-w-lg bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-neutral-800 pb-3">
              <div>
                <h3 className="text-sm font-semibold text-neutral-100 flex items-center gap-2">
                  <IconShield size={16} className="text-neutral-400" />
                  <span>Массовое оповещение заявителей</span>
                </h3>
                <p className="text-xs text-neutral-400 mt-0.5">
                  Будет добавлен комментарий во все {broadcastModalOutage.ticket_ids.length} заявок инцидента #{broadcastModalOutage.master_ticket_id}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setBroadcastModalOutage(null)}
                className="text-neutral-400 hover:text-neutral-200 text-sm cursor-pointer"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleBroadcast} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1.5">
                  Текст комментария заявителям:
                </label>
                <textarea
                  rows={4}
                  value={broadcastComment}
                  onChange={(e) => setBroadcastComment(e.target.value)}
                  className="w-full px-3 py-2 bg-neutral-950 border border-neutral-800 rounded-xl text-xs text-neutral-100 placeholder-neutral-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
                  placeholder="Введите текст сообщения..."
                  required
                />
              </div>

              <div className="flex justify-end gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => setBroadcastModalOutage(null)}
                  className="px-3 py-1.5 text-xs text-neutral-400 hover:text-neutral-200 rounded-lg border border-neutral-800 hover:bg-neutral-800 cursor-pointer"
                >
                  Отмена
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting || !broadcastComment.trim()}
                  className="px-4 py-1.5 text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors cursor-pointer disabled:opacity-50"
                >
                  {isSubmitting ? 'Рассылка...' : `Отправить в ${broadcastModalOutage.ticket_ids.length} заявок`}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
