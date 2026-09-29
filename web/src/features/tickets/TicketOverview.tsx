import React from "react";
import {
  Building,
  ChevronDown,
  Clock3,
  FileText,
  Mail,
  MapPin,
  Paperclip,
  Phone,
  UserRound,
} from "lucide-react";

import { HostBadge } from "@/features/diagnostics/HostBadge";
import { RAGSuggestion } from "@/features/knowledge-base/RAGSuggestion";
import type { TicketAttachment, TicketDetail } from "@/shared/api";
import { ticketsApi } from "@/shared/api";
import { DomainMetadataGrid } from "./DomainMetadataGrid";

interface TicketOverviewProps {
  ticket: TicketDetail;
  isFetching: boolean;
  onAttachmentClick: (attachment: TicketAttachment) => void;
  onApplySuggestion: (solution: string) => void;
}

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "bmp", "webp"]);

function isImageAttachment(filename: string): boolean {
  return IMAGE_EXTENSIONS.has(filename.split(".").pop()?.toLowerCase() || "");
}

export const TicketOverview: React.FC<TicketOverviewProps> = ({
  ticket,
  isFetching,
  onAttachmentClick,
  onApplySuggestion,
}) => {
  const [contextOpen, setContextOpen] = React.useState(false);
  const applicant = ticket.applicant_name || ticket.entities?.user_name || "Не указан";
  const workstation = ticket.entities?.pc_name || ticket.pc_name;
  const attachmentCount = ticket.attachments?.length || 0;

  return (
    <section className="overflow-hidden rounded-lg border border-neutral-800/80 bg-[#101216]">
      <div className="p-4">
        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-neutral-500">Запрос заявителя</p>
        <div className="mt-2 whitespace-pre-wrap text-sm leading-6 text-neutral-200 select-text">
          {ticket.description ? (
            ticket.description
          ) : isFetching ? (
            <span className="animate-pulse italic text-neutral-500">Загрузка полного текста заявки...</span>
          ) : (
            <span className="italic text-neutral-500">Описание отсутствует</span>
          )}
        </div>

        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 border-t border-neutral-800/70 pt-3 text-[11px] text-neutral-400">
          <span className="flex min-w-0 items-center gap-1.5">
            <UserRound className="h-3.5 w-3.5 shrink-0 text-neutral-500" strokeWidth={1.5} />
            <span className="truncate text-neutral-300">{applicant}</span>
          </span>
          <span className="flex min-w-0 items-center gap-1.5">
            <Building className="h-3.5 w-3.5 shrink-0 text-neutral-500" strokeWidth={1.5} />
            <span className="truncate">{ticket.service_name || "Сервис не указан"}</span>
          </span>
          <HostBadge host={workstation} />
          {ticket.created && (
            <time className="flex items-center gap-1.5 font-mono text-neutral-500" dateTime={ticket.created}>
              <Clock3 className="h-3.5 w-3.5" strokeWidth={1.5} />
              {new Date(ticket.created).toLocaleString("ru-RU")}
            </time>
          )}
        </div>
      </div>

      <button
        type="button"
        onClick={() => setContextOpen((open) => !open)}
        aria-expanded={contextOpen}
        className="flex w-full items-center justify-between border-t border-neutral-800/80 px-4 py-2.5 text-left text-[11px] font-medium text-neutral-400 transition-colors hover:bg-white/[0.025] hover:text-neutral-200"
      >
        <span>
          Контекст и вложения
          {attachmentCount > 0 && <span className="ml-1.5 text-neutral-600">{attachmentCount}</span>}
        </span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${contextOpen ? "rotate-180" : ""}`} strokeWidth={1.5} />
      </button>

      {contextOpen && (
        <div className="space-y-4 border-t border-neutral-800/80 bg-[#0d0f12] p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <ContextGroup title="Заявитель">
              <ContextLine icon={<UserRound />} value={applicant} />
              {(ticket.applicant_phone || ticket.entities?.phone) && (
                <ContextLine icon={<Phone />} value={ticket.applicant_phone || ticket.entities?.phone || ""} href={`tel:${ticket.applicant_phone || ticket.entities?.phone}`} mono />
              )}
              {(ticket.applicant_email || ticket.entities?.email) && (
                <ContextLine icon={<Mail />} value={ticket.applicant_email || ticket.entities?.email || ""} href={`mailto:${ticket.applicant_email || ticket.entities?.email}`} />
              )}
              {ticket.entities?.department && <ContextLine icon={<Building />} value={ticket.entities.department} />}
              {ticket.entities?.room && <ContextLine icon={<MapPin />} value={`Кабинет ${ticket.entities.room}`} />}
            </ContextGroup>

            <ContextGroup title="Обработка">
              <ContextValue label="Рабочая станция"><HostBadge host={workstation} /></ContextValue>
              <ContextValue label="Приоритет" value={ticket.priority_name || "Не указан"} />
              <ContextValue label="Автор" value={ticket.creator_name || "Не указан"} />
              <ContextValue label="Сервис" value={ticket.service_name || "Не указан"} />
            </ContextGroup>
          </div>

          <DomainMetadataGrid ticket={ticket} />

          {attachmentCount > 0 && (
            <div>
              <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
                <Paperclip className="h-3.5 w-3.5" strokeWidth={1.5} />
                Вложения ({attachmentCount})
              </div>
              <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {ticket.attachments?.map((attachment) => {
                  const name = attachment.name || attachment.Name || "Вложение";
                  const id = attachment.id || attachment.Id;
                  const image = isImageAttachment(name);
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => onAttachmentClick(attachment)}
                      className="group flex min-w-0 items-center gap-2 rounded border border-neutral-800 bg-[#12151a] p-2 text-left transition-colors hover:border-neutral-600"
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded border border-neutral-800 bg-[#181b20] text-neutral-400">
                        {image ? (
                          <img
                            src={ticketsApi.getAttachmentUrl(ticket.id, id)}
                            alt=""
                            className="h-full w-full object-cover transition-transform group-hover:scale-105"
                            onError={(event) => { event.currentTarget.style.display = "none"; }}
                          />
                        ) : (
                          <FileText className="h-4 w-4" strokeWidth={1.5} />
                        )}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-medium text-neutral-200">{name}</span>
                        <span className="block text-[10px] text-neutral-500">{image ? "Изображение" : "Документ"}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <RAGSuggestion query={ticket.name} onApplySolution={onApplySuggestion} />
        </div>
      )}
    </section>
  );
};

function ContextGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-neutral-800 bg-[#111419] p-3">
      <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">{title}</p>
      <div className="space-y-1.5 text-[11px]">{children}</div>
    </div>
  );
}

function ContextLine({ icon, value, href, mono }: { icon: React.ReactElement<{ className?: string; strokeWidth?: number }>; value: string; href?: string; mono?: boolean }) {
  const content = href
    ? <a href={href} className="truncate text-neutral-300 transition-colors hover:text-white">{value}</a>
    : <span className="truncate text-neutral-300">{value}</span>;
  return (
    <div className={`flex min-w-0 items-center gap-1.5 ${mono ? "font-mono" : ""}`}>
      {React.cloneElement(icon, { className: "h-3.5 w-3.5 shrink-0 text-neutral-500", strokeWidth: 1.5 })}
      {content}
    </div>
  );
}

function ContextValue({ label, value, children }: { label: string; value?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="shrink-0 text-neutral-500">{label}</span>
      {children || <span className="truncate text-right text-neutral-300">{value}</span>}
    </div>
  );
}
