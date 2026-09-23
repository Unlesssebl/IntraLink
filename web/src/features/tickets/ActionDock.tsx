import React, { useState, useEffect } from "react";
import {
  CheckCircle,
  CheckCheck,
  Copy,
  ArrowRightLeft,
  Send,
  Lock,
  Globe,
  Sparkles,
} from "lucide-react";
import { Button, Modal, Textarea, Input, KbdBadge, Badge } from "@/shared/ui";
import { ticketsApi, ServiceItem } from "@/shared/api";

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
  const [services, setServices] = useState<ServiceItem[]>([]);
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

  // Pre-load services catalog for redirect
  useEffect(() => {
    if (redirModalOpen && services.length === 0) {
      ticketsApi
        .getServices()
        .then((data) => {
          setServices(data.filter((s) => s.is_active !== false));
          if (data.length > 0 && !selectedServiceId) {
            setSelectedServiceId(data[0].id);
          }
        })
        .catch(() => {});
    }
  }, [redirModalOpen, services.length, selectedServiceId]);

  // Global hotkeys: Alt+1, Alt+2, Alt+3, Alt+4
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.altKey) {
        if (e.key === "1") {
          e.preventDefault();
          onTake();
        } else if (e.key === "2") {
          e.preventDefault();
          setResolveModalOpen(true);
        } else if (e.key === "3") {
          e.preventDefault();
          setDupModalOpen(true);
        } else if (e.key === "4") {
          e.preventDefault();
          setRedirModalOpen(true);
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onTake]);

  const handleSendComment = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!commentText.trim()) return;

    try {
      setSendingComment(true);
      await onAddComment(commentText.trim(), isPrivate);
      handleCommentChange("");
    } finally {
      setSendingComment(false);
    }
  };

  const handleConfirmResolve = async () => {
    if (!resolveComment.trim()) return;
    await onResolve(resolveComment.trim());
    setResolveModalOpen(false);
  };

  const handleConfirmDuplicate = async () => {
    const masterId = parseInt(dupMasterId.replace("#", "").trim(), 10);
    if (isNaN(masterId) || masterId <= 0) {
      alert("Укажите корректный номер основной заявки (Master Ticket ID)");
      return;
    }
    const finalComment =
      dupComment.trim() ||
      `Заявка закрыта как дубликат обращения #${masterId}. Дальнейшие работы ведутся в основной заявке #${masterId}.`;

    await onDuplicate(masterId, finalComment);
    setDupModalOpen(false);
    setDupMasterId("");
    setDupComment("");
  };

  const handleConfirmRedirect = async () => {
    if (!selectedServiceId) {
      alert("Выберите целевой сервис");
      return;
    }
    const sObj = services.find((s) => s.id === Number(selectedServiceId));
    const sName = sObj ? sObj.name : `ID ${selectedServiceId}`;
    const finalComment =
      redirComment.trim() ||
      `Заявка перенаправлена в сервис: «${sName}». Пожалуйста, ожидайте ответа специалистов профильного подразделения.`;

    await onRedirect(Number(selectedServiceId), finalComment);
    setRedirModalOpen(false);
    setRedirComment("");
  };

  const isAlreadyInWork = statusId === 2;

  return (
    <div className="border-t border-[#1e2330] bg-[#0d1017] p-3 space-y-2.5 shrink-0">
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
                ? "🔒 Внутренняя заметка инженера (Ctrl+Enter для отправки)..."
                : "💬 Ответ заявителю (Ctrl+Enter для отправки)..."
            }
            rows={2}
            className={`w-full text-xs rounded-md border p-2.5 placeholder:text-slate-500 focus:outline-none focus:ring-1 resize-none ${
              isPrivate
                ? "bg-amber-950/20 border-amber-800/50 text-amber-200 focus:ring-amber-500"
                : "bg-[#131724] border-[#22293a] text-slate-200 focus:ring-indigo-500"
            }`}
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 bg-[#141824] border border-[#222838] p-0.5 rounded-md text-[11px]">
            <button
              type="button"
              onClick={() => setIsPrivate(false)}
              className={`flex items-center gap-1 px-2 py-0.5 rounded transition-colors ${
                !isPrivate
                  ? "bg-indigo-600 text-white font-medium"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Globe className="w-3 h-3" />
              <span>Заявителю</span>
            </button>
            <button
              type="button"
              onClick={() => setIsPrivate(true)}
              className={`flex items-center gap-1 px-2 py-0.5 rounded transition-colors ${
                isPrivate
                  ? "bg-amber-600 text-white font-medium"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Lock className="w-3 h-3" />
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
      <div className="grid grid-cols-4 gap-2 pt-1 border-t border-[#1a1f2b]">
        {/* Alt + 1: В работу */}
        <button
          type="button"
          onClick={onTake}
          disabled={isBusy || isAlreadyInWork}
          className={`flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-md border text-xs font-medium transition-all ${
            isAlreadyInWork
              ? "bg-[#141924] border-emerald-900/40 text-emerald-400/70 opacity-80 cursor-default"
              : "bg-[#151a26] border-[#252c3e] hover:border-emerald-500/50 hover:bg-[#182030] text-slate-200 active:scale-95"
          }`}
          title="Взять заявку в работу (Alt+1)"
        >
          <CheckCircle className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
          <span className="truncate">В работу</span>
          <KbdBadge shortcut="Alt+1" />
        </button>

        {/* Alt + 2: Решено */}
        <button
          type="button"
          onClick={() => setResolveModalOpen(true)}
          disabled={isBusy}
          className="flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-md border border-[#252c3e] bg-[#151a26] hover:border-teal-500/50 hover:bg-[#182030] text-slate-200 text-xs font-medium transition-all active:scale-95"
          title="Закрыть с решением (Alt+2)"
        >
          <CheckCheck className="w-3.5 h-3.5 text-teal-400 shrink-0" />
          <span className="truncate">Решено</span>
          <KbdBadge shortcut="Alt+2" />
        </button>

        {/* Alt + 3: Дубликат */}
        <button
          type="button"
          onClick={() => setDupModalOpen(true)}
          disabled={isBusy}
          className="flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-md border border-[#252c3e] bg-[#151a26] hover:border-rose-500/50 hover:bg-[#182030] text-slate-200 text-xs font-medium transition-all active:scale-95"
          title="Отменить как дубликат (Alt+3)"
        >
          <Copy className="w-3.5 h-3.5 text-rose-400 shrink-0" />
          <span className="truncate">Дубликат</span>
          <KbdBadge shortcut="Alt+3" />
        </button>

        {/* Alt + 4: Перенаправить */}
        <button
          type="button"
          onClick={() => setRedirModalOpen(true)}
          disabled={isBusy}
          className="flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-md border border-[#252c3e] bg-[#151a26] hover:border-indigo-500/50 hover:bg-[#182030] text-slate-200 text-xs font-medium transition-all active:scale-95"
          title="Перенаправить в другой сервис (Alt+4)"
        >
          <ArrowRightLeft className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
          <span className="truncate">Перенаправить</span>
          <KbdBadge shortcut="Alt+4" />
        </button>
      </div>

      {/* Modal 1: Resolve Ticket */}
      <Modal
        isOpen={resolveModalOpen}
        onClose={() => setResolveModalOpen(false)}
        title={`Закрытие заявки #${ticketId}`}
        description="Выберите готовый шаблон решения или введите индивидуальный комментарий заявителю."
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
            <span className="text-xs font-medium text-slate-300">Быстрые шаблоны:</span>
            <div className="flex flex-wrap gap-1.5">
              {RESOLVE_TEMPLATES.map((tmpl, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setResolveComment(tmpl)}
                  className={`text-[11px] text-left px-2 py-1 rounded border transition-colors ${
                    resolveComment === tmpl
                      ? "bg-teal-950/40 border-teal-600/60 text-teal-200 font-medium"
                      : "bg-[#141824] border-[#22293a] text-slate-400 hover:text-slate-200 hover:bg-[#181d2c]"
                  }`}
                >
                  {tmpl.slice(0, 38)}...
                </button>
              ))}
            </div>
          </div>

          <Textarea
            label="Текст решения для заявителя"
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
        description="Заявка будет отменена в текущем сервисе с четкой инструкцией для заявителя."
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
            <label className="text-xs font-medium text-slate-300">Целевой сервис:</label>
            <select
              value={selectedServiceId}
              onChange={(e) => setSelectedServiceId(Number(e.target.value))}
              className="w-full bg-[#131724] border border-[#22293a] rounded-md px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
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
            placeholder="Заявка перенаправлена в целевой сервис..."
            value={redirComment}
            onChange={(e) => setRedirComment(e.target.value)}
            rows={3}
          />
        </div>
      </Modal>
    </div>
  );
};
