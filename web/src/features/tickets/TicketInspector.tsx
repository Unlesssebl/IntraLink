import React, { useState, useEffect } from "react";
import {
  X,
  User,
  Phone,
  Mail,
  Clock,
  Send,
  CheckCircle,
  Ban,
  ArrowRight,
  MessageSquare,
  AlertCircle,
} from "lucide-react";
import { Button, Badge, Input, Textarea, Modal } from "@/shared/ui";
import { HostBadge } from "@/features/diagnostics/HostBadge";
import { ticketsApi, TicketDetail } from "@/shared/api";

export interface TicketInspectorProps {
  ticketId: number | null;
  onClose: () => void;
  onTicketUpdated?: () => void;
}

export const TicketInspector: React.FC<TicketInspectorProps> = ({
  ticketId,
  onClose,
  onTicketUpdated,
}) => {
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Comment state
  const [commentText, setCommentText] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [submittingComment, setSubmittingComment] = useState(false);

  // Cancel modal state
  const [cancelModalOpen, setCancelModalOpen] = useState(false);
  const [cancelComment, setCancelComment] = useState("");
  const [cancelling, setCancelling] = useState(false);

  // Take action state
  const [taking, setTaking] = useState(false);

  const fetchTicket = async (id: number) => {
    try {
      setLoading(true);
      setError(null);
      const data = await ticketsApi.get(id);
      setTicket(data);
    } catch (err: any) {
      setError(err?.message || "Не удалось загрузить данные заявки");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (ticketId) {
      fetchTicket(ticketId);
    } else {
      setTicket(null);
    }
  }, [ticketId]);

  const handleTake = async () => {
    if (!ticketId) return;
    try {
      setTaking(true);
      await ticketsApi.take(ticketId);
      await fetchTicket(ticketId);
      if (onTicketUpdated) onTicketUpdated();
    } catch (err: any) {
      alert(`Ошибка: ${err?.message}`);
    } finally {
      setTaking(false);
    }
  };

  const handleCancel = async () => {
    if (!ticketId || !cancelComment.trim()) return;
    try {
      setCancelling(true);
      await ticketsApi.cancel(ticketId, cancelComment.trim(), "Отменена через IntraLink v2");
      setCancelModalOpen(false);
      setCancelComment("");
      await fetchTicket(ticketId);
      if (onTicketUpdated) onTicketUpdated();
    } catch (err: any) {
      alert(`Ошибка: ${err?.message}`);
    } finally {
      setCancelling(false);
    }
  };

  const handleAddComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticketId || !commentText.trim()) return;
    try {
      setSubmittingComment(true);
      await ticketsApi.addComment(ticketId, commentText.trim(), isPrivate);
      setCommentText("");
      await fetchTicket(ticketId);
      if (onTicketUpdated) onTicketUpdated();
    } catch (err: any) {
      alert(`Ошибка: ${err?.message}`);
    } finally {
      setSubmittingComment(false);
    }
  };

  if (!ticketId) return null;

  return (
    <>
      <div className="fixed inset-y-0 right-0 w-full max-w-2xl bg-[#0f1218] border-l border-[#232938] shadow-2xl z-40 flex flex-col animate-in slide-in-from-right duration-200">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#212634] bg-[#121620]">
          <div className="flex items-center gap-3">
            <span className="font-mono text-sm font-bold text-indigo-400">
              #{ticketId}
            </span>
            {ticket && (
              <Badge variant={ticket.status_name?.includes("Отмен") ? "danger" : "accent"}>
                {ticket.status_name || "Новая"}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              loading={taking}
              icon={<CheckCircle className="w-3.5 h-3.5 text-emerald-400" />}
              onClick={handleTake}
            >
              В работу
            </Button>
            <Button
              size="sm"
              variant="danger"
              icon={<Ban className="w-3.5 h-3.5" />}
              onClick={() => setCancelModalOpen(true)}
            >
              Отменить
            </Button>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-200 p-1.5 rounded hover:bg-[#1a1f2c] transition-colors ml-2"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {loading ? (
            <div className="flex items-center justify-center py-20 text-xs text-slate-400">
              Загрузка заявки #{ticketId}...
            </div>
          ) : error ? (
            <div className="p-4 bg-red-950/40 border border-red-800/60 rounded-lg text-xs text-red-300 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : ticket ? (
            <>
              {/* Ticket Title & Service */}
              <div>
                <h3 className="text-sm font-semibold text-slate-100 leading-snug">
                  {ticket.name}
                </h3>
                <div className="text-xs text-slate-400 mt-1 flex items-center gap-2">
                  <span>Сервис:</span>
                  <span className="text-slate-300 font-medium">
                    {ticket.service_name || "Общий сервис"}
                  </span>
                </div>
              </div>

              {/* Applicant & Host Grid */}
              <div className="grid grid-cols-2 gap-3 p-3.5 bg-[#141822] border border-[#232938] rounded-lg text-xs">
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2 text-slate-300">
                    <User className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    <span className="font-medium truncate">
                      {ticket.applicant_name || "Не указан"}
                    </span>
                  </div>
                  {ticket.applicant_phone && (
                    <div className="flex items-center gap-2 text-slate-400">
                      <Phone className="w-3.5 h-3.5 shrink-0" />
                      <span>{ticket.applicant_phone}</span>
                    </div>
                  )}
                  {ticket.applicant_email && (
                    <div className="flex items-center gap-2 text-slate-400">
                      <Mail className="w-3.5 h-3.5 shrink-0" />
                      <span className="truncate">{ticket.applicant_email}</span>
                    </div>
                  )}
                </div>

                <div className="space-y-1.5 border-l border-[#232938] pl-3.5">
                  <div className="text-[11px] text-slate-400">Рабочая станция:</div>
                  <HostBadge host={ticket.pc_name} autoCheck />
                  <div className="text-[11px] text-slate-400 flex items-center gap-1.5 pt-1">
                    <Clock className="w-3 h-3" />
                    <span>{new Date(ticket.created).toLocaleString("ru-RU")}</span>
                  </div>
                </div>
              </div>

              {/* Description */}
              <div className="space-y-1.5">
                <div className="text-xs font-semibold text-slate-300">
                  Описание проблемы
                </div>
                <div className="p-3 bg-[#131620] border border-[#222736] rounded-lg text-xs text-slate-200 whitespace-pre-wrap leading-relaxed">
                  {ticket.description || "Описание отсутствует"}
                </div>
              </div>

              {/* Comments Timeline */}
              <div className="space-y-3 pt-2">
                <div className="flex items-center justify-between text-xs font-semibold text-slate-300">
                  <span className="flex items-center gap-1.5">
                    <MessageSquare className="w-3.5 h-3.5 text-indigo-400" />
                    История переписки ({ticket.comments?.length || 0})
                  </span>
                </div>

                <div className="space-y-2.5">
                  {ticket.comments?.map((c) => (
                    <div
                      key={c.id}
                      className={`p-3 rounded-lg border text-xs space-y-1.5 ${
                        c.is_private
                          ? "bg-amber-950/20 border-amber-800/40 text-amber-200"
                          : "bg-[#141822] border-[#242a39] text-slate-200"
                      }`}
                    >
                      <div className="flex items-center justify-between text-[11px]">
                        <span className="font-semibold text-slate-300">
                          {c.author_name}
                        </span>
                        <div className="flex items-center gap-2">
                          {c.is_private && (
                            <Badge variant="warning">Внутренняя заметка</Badge>
                          )}
                          <span className="text-slate-500 font-mono">
                            {new Date(c.created).toLocaleTimeString("ru-RU", {
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                        </div>
                      </div>
                      <p className="whitespace-pre-wrap leading-relaxed">{c.text}</p>
                    </div>
                  ))}

                  {(!ticket.comments || ticket.comments.length === 0) && (
                    <div className="text-center py-6 text-xs text-slate-500 italic">
                      Комментариев пока нет
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </div>

        {/* Comment input footer */}
        {ticket && (
          <form
            onSubmit={handleAddComment}
            className="p-4 border-t border-[#212634] bg-[#121620] space-y-2.5"
          >
            <Textarea
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              placeholder="Напишите ответ заявителю или внутренний комментарий..."
              rows={2}
            />
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={isPrivate}
                  onChange={(e) => setIsPrivate(e.target.checked)}
                  className="rounded border-[#252b3b] bg-[#141822] text-indigo-600 focus:ring-indigo-500"
                />
                <span>Внутренняя заметка (скрыто от заявителя)</span>
              </label>

              <Button
                type="submit"
                size="sm"
                variant="primary"
                loading={submittingComment}
                disabled={!commentText.trim()}
                icon={<Send className="w-3 h-3" />}
              >
                Отправить
              </Button>
            </div>
          </form>
        )}
      </div>

      {/* Cancel Ticket Modal */}
      <Modal
        isOpen={cancelModalOpen}
        onClose={() => setCancelModalOpen(false)}
        title={`Отмена заявки #${ticketId}`}
        description="Заявитель получит уведомление об отмене. Обязательно укажите причину."
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setCancelModalOpen(false)}
            >
              Назад
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={cancelling}
              disabled={!cancelComment.trim()}
              onClick={handleCancel}
            >
              Подтвердить отмену
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Textarea
            label="Причина отмены / Комментарий заявителю"
            value={cancelComment}
            onChange={(e) => setCancelComment(e.target.value)}
            placeholder="Укажите, почему заявка отменяется (например: дубликат заявки #..., либо перенаправление в сервис 1С)..."
            rows={4}
            autoFocus
          />
        </div>
      </Modal>
    </>
  );
};
