import React, { useState, useEffect } from "react";
import {
  X,
  Maximize2,
  Minimize2,
  ExternalLink,
  User,
  Phone,
  Mail,
  MapPin,
  Clock,
  Paperclip,
  FileText,
  Image as ImageIcon,
  MessageSquare,
  AlertCircle,
  Building,
  Hash,
} from "lucide-react";
import { Badge, StatusDot, Lightbox } from "@/shared/ui";
import { HostBadge } from "@/features/diagnostics/HostBadge";
import { RAGSuggestion } from "@/features/knowledge-base/RAGSuggestion";
import { ActionDock } from "./ActionDock";
import {
  ticketsApi,
  TicketDetail,
  TicketLifetimeEvent,
  TicketAttachment,
} from "@/shared/api";

export interface TicketInspectorProps {
  ticketId: number | null;
  onClose: () => void;
  onTicketUpdated?: () => void;
  isFullscreen?: boolean;
  onToggleFullscreen?: () => void;
}

export const TicketInspector: React.FC<TicketInspectorProps> = ({
  ticketId,
  onClose,
  onTicketUpdated,
  isFullscreen = false,
  onToggleFullscreen,
}) => {
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [events, setEvents] = useState<TicketLifetimeEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState(false);

  // Lightbox preview for screenshots
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [lightboxAlt, setLightboxAlt] = useState("");

  // Comment draft synced with RAG suggestions
  const [commentDraft, setCommentDraft] = useState("");

  const loadTicketData = async (id: number) => {
    try {
      setLoading(true);
      setError(null);
      const [ticketData, lifetimeData] = await Promise.all([
        ticketsApi.get(id),
        ticketsApi.getLifetime(id).catch(() => []),
      ]);
      setTicket(ticketData);
      setEvents(lifetimeData);
    } catch (err: any) {
      setError(err?.message || "Не удалось загрузить данные заявки");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (ticketId) {
      loadTicketData(ticketId);
    } else {
      setTicket(null);
      setEvents([]);
    }
  }, [ticketId]);

  // Handle action callbacks
  const handleTake = async () => {
    if (!ticketId) return;
    try {
      setBusyAction(true);
      await ticketsApi.take(ticketId);
      await loadTicketData(ticketId);
      if (onTicketUpdated) onTicketUpdated();
    } catch (err: any) {
      alert(`Ошибка: ${err?.message}`);
    } finally {
      setBusyAction(false);
    }
  };

  const handleResolve = async (comment: string) => {
    if (!ticketId) return;
    try {
      setBusyAction(true);
      await ticketsApi.resolve(ticketId, comment);
      await loadTicketData(ticketId);
      if (onTicketUpdated) onTicketUpdated();
    } catch (err: any) {
      alert(`Ошибка закрытия: ${err?.message}`);
    } finally {
      setBusyAction(false);
    }
  };

  const handleDuplicate = async (masterId: number, comment: string) => {
    if (!ticketId) return;
    try {
      setBusyAction(true);
      await ticketsApi.cancel(ticketId, comment, `Дубликат заявки #${masterId}`);
      await loadTicketData(ticketId);
      if (onTicketUpdated) onTicketUpdated();
    } catch (err: any) {
      alert(`Ошибка отмены дубликата: ${err?.message}`);
    } finally {
      setBusyAction(false);
    }
  };

  const handleRedirect = async (serviceId: number, comment: string) => {
    if (!ticketId) return;
    try {
      setBusyAction(true);
      await ticketsApi.redirect(ticketId, serviceId, comment);
      await loadTicketData(ticketId);
      if (onTicketUpdated) onTicketUpdated();
    } catch (err: any) {
      alert(`Ошибка перенаправления: ${err?.message}`);
    } finally {
      setBusyAction(false);
    }
  };

  const handleAddComment = async (comment: string, isPrivate: boolean) => {
    if (!ticketId) return;
    try {
      setBusyAction(true);
      await ticketsApi.addComment(ticketId, comment, isPrivate);
      await loadTicketData(ticketId);
      if (onTicketUpdated) onTicketUpdated();
    } catch (err: any) {
      alert(`Ошибка добавления комментария: ${err?.message}`);
    } finally {
      setBusyAction(false);
    }
  };

  const isImageAttachment = (filename: string) => {
    const ext = filename.split(".").pop()?.toLowerCase() || "";
    return ["png", "jpg", "jpeg", "gif", "bmp", "webp"].includes(ext);
  };

  const handleAttachmentClick = (att: TicketAttachment) => {
    if (!ticketId) return;
    const url = ticketsApi.getAttachmentUrl(ticketId, att.id || att.Id);
    if (isImageAttachment(att.name || att.Name || "")) {
      setLightboxSrc(url);
      setLightboxAlt(att.name || att.Name || "Вложение заявки");
    } else {
      window.open(url, "_blank");
    }
  };

  if (!ticketId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-slate-500 bg-[#090b10]">
        <Hash className="w-12 h-12 text-slate-700 mb-3 stroke-[1.5]" />
        <h3 className="text-sm font-semibold text-slate-300">
          Заявка не выбрана
        </h3>
        <p className="text-xs text-slate-500 max-w-sm mt-1">
          Выберите заявку из очереди слева или воспользуйтесь клавишами{" "}
          <kbd className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700 font-mono text-[10px]">
            J
          </kbd>{" "}
          /{" "}
          <kbd className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700 font-mono text-[10px]">
            K
          </kbd>
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#0a0d14]">
        {/* Top Header */}
        <div className="px-4 py-3 border-b border-[#1e2330] bg-[#0d1017] flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            {ticket && (
              <StatusDot
                statusId={ticket.status_id}
                statusName={ticket.status_name}
                size="md"
              />
            )}
            <span className="font-mono text-sm font-bold text-indigo-400 shrink-0">
              #{ticketId}
            </span>
            {ticket && (
              <span className="text-xs font-semibold text-slate-100 truncate">
                {ticket.name || "(Без темы)"}
              </span>
            )}
            {ticket?.status_name && (
              <Badge
                variant={
                  ticket.status_name.toLowerCase().includes("отмен")
                    ? "danger"
                    : ticket.status_name.toLowerCase().includes("работ")
                    ? "success"
                    : "accent"
                }
              >
                {ticket.status_name}
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            {onToggleFullscreen && (
              <button
                type="button"
                onClick={onToggleFullscreen}
                className="text-slate-400 hover:text-slate-200 p-1.5 rounded hover:bg-[#1a1f2c] transition-colors"
                title={isFullscreen ? "Свернуть панель" : "На весь экран"}
              >
                {isFullscreen ? (
                  <Minimize2 className="w-3.5 h-3.5" />
                ) : (
                  <Maximize2 className="w-3.5 h-3.5" />
                )}
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="text-slate-400 hover:text-rose-400 p-1.5 rounded hover:bg-[#1a1f2c] transition-colors"
              title="Закрыть (Esc)"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
          {loading ? (
            <div className="flex items-center justify-center py-20 text-slate-400 text-xs">
              Загрузка карточки заявки #{ticketId}...
            </div>
          ) : error ? (
            <div className="p-4 bg-red-950/40 border border-red-800/60 rounded-lg text-red-300 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : ticket ? (
            <>
              {/* Context Cards: Applicant + Workstation */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* Applicant Card */}
                <div className="p-3 bg-[#11141d] border border-[#202636] rounded-lg space-y-2">
                  <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Заявитель
                  </div>
                  <div className="flex items-center gap-2 text-slate-200 font-medium">
                    <User className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
                    <span className="truncate">
                      {ticket.applicant_name || ticket.entities?.user_name || "Не указан"}
                    </span>
                  </div>

                  <div className="space-y-1 text-slate-400 text-[11px] pt-1 border-t border-[#1c2130]">
                    {(ticket.applicant_phone || ticket.entities?.phone) && (
                      <div className="flex items-center gap-1.5">
                        <Phone className="w-3 h-3 text-slate-500 shrink-0" />
                        <a
                          href={`tel:${ticket.applicant_phone || ticket.entities?.phone}`}
                          className="hover:text-indigo-300 transition-colors"
                        >
                          {ticket.applicant_phone || ticket.entities?.phone}
                        </a>
                      </div>
                    )}
                    {(ticket.applicant_email || ticket.entities?.email) && (
                      <div className="flex items-center gap-1.5 truncate">
                        <Mail className="w-3 h-3 text-slate-500 shrink-0" />
                        <a
                          href={`mailto:${ticket.applicant_email || ticket.entities?.email}`}
                          className="hover:text-indigo-300 transition-colors truncate"
                        >
                          {ticket.applicant_email || ticket.entities?.email}
                        </a>
                      </div>
                    )}
                    {ticket.entities?.department && (
                      <div className="flex items-center gap-1.5 truncate">
                        <Building className="w-3 h-3 text-slate-500 shrink-0" />
                        <span className="truncate">{ticket.entities.department}</span>
                      </div>
                    )}
                    {ticket.entities?.room && (
                      <div className="flex items-center gap-1.5">
                        <MapPin className="w-3 h-3 text-slate-500 shrink-0" />
                        <span>Кабинет: {ticket.entities.room}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Workstation & Diagnostics Card */}
                <div className="p-3 bg-[#11141d] border border-[#202636] rounded-lg space-y-2">
                  <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Рабочая станция и диагностика
                  </div>

                  <div className="flex items-center justify-between gap-2">
                    <HostBadge
                      host={ticket.entities?.pc_name || ticket.pc_name}
                      autoCheck={true}
                    />
                    <div className="flex items-center gap-1 text-[11px] text-slate-500 font-mono">
                      <Clock className="w-3 h-3 text-slate-500" />
                      <span>
                        {ticket.created ? new Date(ticket.created).toLocaleString("ru-RU") : ""}
                      </span>
                    </div>
                  </div>

                  <div className="space-y-1 text-slate-400 text-[11px] pt-1 border-t border-[#1c2130]">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500">Сервис:</span>
                      <span className="text-slate-300 font-medium truncate max-w-[200px]">
                        {ticket.service_name || "Не указан"}
                      </span>
                    </div>
                    {ticket.priority_name && (
                      <div className="flex items-center justify-between">
                        <span className="text-slate-500">Приоритет:</span>
                        <span className="text-slate-300 font-medium">
                          {ticket.priority_name}
                        </span>
                      </div>
                    )}
                    {ticket.creator_name && (
                      <div className="flex items-center justify-between">
                        <span className="text-slate-500">Автор заявки:</span>
                        <span className="text-slate-400 truncate max-w-[180px]">
                          {ticket.creator_name}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* RAG Knowledge Base Assistant */}
              <RAGSuggestion
                query={ticket.name}
                onApplySolution={(sol) => setCommentDraft(sol)}
              />

              {/* Description Body */}
              <div className="space-y-1.5">
                <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                  Описание проблемы
                </div>
                <div className="p-3.5 bg-[#0f121a] border border-[#1e2332] rounded-lg text-slate-200 whitespace-pre-wrap leading-relaxed select-text font-normal">
                  {ticket.description || "(Текст описания отсутствует)"}
                </div>
              </div>

              {/* Attachments / Screenshots Gallery */}
              {ticket.attachments && ticket.attachments.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    <Paperclip className="w-3.5 h-3.5 text-indigo-400" />
                    <span>Вложения ({ticket.attachments.length})</span>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
                    {ticket.attachments.map((att, idx) => {
                      const attId = att.id || att.Id;
                      const attName = att.name || att.Name || `Файл #${attId}`;
                      const isImg = isImageAttachment(attName);
                      const downloadUrl = ticketsApi.getAttachmentUrl(ticket.id, attId);

                      return (
                        <div
                          key={attId || idx}
                          onClick={() => handleAttachmentClick(att)}
                          className="group p-2 bg-[#121622] border border-[#22283a] hover:border-indigo-500/50 rounded-lg cursor-pointer transition-all flex items-center gap-2"
                        >
                          {isImg ? (
                            <div className="w-9 h-9 rounded bg-[#181d2c] flex items-center justify-center shrink-0 overflow-hidden border border-[#293144]">
                              <img
                                src={downloadUrl}
                                alt={attName}
                                className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                                onError={(e) => {
                                  // Fallback to icon if load fails
                                  (e.currentTarget as HTMLElement).style.display = "none";
                                }}
                              />
                            </div>
                          ) : (
                            <div className="w-9 h-9 rounded bg-[#181d2c] flex items-center justify-center shrink-0 border border-[#293144] text-slate-400">
                              <FileText className="w-4 h-4" />
                            </div>
                          )}

                          <div className="min-w-0 flex-1">
                            <div className="text-[11px] font-medium text-slate-200 truncate group-hover:text-indigo-300">
                              {attName}
                            </div>
                            <div className="text-[10px] text-slate-500">
                              {isImg ? "Изображение (клик для зума)" : "Документ"}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Comments & Lifetime History */}
              <div className="space-y-2.5 pt-2">
                <div className="flex items-center justify-between text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                  <span className="flex items-center gap-1.5">
                    <MessageSquare className="w-3.5 h-3.5 text-indigo-400" />
                    История переписки и событий ({events.length})
                  </span>
                </div>

                <div className="space-y-2">
                  {events.map((ev, idx) => {
                    const isComment = !!ev.comment;
                    const isStatusChange =
                      ev.old_status_name && ev.new_status_name;

                    if (!isComment && !isStatusChange) return null;

                    return (
                      <div
                        key={ev.id || idx}
                        className={`p-3 rounded-lg border text-xs space-y-1.5 leading-relaxed ${
                          ev.is_private || ev.is_private_comment
                            ? "bg-amber-950/20 border-amber-800/40 text-amber-200"
                            : isStatusChange
                            ? "bg-[#11141d] border-[#1d2230] text-slate-400"
                            : "bg-[#131722] border-[#222838] text-slate-200"
                        }`}
                      >
                        <div className="flex items-center justify-between text-[11px]">
                          <span className="font-semibold text-slate-300">
                            {ev.user_name || "Система"}
                          </span>
                          <div className="flex items-center gap-2">
                            {(ev.is_private || ev.is_private_comment) && (
                              <Badge variant="warning">Служебная заметка</Badge>
                            )}
                            <span className="text-slate-500 font-mono text-[10px]">
                              {ev.created
                                ? new Date(ev.created).toLocaleString("ru-RU")
                                : ""}
                            </span>
                          </div>
                        </div>

                        {isStatusChange && (
                          <div className="text-[11px] text-slate-400">
                            Статус изменен:{" "}
                            <span className="text-slate-300 line-through">
                              {ev.old_status_name}
                            </span>{" "}
                            ➔{" "}
                            <span className="text-emerald-400 font-medium">
                              {ev.new_status_name}
                            </span>
                          </div>
                        )}

                        {ev.comment && (
                          <p className="whitespace-pre-wrap select-text">
                            {ev.comment}
                          </p>
                        )}
                      </div>
                    );
                  })}

                  {events.length === 0 && (
                    <div className="text-center py-6 text-slate-500 italic text-[11px]">
                      История изменений пока отсутствует
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </div>

        {/* Action Dock Fixed Bottom */}
        {ticket && (
          <ActionDock
            ticketId={ticket.id}
            ticketTitle={ticket.name}
            statusId={ticket.status_id}
            onTake={handleTake}
            onResolve={handleResolve}
            onDuplicate={handleDuplicate}
            onRedirect={handleRedirect}
            onAddComment={handleAddComment}
            isBusy={busyAction}
            commentDraft={commentDraft}
            onCommentDraftChange={setCommentDraft}
          />
        )}
      </div>

      {/* Lightbox for full screenshot zoom */}
      <Lightbox
        src={lightboxSrc}
        alt={lightboxAlt}
        isOpen={!!lightboxSrc}
        onClose={() => setLightboxSrc(null)}
      />
    </>
  );
};
