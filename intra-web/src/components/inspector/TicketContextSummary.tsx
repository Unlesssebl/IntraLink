import { useState, type ReactNode } from 'react';
import type { Ticket } from '../../data/mock';
import type { TaskDetails } from '../../lib/types';
import { useResolvedEntities } from './useResolvedEntities';
import {
  IconBuilding,
  IconChevronDown,
  IconPaperclip,
  IconPhone,
  IconUser,
} from '../Icons';

interface TicketContextSummaryProps {
  ticket: Ticket;
  details: TaskDetails | null;
  rawId: number;
  attachmentsCount: number;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

export default function TicketContextSummary({
  ticket,
  details,
  rawId: _rawId,
  attachmentsCount,
  onToast,
}: TicketContextSummaryProps) {
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const description = ticket.description || details?.description || '';

  const entities = useResolvedEntities(ticket, details);
  const { requester } = entities;

  const location = requester.locationStr;
  const phone = requester.phone;
  const requesterName = requester.name;

  const meta = [
    requesterName
      ? {
          key: 'requester',
          label: 'Заявитель',
          value: requesterName,
          subValue: requester.login ? `@${requester.login}` : undefined,
          icon: <IconUser size={14} />,
        }
      : null,
    phone
      ? {
          key: 'phone',
          label: 'Телефон',
          value: phone,
          icon: <IconPhone size={14} />,
          onCopy: () => {
            navigator.clipboard.writeText(phone).then(() => {
              onToast({ type: 'info', message: `Телефон ${phone} скопирован` });
            });
          },
        }
      : null,
    location
      ? {
          key: 'location',
          label: 'Размещение',
          value: location,
          icon: <IconBuilding size={14} />,
        }
      : null,
    ticket.executors
      ? {
          key: 'executor',
          label: 'Исполнитель',
          value: ticket.executors,
          icon: <IconUser size={14} />,
        }
      : null,
  ].filter(Boolean) as Array<{
    key: string;
    label: string;
    value: string;
    subValue?: string;
    icon: ReactNode;
    onCopy?: () => void;
  }>;

  return (
    <section className="rounded-2xl border border-neutral-200/90 bg-white shadow-xs dark:border-neutral-800 dark:bg-neutral-900 overflow-hidden">
      <div className="space-y-3.5 p-4">
        {/* Заголовок карточки контекста */}
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] text-neutral-500 dark:text-neutral-400">
            Контекст и заявитель
          </h3>
          <div className="flex items-center gap-2 text-[11px] text-neutral-500 dark:text-neutral-400">
            {attachmentsCount > 0 && (
              <span className="inline-flex items-center gap-1">
                <IconPaperclip size={13} />
                {attachmentsCount}
              </span>
            )}
            <span className="rounded-md bg-neutral-100 px-2 py-1 font-medium dark:bg-neutral-800">
              {ticket.serviceName}
            </span>
          </div>
        </div>

        {/* Метаданные заявителя и размещения */}
        {meta.length > 0 && (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2">
            {meta.map((item) => (
              <div
                key={item.key}
                onClick={item.onCopy}
                className={`flex min-w-0 items-center gap-2.5 rounded-xl border border-neutral-100 bg-neutral-50/70 px-2.5 py-2 transition-colors dark:border-neutral-800/80 dark:bg-neutral-800/40 ${
                  item.onCopy ? 'cursor-pointer hover:border-neutral-300 dark:hover:border-neutral-700' : ''
                }`}
                title={item.onCopy ? `Нажмите, чтобы скопировать ${item.label.toLowerCase()}` : item.value}
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white text-neutral-500 shadow-xs dark:bg-neutral-900 dark:text-neutral-400">
                  {item.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[9.5px] font-bold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                    {item.label}
                  </span>
                  <span className="block truncate text-xs font-semibold text-neutral-900 dark:text-neutral-100">
                    {item.value}
                  </span>
                  {item.subValue && (
                    <span className="block truncate font-mono text-[10px] text-neutral-400">
                      {item.subValue}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Описание проблемы */}
        <div className="space-y-1.5">
          <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-neutral-400 dark:text-neutral-500">
            Описание заявки
          </div>
          {description ? (
            <>
              <p
                className={`whitespace-pre-wrap text-[13px] leading-5 text-neutral-800 dark:text-neutral-200 ${
                  descriptionExpanded ? '' : 'line-clamp-4'
                }`}
              >
                {description}
              </p>
              {description.length > 220 && (
                <button
                  type="button"
                  onClick={() => setDescriptionExpanded((value) => !value)}
                  aria-expanded={descriptionExpanded}
                  className="inline-flex min-h-7 items-center gap-1 rounded text-xs font-semibold text-neutral-600 outline-none transition-colors hover:text-neutral-950 focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-neutral-400 dark:hover:text-neutral-100 cursor-pointer"
                >
                  {descriptionExpanded ? 'Свернуть описание' : 'Показать полностью'}
                  <IconChevronDown
                    size={13}
                    className={`transition-transform ${descriptionExpanded ? 'rotate-180' : ''}`}
                  />
                </button>
              )}
            </>
          ) : (
            <p className="text-[13px] text-neutral-400 italic">Описание отсутствует</p>
          )}
        </div>
      </div>
    </section>
  );
}
