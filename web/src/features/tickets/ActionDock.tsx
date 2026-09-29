import React from "react";
import { Globe, Lock, Send } from "lucide-react";

import { Button, useToast } from "@/shared/ui";

export interface ActionDockProps {
  onAddComment: (comment: string, isPrivate: boolean) => Promise<void>;
  isBusy?: boolean;
  commentDraft?: string;
  onCommentDraftChange?: (value: string) => void;
}

export const ActionDock: React.FC<ActionDockProps> = ({
  onAddComment,
  isBusy = false,
  commentDraft = "",
  onCommentDraftChange,
}) => {
  const [commentText, setCommentText] = React.useState(commentDraft);
  const [isPrivate, setIsPrivate] = React.useState(false);
  const [sending, setSending] = React.useState(false);
  const toast = useToast();

  React.useEffect(() => setCommentText(commentDraft), [commentDraft]);
  const change = (value: string) => {
    setCommentText(value);
    onCommentDraftChange?.(value);
  };
  const send = async (event?: React.FormEvent) => {
    event?.preventDefault();
    if (!commentText.trim()) return;
    setSending(true);
    try {
      await onAddComment(commentText.trim(), isPrivate);
      change("");
      toast.success(isPrivate ? "Служебная заметка добавлена" : "Ответ заявителю отправлен");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="shrink-0 border-t border-neutral-800 bg-[#0c0d0e] p-3">
      <form onSubmit={send} className="space-y-2">
        <textarea
          value={commentText}
          onChange={(event) => change(event.target.value)}
          onKeyDown={(event) => { if (event.ctrlKey && event.key === "Enter") send(); }}
          placeholder={isPrivate ? "Служебная заметка…" : "Ответ заявителю…"}
          rows={2}
          className={`w-full resize-none rounded border p-2.5 text-xs outline-none focus:ring-1 ${isPrivate ? "border-amber-800/50 bg-amber-950/20 text-amber-100 focus:ring-amber-500" : "border-neutral-800 bg-[#121316] text-neutral-100 focus:ring-neutral-500"}`}
        />
        <div className="flex items-center justify-between">
          <div className="flex rounded border border-neutral-800 bg-[#121316] p-0.5 text-[11px]">
            <button type="button" onClick={() => setIsPrivate(false)} className={`flex items-center gap-1 rounded px-2 py-1 ${!isPrivate ? "bg-neutral-800 text-neutral-100" : "text-neutral-500"}`}><Globe className="h-3 w-3" />Заявителю</button>
            <button type="button" onClick={() => setIsPrivate(true)} className={`flex items-center gap-1 rounded px-2 py-1 ${isPrivate ? "bg-amber-950/50 text-amber-300" : "text-neutral-500"}`}><Lock className="h-3 w-3" />Служебная заметка</button>
          </div>
          <Button type="submit" size="sm" loading={sending} disabled={!commentText.trim() || isBusy} icon={<Send className="h-3 w-3" />}>Отправить</Button>
        </div>
      </form>
    </div>
  );
};
