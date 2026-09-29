import React from "react";
import { ArrowRight, ChevronDown, MessageSquare } from "lucide-react";

import type { TicketLifetimeEvent } from "@/shared/api";
import { Badge } from "@/shared/ui";

interface TicketActivityProps {
  events: TicketLifetimeEvent[];
  isFetching: boolean;
}

export const TicketActivity: React.FC<TicketActivityProps> = ({ events, isFetching }) => {
  const [open, setOpen] = React.useState(false);
  const visibleEvents = events.filter((event) => event.comment || (event.old_status_name && event.new_status_name));

  return (
    <section className="overflow-hidden rounded-lg border border-neutral-800/80 bg-[#101216]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-white/[0.025]"
      >
        <span className="flex items-center gap-2 text-xs font-medium text-neutral-300">
          <MessageSquare className="h-3.5 w-3.5 text-neutral-500" strokeWidth={1.5} />
          История заявки
          <span className="font-mono text-[10px] text-neutral-600">{visibleEvents.length}</span>
          {isFetching && <span className="text-[10px] font-normal text-neutral-500">обновление...</span>}
        </span>
        <ChevronDown className={`h-3.5 w-3.5 text-neutral-500 transition-transform ${open ? "rotate-180" : ""}`} strokeWidth={1.5} />
      </button>

      {open && (
        <div className="space-y-2 border-t border-neutral-800/80 bg-[#0d0f12] p-3">
          {visibleEvents.map((event, index) => {
            const privateComment = Boolean(event.is_private || event.is_private_comment);
            const statusChange = Boolean(event.old_status_name && event.new_status_name);
            return (
              <article
                key={event.id || index}
                className={`space-y-1.5 rounded border p-3 text-xs leading-relaxed ${
                  privateComment
                    ? "border-amber-800/40 bg-amber-950/15 text-amber-100"
                    : statusChange
                      ? "border-neutral-800/70 bg-[#101216] text-neutral-400"
                      : "border-neutral-800/80 bg-[#12151a] text-neutral-200"
                }`}
              >
                <header className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                  <span className="font-semibold text-neutral-200">{event.user_name || "Система"}</span>
                  <span className="flex items-center gap-2">
                    {privateComment && <Badge variant="warning">Служебная заметка</Badge>}
                    {event.created && (
                      <time className="font-mono text-[10px] text-neutral-500" dateTime={event.created}>
                        {new Date(event.created).toLocaleString("ru-RU")}
                      </time>
                    )}
                  </span>
                </header>
                {statusChange && (
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="text-neutral-500">Статус:</span>
                    <span className="text-neutral-500 line-through">{event.old_status_name}</span>
                    <ArrowRight className="h-3 w-3 text-neutral-600" strokeWidth={1.5} />
                    <span className="font-medium text-emerald-400">{event.new_status_name}</span>
                  </div>
                )}
                {event.comment && <p className="whitespace-pre-wrap select-text">{event.comment}</p>}
              </article>
            );
          })}

          {visibleEvents.length === 0 && (
            <div className="py-6 text-center text-[11px] italic text-neutral-500">
              {isFetching ? "Загрузка истории..." : "История изменений пока отсутствует"}
            </div>
          )}
        </div>
      )}
    </section>
  );
};
