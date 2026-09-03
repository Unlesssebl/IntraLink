import { useState, useEffect, useRef } from 'react';
import type { Ticket, Page } from '../data/mock';
import { statusConfig } from '../data/mock';

interface Props {
  tickets: Ticket[];
  onClose: () => void;
  onSelectTicket: (id: string) => void;
  onNavigate: (page: Page) => void;
}

export default function CommandPalette({ tickets, onClose, onSelectTicket }: Props) {
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

  const matchedTickets = q
    ? tickets
        .filter(
          t =>
            t.id.toLowerCase().includes(q) ||
            String(t.rawId).includes(q) ||
            t.title.toLowerCase().includes(q) ||
            t.requesterName.toLowerCase().includes(q) ||
            t.host.toLowerCase().includes(q) ||
            (t.serviceName || '').toLowerCase().includes(q)
        )
        .slice(0, 10)
    : [];

  const handleKey = (e: React.KeyboardEvent) => {
    if (matchedTickets.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx(i => Math.min(i + 1, matchedTickets.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      const item = matchedTickets[activeIdx];
      if (!item) return;
      onSelectTicket(item.id);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[18vh] px-4"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/40 backdrop-blur-xs" />
      <div
        className="relative w-full max-w-xl bg-white dark:bg-neutral-900 rounded-lg border border-neutral-200 dark:border-neutral-700 shadow-2xl overflow-hidden"
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
            onChange={e => {
              setQuery(e.target.value);
              setActiveIdx(0);
            }}
            onKeyDown={handleKey}
            placeholder="Введите номер, тему, заявителя или ПК для поиска..."
            className="flex-1 text-sm text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 bg-transparent outline-none"
          />
          <kbd className="text-[10px] font-sans bg-neutral-100 dark:bg-neutral-800 text-neutral-400 px-1.5 py-0.5 rounded border border-neutral-200/60 dark:border-neutral-700">
            Esc
          </kbd>
        </div>

        {/* Results Area */}
        <div ref={listRef} className="max-h-80 overflow-y-auto py-1">
          {!q ? (
            <div className="px-4 py-8 text-center text-neutral-400 dark:text-neutral-500">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" className="mx-auto mb-2 opacity-40">
                <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
              <p className="text-xs">Начните вводить номер заявки, тему, ФИО или имя ПК...</p>
            </div>
          ) : matchedTickets.length === 0 ? (
            <p className="px-4 py-8 text-xs text-center text-neutral-400">
              По запросу «{query}» ничего не найдено в очереди
            </p>
          ) : (
            matchedTickets.map((t, idx) => {
              const active = idx === activeIdx;
              return (
                <button
                  key={`ticket-${t.id}`}
                  onClick={() => onSelectTicket(t.id)}
                  onMouseEnter={() => setActiveIdx(idx)}
                  className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors cursor-pointer ${
                    active ? 'bg-neutral-100 dark:bg-neutral-800' : 'hover:bg-neutral-50 dark:hover:bg-neutral-800/50'
                  }`}
                >
                  <span className="font-sans font-medium tabular-nums text-[11px] text-neutral-500 shrink-0 w-14">
                    #{t.rawId}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] font-medium text-neutral-900 dark:text-neutral-100 truncate">
                      {t.title}
                    </p>
                    <p className="text-[11px] text-neutral-400 truncate mt-0.5">
                      {t.requesterName} {t.host ? `· ${t.host}` : ''} · {t.serviceName}
                    </p>
                  </div>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 ${statusConfig[t.status].className}`}>
                    {statusConfig[t.status].label}
                  </span>
                </button>
              );
            })
          )}
        </div>

        {/* Footer hints */}
        <div className="px-4 py-2 border-t border-neutral-100 dark:border-neutral-800 flex items-center gap-3 text-[11px] text-neutral-400">
          <span><kbd className="font-sans bg-neutral-100 dark:bg-neutral-800 px-1 py-0.5 rounded text-[10px]">↑↓</kbd> выбор</span>
          <span><kbd className="font-sans bg-neutral-100 dark:bg-neutral-800 px-1 py-0.5 rounded text-[10px]">↵</kbd> открыть</span>
          <span><kbd className="font-sans bg-neutral-100 dark:bg-neutral-800 px-1 py-0.5 rounded text-[10px]">Esc</kbd> закрыть</span>
        </div>
      </div>
    </div>
  );
}
