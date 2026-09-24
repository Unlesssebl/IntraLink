import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  X,
  Maximize2,
  Minimize2,
  User,
  Phone,
  Mail,
  MapPin,
  Clock,
  Paperclip,
  FileText,
  MessageSquare,
  AlertCircle,
  Building,
  Hash,
} from "lucide-react";
import { Badge, StatusDot, Lightbox, KbdBadge } from "@/shared/ui";
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

// In-Memory SWR Cache for instant 0ms ticket switching
interface CachedTicket {
  ticket: TicketDetail;
  events: TicketLifetimeEvent[];
  cachedAt: number;
}
const TICKET_CACHE = new Map<number, CachedTicket>();
const MAX_CACHE_SIZE = 60;
const CACHE_TTL_MS = 60000; // 1 minute fresh TTL

function setTicketCache(id: number, data: { ticket: TicketDetail; events: TicketLifetimeEvent[] }) {
  if (TICKET_CACHE.size >= MAX_CACHE_SIZE) {
    const oldestKey = TICKET_CACHE.keys().next().value;
    if (oldestKey !== undefined) TICKET_CACHE.delete(oldestKey);
  }
  TICKET_CACHE.set(id, { ...data, cachedAt: Date.now() });
}

function invalidateTicketCache(id: number) {
  TICKET_CACHE.delete(id);
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

  // Request cancellation and debounce references
  const abortControllerRef = useRef<AbortController | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadTicketData = useCallback(async (id: number, isForceRefresh = false) => {
    // 1. Cancel previous in-flight request immediately!
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    // 2. Check SWR cache for instant render
    const cached = TICKET_CACHE.get(id);
    const isCacheFresh = cached && Date.now() - cached.cachedAt < CACHE_TTL_MS;

    if (cached && !isForceRefresh) {
      setTicket(cached.ticket);
      setEvents(cached.events);
      setError(null);
      setLoading(false);
      // If cache is fresh, skip background re-fetch
      if (isCacheFresh) {
        return;
      }
    } else {
      setLoading(true);
      setError(null);
    }

    // 3. Fetch from API with abort signal
    try {
      const [ticketData, lifetimeData] = await Promise.all([
        ticketsApi.get(id, controller.signal),
        ticketsApi.getLifetime(id, controller.signal).catch((err) => {
          if (err.name === "AbortError") throw err;
          return [];
        }),
      ]);

      if (controller.signal.aborted) return;

      setTicketCache(id, { ticket: ticketData, events: lifetimeData });
      setTicket(ticketData);
      setEvents(lifetimeData);
      setError(null);
    } catch (err: any) {
      if (controller.signal.aborted || err.name === "AbortError") {
        return;
      }
      setError(err?.message || "Не удалось загрузить данные заявки");
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!ticketId) {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      setTicket(null);
      setEvents([]);
      setLoading(false);
      return;
    }

    // Fast-path: if ticket is already in cache, show it instantly without waiting for debounce
    const cached = TICKET_CACHE.get(ticketId);
    if (cached) {
      setTicket(cached.ticket);
      setEvents(cached.events);
      setError(null);
      setLoading(false);
    }

    // Debounce network requests by 60ms so rapid keyboard switching (J/K) doesn't flood the network
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    debounceTimerRef.current = setTimeout(() => {
      loadTicketData(ticketId);
    }, 60);

    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [ticketId, loadTicketData]);

  // Handle action callbacks with cache invalidation
  const handleTake = async () => {
    if (!ticketId) return;
    try {
      setBusyAction(true);
      await ticketsApi.take(ticketId);
      invalidateTicketCache(ticketId);
      await loadTicketData(ticketId, true);
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
      invalidateTicketCache(ticketId);
      await loadTicketData(ticketId, true);
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
      invalidateTicketCache(ticketId);
      await loadTicketData(ticketId, true);
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
      invalidateTicketCache(ticketId);
      await loadTicketData(ticketId, true);
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
      invalidateTicketCache(ticketId);
      await loadTicketData(ticketId, true);
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
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-neutral-500 bg-[#08090a]">
        <Hash className="w-12 h-12 text-neutral-700 mb-3 stroke-[1.2]" />
        <h3 className="text-sm font-semibold text-neutral-300">
          Заявка не выбрана
        </h3>
        <p className="text-xs text-neutral-500 max-w-sm mt-1">
          Выберите заявку из очереди слева или переключайтесь горячими клавишами{" "}
          <KbdBadge shortcut="J" /> / <KbdBadge shortcut="K" />
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#08090a]">
        {/* Top Header */}
        <div className="px-4 py-3 border-b border-neutral-800/80 bg-[#0c0d0e] flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            {ticket && (
              <StatusDot
                statusId={ticket.status_id}
                statusName={ticket.status_name}
                size="md"
              />
            )}
            <span className="font-mono text-sm font-semibold text-neutral-200 shrink-0">
              #{ticketId}
            </span>
            {ticket && (
              <span className="text-xs font-semibold text-neutral-100 truncate">
                {ticket.name || "(Без темы)"}
              </span>
            )}
            {ticket?.status_name && (
              <Badge
                variant={
                  ticket.status_name.toLowerCase().includes("отмен")
                    ? "danger"
                    : ticket.status_name.toLowerCase().includes("работ")
                    ? "warning"
                    : ticket.status_name.toLowerCase().includes("решен")
                    ? "success"
                    : "neutral"
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
                className="text-neutral-400 hover:text-neutral-200 p-1.5 rounded hover:bg-neutral-800/60 transition-colors"
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
              className="text-neutral-400 hover:text-rose-400 p-1.5 rounded hover:bg-neutral-800/60 transition-colors"
              title="Закрыть (Esc)"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
          {loading ? (
            <div className="flex items-center justify-center py-20 text-neutral-400 text-xs">
              Загрузка карточки заявки #{ticketId}...
            </div>
          ) : error ? (
            <div className="p-4 bg-rose-950/40 border border-rose-800/60 rounded text-rose-300 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : ticket ? (
            <>
              {/* Context Cards: Applicant + Workstation */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* Applicant Card */}
                <div className="p-3 bg-[#121316] border border-neutral-800/80 rounded space-y-2">
                  <div className="text-[10px] font-semibold text-neutral-400 uppercase tracking-wider">
                    Заявитель
                  </div>
                  <div className="flex items-center gap-2 text-neutral-100 font-medium">
                    <User className="w-3.5 h-3.5 text-neutral-400 shrink-0" />
                    <span className="truncate">
                      {ticket.applicant_name || ticket.entities?.user_name || "Не указан"}
                    </span>
                  </div>

                  <div className="space-y-1 text-neutral-400 text-[11px] pt-1.5 border-t border-neutral-800/80">
                    {(ticket.applicant_phone || ticket.entities?.phone) && (
                      <div className="flex items-center gap-1.5">
                        <Phone className="w-3 h-3 text-neutral-500 shrink-0" />
                        <a
                          href={`tel:${ticket.applicant_phone || ticket.entities?.phone}`}
                          className="hover:text-white transition-colors font-mono"
                        >
                          {ticket.applicant_phone || ticket.entities?.phone}
                        </a>
                      </div>
                    )}
                    {(ticket.applicant_email || ticket.entities?.email) && (
                      <div className="flex items-center gap-1.5 truncate">
                        <Mail className="w-3 h-3 text-neutral-500 shrink-0" />
                        <a
                          href={`mailto:${ticket.applicant_email || ticket.entities?.email}`}
                          className="hover:text-white transition-colors truncate"
                        >
                          {ticket.applicant_email || ticket.entities?.email}
                        </a>
                      </div>
                    )}
                    {ticket.entities?.department && (
                      <div className="flex items-center gap-1.5 truncate">
                        <Building className="w-3 h-3 text-neutral-500 shrink-0" />
                        <span className="truncate">{ticket.entities.department}</span>
                      </div>
                    )}
                    {ticket.entities?.room && (
                      <div className="flex items-center gap-1.5">
                        <MapPin className="w-3 h-3 text-neutral-500 shrink-0" />
                        <span>Кабинет: {ticket.entities.room}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Workstation & Diagnostics Card */}
                <div className="p-3 bg-[#121316] border border-neutral-800/80 rounded space-y-2">
                  <div className="text-[10px] font-semibold text-neutral-400 uppercase tracking-wider">
                    Рабочая станция и диагностика
                  </div>

                  <div className="flex items-center justify-between gap-2">
                    <HostBadge host={ticket.entities?.pc_name || ticket.pc_name} />
                    <div className="flex items-center gap-1 text-[11px] text-neutral-400 font-mono">
                      <Clock className="w-3 h-3 text-neutral-500" />
                      <span>
                        {ticket.created ? new Date(ticket.created).toLocaleString("ru-RU") : ""}
                      </span>
                    </div>
                  </div>

                  <div className="space-y-1 text-neutral-400 text-[11px] pt-1.5 border-t border-neutral-800/80">
                    <div className="flex items-center justify-between">
                      <span className="text-neutral-500">Сервис:</span>
                      <span className="text-neutral-300 font-medium truncate max-w-[200px]">
                        {ticket.service_name || "Не указан"}
                      </span>
                    </div>
                    {ticket.priority_name && (
                      <div className="flex items-center justify-between">
                        <span className="text-neutral-500">Приоритет:</span>
                        <span className="text-neutral-300 font-medium">
                          {ticket.priority_name}
                        </span>
                      </div>
                    )}
                    {ticket.creator_name && (
                      <div className="flex items-center justify-between">
                        <span className="text-neutral-500">Автор заявки:</span>
                        <span className="text-neutral-400 truncate max-w-[180px]">
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
                <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">
                  Описание проблемы
                </div>
                <div className="p-3.5 bg-[#101114] border border-neutral-800/80 rounded text-neutral-200 whitespace-pre-wrap leading-relaxed select-text font-normal">
                  {ticket.description || "(Текст описания отсутствует)"}
                </div>
              </div>

              {/* Attachments / Screenshots Gallery */}
              {ticket.attachments && ticket.attachments.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">
                    <Paperclip className="w-3.5 h-3.5 text-neutral-400" />
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
                          className="group p-2 bg-[#121316] border border-neutral-800/80 hover:border-neutral-600 rounded cursor-pointer transition-all flex items-center gap-2"
                        >
                          {isImg ? (
                            <div className="w-9 h-9 rounded bg-[#18191d] flex items-center justify-center shrink-0 overflow-hidden border border-neutral-800">
                              <img
                                src={downloadUrl}
                                alt={attName}
                                className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                                onError={(e) => {
                                  (e.currentTarget as HTMLElement).style.display = "none";
                                }}
                              />
                            </div>
                          ) : (
                            <div className="w-9 h-9 rounded bg-[#18191d] flex items-center justify-center shrink-0 border border-neutral-800 text-neutral-400">
                              <FileText className="w-4 h-4" />
                            </div>
                          )}

                          <div className="min-w-0 flex-1">
                            <div className="text-[11px] font-medium text-neutral-200 truncate group-hover:text-white">
                              {attName}
                            </div>
                            <div className="text-[10px] text-neutral-500">
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
                <div className="flex items-center justify-between text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">
                  <span className="flex items-center gap-1.5">
                    <MessageSquare className="w-3.5 h-3.5 text-neutral-400" />
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
                        className={`p-3 rounded border text-xs space-y-1.5 leading-relaxed ${
                          ev.is_private || ev.is_private_comment
                            ? "bg-amber-950/20 border-amber-800/40 text-amber-200"
                            : isStatusChange
                            ? "bg-[#101114] border-neutral-800/70 text-neutral-400"
                            : "bg-[#121316] border-neutral-800/80 text-neutral-200"
                        }`}
                      >
                        <div className="flex items-center justify-between text-[11px]">
                          <span className="font-semibold text-neutral-200">
                            {ev.user_name || "Система"}
                          </span>
                          <div className="flex items-center gap-2">
                            {(ev.is_private || ev.is_private_comment) && (
                              <Badge variant="warning">Служебная заметка</Badge>
                            )}
                            <span className="text-neutral-500 font-mono text-[10px]">
                              {ev.created
                                ? new Date(ev.created).toLocaleString("ru-RU")
                                : ""}
                            </span>
                          </div>
                        </div>

                        {isStatusChange && (
                          <div className="text-[11px] text-neutral-400">
                            Статус изменен:{" "}
                            <span className="text-neutral-400 line-through">
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
                    <div className="text-center py-6 text-neutral-500 italic text-[11px]">
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
