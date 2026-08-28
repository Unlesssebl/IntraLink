import { useState, useEffect, useRef } from 'react';
import type { Ticket, Page } from '../data/mock';
import { statusConfig, priorityConfig } from '../data/mock';

interface Props {
  tickets: Ticket[];
  onClose: () => void;
  onSelectTicket: (id: string) => void;
  onNavigate: (page: Page) => void;
}

const pages: { id: Page; label: string; desc: string }[] = [
  { id: 'queue', label: 'Очередь заявок', desc: 'Все заявки' },
  { id: 'automation', label: 'Центр автоматизации', desc: 'Правила и AI-потоки' },
  { id: 'settings', label: 'Настройки', desc: 'Конфигурация системы' },
];

export default function CommandPalette({ tickets, onClose, onSelectTicket, onNavigate }: Props) {
  const [query, setQuery] = useState('');
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const q = query.toLowerCase().trim();

  const matchedPages = q
    ? pages.filter(p => p.label.toLowerCase().includes(q) || p.desc.toLowerCase().includes(q))
    : pages;

  const matchedTickets = q
    ? tickets.filter(t =>
        t.id.toLowerCase().includes(q) ||
        t.title.toLowerCase().includes(q) ||
        t.requesterName.toLowerCase().includes(q) ||
        t.host.toLowerCase().includes(q)
      ).slice(0, 8)
    : tickets.filter(t => t.status !== 'resolved').slice(0, 5);

  type ResultItem =
    | { kind: 'page'; id: Page; label: string; desc: string }
    | { kind: 'ticket'; ticket: Ticket };

  const results: ResultItem[] = [
    ...matchedPages.map(p => ({ kind: 'page' as const, ...p })),
    ...matchedTickets.map(t => ({ kind: 'ticket' as const, ticket: t })),
  ];

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx(i => Math.min(i + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      const item = results[activeIdx];
      if (!item) return;
      if (item.kind === 'page') onNavigate(item.id);
      else onSelectTicket(item.ticket.id);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh] px-4"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/30 dark:bg-black/50" />
      <div
        className="relative w-full max-w-xl bg-white dark:bg-neutral-900 rounded-lg border border-neutral-200 dark:border-neutral-700 shadow-xl overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Input */}
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-neutral-100 dark:border-neutral-800">
          <svg width="15" height="15" viewBox="0 0 15 15" fill="none" className="text-neutral-400 shrink-0">
            <circle cx="6.5" cy="6.5" r="4" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={e => { setQuery(e.target.value); setActiveIdx(0); }}
            onKeyDown={handleKey}
            placeholder="Поиск по разделам и заявкам..."
            className="flex-1 text-sm text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 dark:placeholder-neutral-600 bg-transparent outline-none"
          />
          <kbd className="text-[11px] font-mono bg-neutral-100 dark:bg-neutral-800 text-neutral-400 px-1.5 py-0.5 rounded">
            Esc
          </kbd>
        </div>

        {/* Results */}
        <div ref={listRef} className="max-h-80 overflow-y-auto py-1">
          {!q && (
            <p className="px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">
              Разделы
            </p>
          )}
          {results.length === 0 && (
            <p className="px-4 py-8 text-sm text-center text-neutral-400">Ничего не найдено</p>
          )}
          {results.map((item, idx) => {
            const active = idx === activeIdx;
            if (item.kind === 'page') {
              return (
                <button
                  key={`page-${item.id}`}
                  onClick={() => onNavigate(item.id)}
                  onMouseEnter={() => setActiveIdx(idx)}
                  className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${active ? 'bg-neutral-100 dark:bg-neutral-800' : ''}`}
                >
                  <div className="w-7 h-7 bg-neutral-100 dark:bg-neutral-800 rounded flex items-center justify-center shrink-0">
                    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" className="text-neutral-500">
                      <rect x="1" y="1.5" width="11" height="1.8" rx="0.9" fill="currentColor"/>
                      <rect x="1" y="5.6" width="11" height="1.8" rx="0.9" fill="currentColor"/>
                      <rect x="1" y="9.7" width="7" height="1.8" rx="0.9" fill="currentColor"/>
                    </svg>
                  </div>
                  <div>
                    <p className="text-sm font-medium text-neutral-800 dark:text-neutral-200">{item.label}</p>
                    <p className="text-xs text-neutral-400">{item.desc}</p>
                  </div>
                  {active && <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="ml-auto text-neutral-400 shrink-0">
                    <path d="M4 2l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>}
                </button>
              );
            }
            const t = item.ticket;
            return (
              <button
                key={`ticket-${t.id}`}
                onClick={() => onSelectTicket(t.id)}
                onMouseEnter={() => setActiveIdx(idx)}
                className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${active ? 'bg-neutral-100 dark:bg-neutral-800' : ''}`}
              >
                <span className="font-mono text-[11px] text-neutral-400 shrink-0 w-14">{t.id}</span>
                <span className="flex-1 text-sm text-neutral-800 dark:text-neutral-200 truncate">{t.title}</span>
                <span className={`text-[11px] px-1.5 py-0.5 rounded-sm font-medium shrink-0 ${statusConfig[t.status].className}`}>
                  {statusConfig[t.status].label}
                </span>
              </button>
            );
          })}
        </div>

        {/* Footer hints */}
        <div className="px-4 py-2 border-t border-neutral-100 dark:border-neutral-800 flex items-center gap-3 text-[11px] text-neutral-400">
          <span><kbd className="font-mono bg-neutral-100 dark:bg-neutral-800 px-1 rounded">↑↓</kbd> навигация</span>
          <span><kbd className="font-mono bg-neutral-100 dark:bg-neutral-800 px-1 rounded">↵</kbd> открыть</span>
          <span><kbd className="font-mono bg-neutral-100 dark:bg-neutral-800 px-1 rounded">Esc</kbd> закрыть</span>
        </div>
      </div>
    </div>
  );
}
