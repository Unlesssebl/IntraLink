import React, { useState } from "react";
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
import { Badge, StatusDot, Lightbox, KbdBadge, useToast } from "@/shared/ui";
import { HostBadge } from "@/features/diagnostics/HostBadge";
import { RAGSuggestion } from "@/features/knowledge-base/RAGSuggestion";
import { getStatusMeta, isStatusResolved, isStatusCancelled } from "@/shared/statuses";
import { ActionDock } from "./ActionDock";
import { AgentPlanCard } from "./AgentPlanCard";
import { LiveExecutionStepper } from "./LiveExecutionStepper";
import { DialogueLoopCard } from "./DialogueLoopCard";
import { DomainMetadataGrid } from "./DomainMetadataGrid";
import { TicketAttachment, ticketsApi } from "@/shared/api";
import {
  useTicketDetail,
  useTicketLifetime,
  useTicketActions,
  useAgentPlan,
} from "./queries";

export interface TicketInspectorProps {
  ticketId: number | null;
  onClose: () => void;
  isFullscreen?: boolean;
  onToggleFullscreen?: () => void;
}

export const TicketInspector: React.FC<TicketInspectorProps> = ({
  ticketId,
  onClose,
  isFullscreen = false,
  onToggleFullscreen,
}) => {
  // 1. Data queries (auto-cached, automatic cancellation, 0ms placeholderData)
  const {
    data: ticket,
    isLoading: ticketLoading,
    isFetching: ticketFetching,
    error: ticketError,
  } = useTicketDetail(ticketId);

  const {
    data: events = [],
    isFetching: eventsFetching,
  } = useTicketLifetime(ticketId);

  // 2. Declarative mutations with automatic query invalidation
  const {
    takeMutation,
    resolveMutation,
    duplicateMutation,
    redirectMutation,
    addCommentMutation,
  } = useTicketActions();

  const isBusy =
    takeMutation.isPending ||
    resolveMutation.isPending ||
    duplicateMutation.isPending ||
    redirectMutation.isPending ||
    addCommentMutation.isPending;

  // Lightbox preview for screenshots
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [lightboxAlt, setLightboxAlt] = useState("");

  // Comment draft synced with RAG suggestions
  const [commentDraft, setCommentDraft] = useState("");

  // HITL Autopilot execution tracking
  const [activeCommandId, setActiveCommandId] = useState<string | null>(null);
  const { data: agentPlan } = useAgentPlan(ticketId);

  const toast = useToast();

  // Reusable unified action executor with error handling
  const executeAction = async (fn: () => Promise<any>, errorPrefix: string) => {
    if (!ticketId) return;
    try {
      await fn();
    } catch (err: any) {
      toast.error(`${errorPrefix}: ${err?.message || "Произошла ошибка"}`);
    }
  };

  const handleTake = () =>
    executeAction(() => takeMutation.mutateAsync(ticketId!), "Ошибка взятия в работу");

  const handleResolve = (comment: string) =>
    executeAction(
      () => resolveMutation.mutateAsync({ id: ticketId!, comment }),
      "Ошибка закрытия заявки"
    );

  const handleDuplicate = (masterId: number, comment: string) =>
    executeAction(
      () => duplicateMutation.mutateAsync({ id: ticketId!, masterId, comment }),
      "Ошибка отмены дубликата"
    );

  const handleRedirect = (serviceId: number, comment: string) =>
    executeAction(
      () => redirectMutation.mutateAsync({ id: ticketId!, serviceId, comment }),
      "Ошибка перенаправления"
    );

  const handleAddComment = (comment: string, isPrivate: boolean) =>
    executeAction(
      () => addCommentMutation.mutateAsync({ id: ticketId!, comment, isPrivate }),
      "Ошибка добавления комментария"
    );

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

  const isBackgroundFetching = ticketFetching || eventsFetching;

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
            {ticket && (
              <Badge variant={getStatusMeta(ticket.status_id, ticket.status_name).variant}>
                {getStatusMeta(ticket.status_id, ticket.status_name).name}
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

        {/* Linear-style Delicate 1.5px Progress Bar */}
        {isBackgroundFetching && (
          <div className="h-[2px] w-full bg-neutral-900 overflow-hidden shrink-0">
            <div className="h-full bg-neutral-400 animate-pulse w-full" />
          </div>
        )}

        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
          {ticketError ? (
            <div className="p-4 bg-rose-950/40 border border-rose-800/60 rounded text-rose-300 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{(ticketError as any)?.message || "Не удалось загрузить карточку заявки"}</span>
            </div>
          ) : !ticket && ticketLoading ? (
            /* Linear Skeleton loading when no placeholder is available */
            <div className="space-y-4 animate-pulse">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="h-28 bg-[#121316] rounded border border-neutral-800/80" />
                <div className="h-28 bg-[#121316] rounded border border-neutral-800/80" />
              </div>
              <div className="h-32 bg-[#121316] rounded border border-neutral-800/80" />
            </div>
          ) : ticket ? (
            <>
              {/* 1. Live Execution Stepper (Active Taskiq Worker progression) */}
              {activeCommandId && (
                <LiveExecutionStepper
                  commandId={activeCommandId}
                  onDismiss={() => setActiveCommandId(null)}
                />
              )}

              {/* 2. Autonomous Dialogue Loop Card (Status #6) */}
              {ticket.status_id === 6 && agentPlan && (
                <DialogueLoopCard
                  plan={agentPlan}
                  onForceResume={() => {}}
                />
              )}

              {/* 3. Primary Autonomous Agent Plan Supervisor Card */}
              {!isStatusResolved(ticket.status_id) && !isStatusCancelled(ticket.status_id) && (
                <AgentPlanCard
                  ticketId={ticket.id}
                  currentStatusId={ticket.status_id}
                  onExecutionStarted={(cmdId) => setActiveCommandId(cmdId)}
                  onClose={onClose}
                />
              )}

              {/* 4. Domain Metadata Grid (Directum / 1C / custom entities) */}
              <DomainMetadataGrid ticket={ticket} />

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
                        <Phone className="w-3.5 h-3.5 text-neutral-500 shrink-0" />
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
                        <Mail className="w-3.5 h-3.5 text-neutral-500 shrink-0" />
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
                        <Building className="w-3.5 h-3.5 text-neutral-500 shrink-0" />
                        <span className="truncate">{ticket.entities.department}</span>
                      </div>
                    )}
                    {ticket.entities?.room && (
                      <div className="flex items-center gap-1.5">
                        <MapPin className="w-3.5 h-3.5 text-neutral-500 shrink-0" />
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
                      <Clock className="w-3.5 h-3.5 text-neutral-500" />
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
                onApplySolution={(sol) =>
                  setCommentDraft((prev) => (prev ? `${prev}\n\n${sol}` : sol))
                }
              />

              {/* Description Body */}
              <div className="space-y-1.5">
                <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">
                  Описание проблемы
                </div>
                <div className="p-3.5 bg-[#101114] border border-neutral-800/80 rounded text-neutral-200 whitespace-pre-wrap leading-relaxed select-text font-normal">
                  {ticket.description ? (
                    ticket.description
                  ) : ticketFetching ? (
                    <div className="text-neutral-500 italic py-1 animate-pulse">
                      Загрузка полного текста заявки...
                    </div>
                  ) : (
                    <span className="text-neutral-500 italic">Описание отсутствует</span>
                  )}
                </div>
              </div>

              {/* Attachments Section */}
              {ticket.attachments && ticket.attachments.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">
                    <Paperclip className="w-3.5 h-3.5 text-neutral-400" />
                    <span>Вложения ({ticket.attachments.length})</span>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                    {ticket.attachments.map((att: TicketAttachment) => {
                      const isImg = isImageAttachment(att.name || att.Name || "");
                      const attId = att.id || att.Id;
                      return (
                        <div
                          key={attId}
                          onClick={() => handleAttachmentClick(att)}
                          className="group p-2 bg-[#121316] border border-neutral-800/80 hover:border-neutral-600 rounded cursor-pointer transition-all flex items-center gap-2"
                        >
                          {isImg ? (
                            <div className="w-9 h-9 rounded bg-[#18191d] flex items-center justify-center shrink-0 overflow-hidden border border-neutral-800">
                              <img
                                src={ticketsApi.getAttachmentUrl(ticket.id, attId)}
                                alt={att.name || att.Name}
                                className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                                onError={(e) => {
                                  (e.target as HTMLElement).style.display = "none";
                                }}
                              />
                            </div>
                          ) : (
                            <div className="w-9 h-9 rounded bg-[#18191d] flex items-center justify-center shrink-0 border border-neutral-800 text-neutral-400">
                              <FileText className="w-4 h-4" />
                            </div>
                          )}

                          <div className="min-w-0 flex-1">
                            <div className="text-xs text-neutral-200 truncate group-hover:text-white font-medium">
                              {att.name || att.Name}
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
                  {eventsFetching && (
                    <span className="text-[10px] text-neutral-500 animate-pulse font-normal lowercase">
                      обновление...
                    </span>
                  )}
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
                      {eventsFetching ? "Загрузка истории..." : "История изменений пока отсутствует"}
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
            isBusy={isBusy}
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
