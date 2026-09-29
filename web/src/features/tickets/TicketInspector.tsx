import React, { useState } from "react";
import {
  X,
  Maximize2,
  Minimize2,
  AlertCircle,
  Hash,
} from "lucide-react";
import { Badge, StatusDot, Lightbox, KbdBadge, useToast } from "@/shared/ui";
import { getStatusMeta, isStatusResolved, isStatusCancelled } from "@/shared/statuses";
import { ActionDock } from "./ActionDock";
import { AgentPlanCard } from "./AgentPlanCard";
import { LiveExecutionStepper } from "./LiveExecutionStepper";
import { DialogueLoopCard } from "./DialogueLoopCard";
import { TicketAttachment, ticketsApi } from "@/shared/api";
import { TicketActivity } from "./TicketActivity";
import { TicketOverview } from "./TicketOverview";
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
    addCommentMutation,
  } = useTicketActions();

  const isBusy =
    takeMutation.isPending ||
    resolveMutation.isPending ||
    duplicateMutation.isPending ||
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

              <TicketOverview
                ticket={ticket}
                isFetching={ticketFetching}
                onAttachmentClick={handleAttachmentClick}
                onApplySuggestion={(solution) =>
                  setCommentDraft((previous) => previous ? `${previous}\n\n${solution}` : solution)
                }
              />

              {!isStatusResolved(ticket.status_id) && !isStatusCancelled(ticket.status_id) && (
                <AgentPlanCard
                  ticketId={ticket.id}
                  currentStatusId={ticket.status_id}
                  attachments={ticket.attachments}
                  onExecutionStarted={(commandId) => setActiveCommandId(commandId)}
                  onClose={onClose}
                />
              )}

              <TicketActivity events={events} isFetching={eventsFetching} />
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
