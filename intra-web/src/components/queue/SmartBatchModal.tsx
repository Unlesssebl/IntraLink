import React from 'react';
import type { Ticket } from '../../data/mock';
import { IconSparkles, IconPencil, IconRocket, IconClose } from '../Icons';

export interface SmartBatchItem {
  ticket: Ticket;
  selected: boolean;
  comment: string;
  minutes: number;
  isEditing: boolean;
}

interface SmartBatchModalProps {
  items: SmartBatchItem[];
  processingBulk: boolean;
  onClose: () => void;
  onUpdateItems: React.Dispatch<React.SetStateAction<{ open: boolean; items: SmartBatchItem[] } | null>>;
  onExecute: () => void;
}

export default function SmartBatchModal({
  items,
  processingBulk,
  onClose,
  onUpdateItems,
  onExecute,
}: SmartBatchModalProps) {
  const selectedCount = items.filter(x => x.selected).length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-2xs p-4">
      <div className="w-full max-w-2xl bg-white dark:bg-neutral-900 rounded-2xl shadow-2xl border border-neutral-200 dark:border-neutral-800 flex flex-col max-h-[85vh] animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="px-6 py-4 border-b border-neutral-200 dark:border-neutral-800 flex items-center justify-between shrink-0">
          <div>
            <h3 className="text-base font-bold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <IconSparkles size={16} className="text-neutral-700 dark:text-neutral-300" />
              <span>Сводный план индивидуального выполнения</span>
              <span className="px-2 py-0.5 text-xs bg-neutral-100 text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200 rounded-full font-bold">
                {selectedCount} из {items.length}
              </span>
            </h3>
            <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
              Каждая заявка будет исполнена в инфраструктуре и переведена в свой целевой статус с регламентным ответом заявителю
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={processingBulk}
            className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 cursor-pointer p-1"
          >
            <IconClose size={16} />
          </button>
        </div>

        {/* Body: Tickets list */}
        <div className="flex-1 overflow-y-auto p-5 space-y-3">
          {items.map((item, idx) => {
            const t = item.ticket;
            const plan = t.aiPlan;
            return (
              <div
                key={t.id}
                className={`p-3.5 rounded-xl border transition-all ${
                  item.selected
                    ? 'bg-neutral-50 dark:bg-neutral-800/60 border-neutral-200 dark:border-neutral-700 shadow-2xs'
                    : 'bg-neutral-100/40 dark:bg-neutral-900/40 border-neutral-200/50 dark:border-neutral-800/50 opacity-60'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3 flex-1 min-w-0">
                    <input
                      type="checkbox"
                      checked={item.selected}
                      onChange={e => {
                        const checked = e.target.checked;
                        onUpdateItems(prev => prev ? {
                          ...prev,
                          items: prev.items.map((it, i) => i === idx ? { ...it, selected: checked } : it),
                        } : null);
                      }}
                      className="w-4 h-4 accent-neutral-900 dark:accent-neutral-100 cursor-pointer rounded mt-1 shrink-0"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap mb-1">
                        <a
                          href={`/admin/api/tasks/${t.rawId}/open`}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono font-bold text-[13px] text-neutral-900 dark:text-neutral-100 hover:underline"
                        >
                          #{t.rawId}
                        </a>
                        <span className="font-bold text-[14px] text-neutral-900 dark:text-neutral-100 truncate max-w-sm">
                          {t.title}
                        </span>
                        {plan && (
                          <span className={`text-[11px] font-bold px-2 py-0.5 rounded border ${plan.badgeClass} inline-flex items-center gap-1`}>
                            <IconSparkles size={11} className="opacity-70" />
                            <span>{plan.actionBadge}</span>
                          </span>
                        )}
                      </div>
                      <div className="text-[12.5px] text-neutral-500 dark:text-neutral-400 flex items-center gap-2 flex-wrap">
                        <span>{t.requesterName}</span>
                        {t.room && <span>· каб. {t.room}</span>}
                        {t.host && <span className="font-mono font-semibold bg-neutral-200 dark:bg-neutral-700 px-1.5 py-0.2 rounded text-[11px]">{t.host}</span>}
                        <span>· {plan?.targetStatusName || t.statusName} ({item.minutes} мин)</span>
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={() => {
                      onUpdateItems(prev => prev ? {
                        ...prev,
                        items: prev.items.map((it, i) => i === idx ? { ...it, isEditing: !it.isEditing } : it),
                      } : null);
                    }}
                    className="text-[12px] text-neutral-700 hover:text-neutral-950 dark:text-neutral-300 dark:hover:text-neutral-100 hover:underline font-bold shrink-0 cursor-pointer px-1.5 py-0.5 inline-flex items-center gap-1"
                  >
                    {item.isEditing ? (
                      'Свернуть'
                    ) : (
                      <>
                        <IconPencil size={11} />
                        <span>Изменить</span>
                      </>
                    )}
                  </button>
                </div>

                {/* Editing block */}
                {item.isEditing ? (
                  <div className="mt-3 pt-3 border-t border-neutral-200 dark:border-neutral-700 space-y-2">
                    <label className="text-[11px] font-bold text-neutral-400 uppercase tracking-wider block">
                      Текст ответа заявителю:
                    </label>
                    <textarea
                      value={item.comment}
                      onChange={e => {
                        const text = e.target.value;
                        onUpdateItems(prev => prev ? {
                          ...prev,
                          items: prev.items.map((it, i) => i === idx ? { ...it, comment: text } : it),
                        } : null);
                      }}
                      rows={2}
                      className="w-full px-3 py-2 text-[13px] rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 outline-none resize-none"
                    />
                    <div className="flex items-center gap-2 text-[12.5px] text-neutral-500">
                      <span>Трудозатраты:</span>
                      <input
                        type="number"
                        value={item.minutes}
                        onChange={e => {
                          const m = Number(e.target.value);
                          onUpdateItems(prev => prev ? {
                            ...prev,
                            items: prev.items.map((it, i) => i === idx ? { ...it, minutes: m } : it),
                          } : null);
                        }}
                        min={0}
                        max={240}
                        className="w-14 h-6 px-1.5 bg-white dark:bg-neutral-900 border border-neutral-300 dark:border-neutral-700 rounded text-center font-mono font-bold text-[11.5px]"
                      />
                      <span>мин</span>
                    </div>
                  </div>
                ) : (
                  <div className="mt-2 text-[12.5px] text-neutral-600 dark:text-neutral-400 italic bg-neutral-100/70 dark:bg-neutral-900/60 p-2.5 rounded-md line-clamp-2">
                    «{item.comment}»
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-neutral-200 dark:border-neutral-800 flex items-center justify-between shrink-0 bg-neutral-50 dark:bg-neutral-950/60 rounded-b-2xl">
          <div className="text-xs text-neutral-500 dark:text-neutral-400">
            Выбрано к исполнению: <strong className="text-neutral-900 dark:text-neutral-100">{selectedCount}</strong>
          </div>
          <div className="flex items-center gap-2.5">
            <button
              onClick={onClose}
              disabled={processingBulk}
              className="px-4 py-2 text-[13px] font-medium text-neutral-700 dark:text-neutral-300 hover:bg-neutral-200 dark:hover:bg-neutral-800 rounded-lg transition-colors cursor-pointer"
            >
              Отмена
            </button>
            <button
              onClick={onExecute}
              disabled={processingBulk || selectedCount === 0}
              className="px-5 py-2 bg-neutral-900 hover:bg-neutral-800 text-white dark:bg-neutral-100 dark:hover:bg-white dark:text-neutral-900 text-[13px] font-bold rounded-lg transition-all shadow-md cursor-pointer disabled:opacity-50 flex items-center gap-2"
            >
              {processingBulk ? (
                <>
                  <svg className="animate-spin h-4 w-4 text-current" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                  </svg>
                  <span>Выполнение пакета...</span>
                </>
              ) : (
                <>
                  <IconRocket size={14} />
                  <span>Запустить выполнение ({selectedCount})</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
