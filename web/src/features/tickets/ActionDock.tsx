import React, { useState, useEffect } from "react";
import {
  CheckCircle,
  CheckCheck,
  Copy,
  ArrowRightLeft,
  Send,
  Lock,
  Globe,
} from "lucide-react";
import { Button, Modal, Textarea, Input, KbdBadge, useToast } from "@/shared/ui";
import {
  isStatusInWork,
  isStatusResolved,
  isStatusCancelled,
} from "@/shared/statuses";
import { useServicesCatalog } from "./queries";

export interface ActionDockProps {
  ticketId: number;
  ticketTitle: string;
  statusId: number;
  onTake: () => Promise<void>;
  onResolve: (comment: string) => Promise<void>;
  onDuplicate: (masterId: number, comment: string) => Promise<void>;
  onRedirect: (serviceId: number, comment: string) => Promise<void>;
  onAddComment: (comment: string, isPrivate: boolean) => Promise<void>;
  isBusy?: boolean;
  commentDraft?: string;
  onCommentDraftChange?: (val: string) => void;
}

const RESOLVE_TEMPLATES = [
  "Консультация предоставлена пользователю в полном объеме. Вопрос решен.",
  "Настройка программного обеспечения выполнена. Работоспособность проверена.",
  "Сброшен локальный кэш, служба перезапущена. Ошибка устранена.",
  "Произведена замена картриджа / расходных материалов. Печать исправна.",
  "Доступ предоставлен согласно заявке. Учетная запись активирована.",
];

export const ActionDock: React.FC<ActionDockProps> = ({
  ticketId,
  statusId,
  onTake,
  onResolve,
  onDuplicate,
  onRedirect,
  onAddComment,
  isBusy = false,
  commentDraft = "",
  onCommentDraftChange,
}) => {
  const toast = useToast();

  // Comment state
  const [commentText, setCommentText] = useState(commentDraft);
  const [isPrivate, setIsPrivate] = useState(false);
  const [sendingComment, setSendingComment] = useState(false);

  // Modals state
  const [resolveModalOpen, setResolveModalOpen] = useState(false);
  const [resolveComment, setResolveComment] = useState(RESOLVE_TEMPLATES[0]);

  const [dupModalOpen, setDupModalOpen] = useState(false);
  const [dupMasterId, setDupMasterId] = useState("");
  const [dupComment, setDupComment] = useState("");

  const [redirModalOpen, setRedirModalOpen] = useState(false);
  const { data: rawServices = [] } = useServicesCatalog();
  const services = React.useMemo(
    () => rawServices.filter((s) => s.is_active !== false),
    [rawServices]
  );
  const [selectedServiceId, setSelectedServiceId] = useState<number | "">("");
  const [redirComment, setRedirComment] = useState("");

  // Sync external draft changes if provided
  useEffect(() => {
    if (commentDraft) {
      setCommentText(commentDraft);
    }
  }, [commentDraft]);

  const handleCommentChange = (text: string) => {
    setCommentText(text);
    if (onCommentDraftChange) onCommentDraftChange(text);
  };

  // Pre-select first service when catalog loads
  useEffect(() => {
    if (!selectedServiceId && services.length > 0) {
      setSelectedServiceId(services[0].id);
    }
  }, [selectedServiceId, services]);

  const isAlreadyInWork = isStatusInWork(statusId);
  const isCompleted = isStatusResolved(statusId) || isStatusCancelled(statusId);

  // Global hotkeys: Alt+1, Alt+2, Alt+3, Alt+4
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.altKey) {
        if (e.key === "1") {
          e.preventDefault();
          if (!isCompleted && !isAlreadyInWork && !isBusy) onTake();
        } else if (e.key === "2") {
          e.preventDefault();
          if (!isCompleted && !isBusy) handleOpenResolveModal();
        } else if (e.key === "3") {
          e.preventDefault();
          if (!isCompleted && !isBusy) setDupModalOpen(true);
        } else if (e.key === "4") {
          e.preventDefault();
          if (!isCompleted && !isBusy) setRedirModalOpen(true);
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onTake, isCompleted, isAlreadyInWork, isBusy]);

  const handleSendComment = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!commentText.trim()) return;

    try {
      setSendingComment(true);
      await onAddComment(commentText.trim(), isPrivate);
      handleCommentChange("");
      toast.success(isPrivate ? "Служебная заметка добавлена" : "Ответ заявителю отправлен");
    } catch {
      // Error handled in TicketInspector executeAction
    } finally {
      setSendingComment(false);
    }
  };

  // Business invariant v1 (UnifiedActionDock.tsx):
  // Resolving ticket with internal private comment is strictly forbidden
  const handleOpenResolveModal = () => {
    if (isPrivate) {
      toast.warning("Закрытие заявки запрещено со служебной заметкой. Переключите режим на «Заявителю».");
      return;
    }
    setResolveModalOpen(true);
  };

  const handleConfirmResolve = async () => {
    if (!resolveComment.trim()) {
      toast.warning("Заполните текст решения для заявителя");
      return;
    }
    await onResolve(resolveComment.trim());
    setResolveModalOpen(false);
    toast.success(`Заявка #${ticketId} успешно закрыта`);
  };

  const handleConfirmDuplicate = async () => {
    const masterId = parseInt(dupMasterId.replace("#", "").trim(), 10);
    if (isNaN(masterId) || masterId <= 0) {
      toast.error("Укажите корректный номер основной заявки (Master Ticket ID)");
      return;
    }
    const finalComment =
      dupComment.trim() ||
      `Заявка закрыта как дубликат обращения #${masterId}. Дальнейшие работы ведутся в основной заявке #${masterId}: https://servicedesk-pub.corporate.loc/Task/View/${masterId}`;

    await onDuplicate(masterId, finalComment);
    setDupModalOpen(false);
    setDupMasterId("");
    setDupComment("");
    toast.success(`Заявка #${ticketId} отменена как дубликат #${masterId}`);
  };

  const handleConfirmRedirect = async () => {
    if (!selectedServiceId) {
      toast.error("Выберите целевой сервис");
      return;
    }
    const sObj = services.find((s) => s.id === Number(selectedServiceId));
    const sName = sObj ? sObj.name : `ID ${selectedServiceId}`;
    
    // Helpdesk domain invariant (docs/architecture/domain-model-and-contracts.md)
    // Never falsely promise applicant to "wait for specialists" on a cancelled ticket!
    const finalComment =
      redirComment.trim() ||
      `Заявка отменена, т. к. создана не в подходящем разделе каталога.\nТребуется оставить заявку в подходящем разделе: «${sName}».\nЕсли у вас остались вопросы, пожалуйста, напишите в комментариях к этой заявке.`;

    await onRedirect(Number(selectedServiceId), finalComment);
    setRedirModalOpen(false);
    setRedirComment("");
    toast.success(`Заявка #${ticketId} перенаправлена в сервис «${sName}»`);
  };

  return (
    <div className="border-t border-neutral-800/80 bg-[#0c0d0e] p-3 space-y-2.5 shrink-0">
      {/* Quick Comment Input */}
      <form onSubmit={handleSendComment} className="space-y-2">
        <div className="relative">
          <textarea
            value={commentText}
            onChange={(e) => handleCommentChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.ctrlKey && e.key === "Enter") {
                handleSendComment();
              }
            }}
            placeholder={
              isPrivate
                ? "Внутренняя заметка инженера (Ctrl+Enter для отправки)..."
                : "Ответ заявителю (Ctrl+Enter для отправки)..."
            }
            rows={2}
            className={`w-full text-xs rounded border p-2.5 placeholder:text-neutral-500 focus:outline-none focus:ring-1 resize-none ${
              isPrivate
                ? "bg-amber-950/20 border-amber-800/50 text-amber-200 focus:ring-amber-500"
                : "bg-[#121316] border-neutral-800 text-neutral-100 focus:ring-neutral-400 focus:border-neutral-500"
            }`}
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1 bg-[#121316] border border-neutral-800 p-0.5 rounded text-[11px]">
            <button
              type="button"
              onClick={() => setIsPrivate(false)}
              className={`flex items-center gap-1 px-2 py-0.5 rounded transition-colors ${
                !isPrivate
                  ? "bg-neutral-800 text-neutral-100 font-medium border border-neutral-700 shadow-xs"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              <Globe className="w-3 h-3 text-neutral-400" />
              <span>Заявителю</span>
            </button>
            <button
              type="button"
              onClick={() => setIsPrivate(true)}
              className={`flex items-center gap-1 px-2 py-0.5 rounded transition-colors ${
                isPrivate
                  ? "bg-amber-950/40 text-amber-300 font-medium border border-amber-800/60 shadow-xs"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              <Lock className="w-3 h-3 text-neutral-400" />
              <span>Служебная заметка</span>
            </button>
          </div>

          <Button
            type="submit"
            size="sm"
            variant={isPrivate ? "secondary" : "primary"}
            loading={sendingComment}
            disabled={!commentText.trim() || isBusy}
            icon={<Send className="w-3 h-3" />}
          >
            Отправить
          </Button>
        </div>
      </form>

      {/* Operator Action Dock Buttons */}
      <div className="grid grid-cols-4 gap-2 pt-1 border-t border-neutral-800/80">
        {/* Alt + 1: В работу */}
        <button
          type="button"
          onClick={onTake}
          disabled={isBusy || isAlreadyInWork || isCompleted}
          className={`flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-medium transition-all ${
            isCompleted
              ? "bg-[#101114] border-neutral-800/50 text-neutral-600 cursor-not-allowed opacity-60"
              : isAlreadyInWork
              ? "bg-[#121316] border-emerald-900/40 text-emerald-400/70 opacity-80 cursor-default"
              : "bg-[#121316] border-neutral-800 hover:border-neutral-700 hover:bg-neutral-800/60 text-neutral-200 active:scale-95"
          }`}
          title={
            isCompleted
              ? "Заявка уже завершена"
              : isAlreadyInWork
              ? "Заявка уже находится в работе"
              : "Взять заявку в работу (Alt+1)"
          }
        >
          <CheckCircle className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
          <span className="truncate">В работу</span>
          <KbdBadge shortcut="Alt+1" />
        </button>

        {/* Alt + 2: Решено */}
        <button
          type="button"
          onClick={handleOpenResolveModal}
          disabled={isBusy || isCompleted}
          className={`flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-medium transition-all ${
            isCompleted
              ? "bg-[#101114] border-neutral-800/50 text-neutral-600 cursor-not-allowed opacity-60"
              : "border-neutral-800 bg-[#121316] hover:border-neutral-700 hover:bg-neutral-800/60 text-neutral-200 active:scale-95"
          }`}
          title={isCompleted ? "Заявка уже завершена" : "Закрыть с решением (Alt+2)"}
        >
          <CheckCheck className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
          <span className="truncate">Решено</span>
          <KbdBadge shortcut="Alt+2" />
        </button>

        {/* Alt + 3: Дубликат */}
        <button
          type="button"
          onClick={() => setDupModalOpen(true)}
          disabled={isBusy || isCompleted}
          className={`flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-medium transition-all ${
            isCompleted
              ? "bg-[#101114] border-neutral-800/50 text-neutral-600 cursor-not-allowed opacity-60"
              : "border-neutral-800 bg-[#121316] hover:border-neutral-700 hover:bg-neutral-800/60 text-neutral-200 active:scale-95"
          }`}
          title={isCompleted ? "Заявка уже завершена" : "Отменить как дубликат (Alt+3)"}
        >
          <Copy className="w-3.5 h-3.5 text-amber-400 shrink-0" />
          <span className="truncate">Дубликат</span>
          <KbdBadge shortcut="Alt+3" />
        </button>

        {/* Alt + 4: Перенаправить */}
        <button
          type="button"
          onClick={() => setRedirModalOpen(true)}
          disabled={isBusy || isCompleted}
          className={`flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded border text-xs font-medium transition-all ${
            isCompleted
              ? "bg-[#101114] border-neutral-800/50 text-neutral-600 cursor-not-allowed opacity-60"
              : "border-neutral-800 bg-[#121316] hover:border-neutral-700 hover:bg-neutral-800/60 text-neutral-200 active:scale-95"
          }`}
          title={isCompleted ? "Заявка уже завершена" : "Перенаправить в другой сервис (Alt+4)"}
        >
          <ArrowRightLeft className="w-3.5 h-3.5 text-neutral-400 shrink-0" />
          <span className="truncate">Перенаправить</span>
          <KbdBadge shortcut="Alt+4" />
        </button>
      </div>

      {/* Modal 1: Resolve Ticket */}
      <Modal
        isOpen={resolveModalOpen}
        onClose={() => setResolveModalOpen(false)}
        title={`Закрытие заявки #${ticketId}`}
        description="Выберите готовый шаблон решения или введите индивидуальный комментарий заявителю. Заявитель получит официальное уведомление о решении проблемы."
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setResolveModalOpen(false)}
            >
              Отмена
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={isBusy}
              disabled={!resolveComment.trim()}
              onClick={handleConfirmResolve}
            >
              Подтвердить закрытие
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-neutral-300">Быстрые шаблоны:</span>
            <div className="flex flex-wrap gap-1.5">
              {RESOLVE_TEMPLATES.map((tmpl, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setResolveComment(tmpl)}
                  className={`text-[11px] text-left px-2 py-1 rounded border transition-colors ${
                    resolveComment === tmpl
                      ? "bg-neutral-100 border-neutral-200 text-neutral-950 font-medium"
                      : "bg-[#121316] border-neutral-800 text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50"
                  }`}
                >
                  {tmpl.slice(0, 38)}...
                </button>
              ))}
            </div>
          </div>

          <Textarea
            label="Текст решения для заявителя (публичный)"
            value={resolveComment}
            onChange={(e) => setResolveComment(e.target.value)}
            rows={3}
          />
        </div>
      </Modal>

      {/* Modal 2: Duplicate Ticket */}
      <Modal
        isOpen={dupModalOpen}
        onClose={() => setDupModalOpen(false)}
        title={`Отмена дубликата заявки #${ticketId}`}
        description="Заявка перейдет в статус 30 («Отменена»). Заявитель получит ссылку на основную заявку."
        maxWidth="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setDupModalOpen(false)}>
              Отмена
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={isBusy}
              disabled={!dupMasterId.trim()}
              onClick={handleConfirmDuplicate}
            >
              Отменить как дубль
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Input
            label="Номер основной заявки (Master Ticket ID)"
            placeholder="например: 12345"
            value={dupMasterId}
            onChange={(e) => setDupMasterId(e.target.value)}
            autoFocus
          />

          <Textarea
            label="Комментарий заявителю (опционально)"
            placeholder={`Заявка закрыта как дубликат обращения #${dupMasterId || "..."}`}
            value={dupComment}
            onChange={(e) => setDupComment(e.target.value)}
            rows={2}
          />
        </div>
      </Modal>

      {/* Modal 3: Redirect Ticket */}
      <Modal
        isOpen={redirModalOpen}
        onClose={() => setRedirModalOpen(false)}
        title={`Перенаправление заявки #${ticketId}`}
        description="Заявка будет отменена в текущем сервисе (статус 30) с регламентной инструкцией заявителю подать заявку в корректный раздел."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRedirModalOpen(false)}>
              Отмена
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={isBusy}
              disabled={!selectedServiceId}
              onClick={handleConfirmRedirect}
            >
              Перенаправить
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-neutral-300">Целевой сервис:</label>
            <select
              value={selectedServiceId}
              onChange={(e) => setSelectedServiceId(Number(e.target.value))}
              className="w-full bg-[#121316] border border-neutral-800 rounded px-3 py-1.5 text-xs text-neutral-100 focus:outline-none focus:ring-1 focus:ring-neutral-400 focus:border-neutral-500"
            >
              <option value="">-- Выберите сервис --</option>
              {services.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} (ID: {s.id})
                </option>
              ))}
            </select>
          </div>

          <Textarea
            label="Инструкция для заявителя"
            placeholder="Заявка отменена, т. к. создана не в подходящем разделе..."
            value={redirComment}
            onChange={(e) => setRedirComment(e.target.value)}
            rows={3}
          />
        </div>
      </Modal>
    </div>
  );
};
