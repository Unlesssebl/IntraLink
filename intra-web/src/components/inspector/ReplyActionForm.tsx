import React, { useRef } from 'react';
import type { Ticket } from '../../data/mock';

interface TemplateItem {
  key: string;
  name: string;
  template?: string;
  minutes?: number;
  status_id?: number;
}

export interface MainActionConfig {
  label: string;
  statusId: number;
  actionType?: 'wlan' | 'printer' | string;
  buttonClass: string;
  isAiPlan?: boolean;
}

interface ReplyActionFormProps {
  ticket: Ticket;
  replyMode: 'reply' | 'internal';
  onSetReplyMode: (mode: 'reply' | 'internal') => void;
  replyText: string;
  onChangeReplyText: (text: string) => void;
  templates: TemplateItem[];
  selectedTemplateKey: string;
  onSelectTemplate: (key: string) => void;
  isStatusAllowed: (statusId: number) => boolean;
  isActionsMenuOpen: boolean;
  onToggleActionsMenu: () => void;
  onSelectMenuStatus: (statusId: number, comment: string, minutes: number) => void;
  confirmingCancel: boolean;
  onCancelTicketClick: () => void;
  onTakeOwnership: () => void;
  onExecuteMainAction: () => void;
  mainAction: MainActionConfig;
  submitting: boolean;
}

export default function ReplyActionForm({
  ticket,
  replyMode,
  onSetReplyMode,
  replyText,
  onChangeReplyText,
  templates,
  selectedTemplateKey,
  onSelectTemplate,
  isStatusAllowed,
  isActionsMenuOpen,
  onToggleActionsMenu,
  onSelectMenuStatus,
  confirmingCancel,
  onCancelTicketClick,
  onTakeOwnership,
  onExecuteMainAction,
  mainAction,
  submitting,
}: ReplyActionFormProps) {
  const actionsMenuRef = useRef<HTMLDivElement>(null);

  return (
    <div className="space-y-2.5">
      {/* Row: Mode Switch, Templates & Clear */}
      <div className="flex items-center justify-between gap-2 flex-wrap pt-0.5">
        <div className="flex bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded-lg border border-neutral-200 dark:border-neutral-700">
          <button
            type="button"
            onClick={() => onSetReplyMode('reply')}
            className={`px-3 py-1 rounded-md text-[12px] font-semibold transition-colors cursor-pointer ${
              replyMode === 'reply'
                ? 'bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-2xs'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            Пользователю
          </button>
          <button
            type="button"
            onClick={() => onSetReplyMode('internal')}
            className={`px-3 py-1 rounded-md text-[12px] font-semibold transition-colors cursor-pointer ${
              replyMode === 'internal'
                ? 'bg-amber-100 dark:bg-amber-900/80 text-amber-900 dark:text-amber-100 shadow-2xs'
                : 'text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            Скрытый
          </button>
        </div>

        <div className="flex items-center gap-2">
          {templates.length > 0 && (
            <select
              value={selectedTemplateKey}
              onChange={e => onSelectTemplate(e.target.value)}
              className="text-[12px] bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg px-2.5 py-1 text-neutral-700 dark:text-neutral-300 outline-none max-w-[190px] font-medium cursor-pointer"
            >
              <option value="">Шаблоны ответов...</option>
              {templates.map(t => (
                <option key={t.key} value={t.key}>
                  {t.name}
                </option>
              ))}
            </select>
          )}

          {replyText && (
            <button
              type="button"
              onClick={() => onChangeReplyText('')}
              className="text-[11.5px] text-neutral-400 hover:text-rose-600 dark:hover:text-rose-400 font-medium px-1 cursor-pointer"
              title="Очистить текст"
            >
              Очистить
            </button>
          )}
        </div>
      </div>

      {/* Textarea */}
      <textarea
        value={replyText}
        onChange={e => onChangeReplyText(e.target.value)}
        placeholder={replyMode === 'reply' ? 'Напишите комментарий для пользователя...' : 'Скрытый комментарий (только для инженеров)...'}
        rows={3}
        className={`w-full px-3.5 py-2.5 text-[13.5px] rounded-lg border text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 focus:outline-none focus:ring-1 focus:ring-neutral-400 dark:focus:ring-neutral-600 transition-colors resize-none ${
          replyMode === 'internal'
            ? 'border-amber-300 dark:border-amber-800 bg-amber-50/20 dark:bg-amber-950/20'
            : 'border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-900'
        }`}
      />

      {/* Action Controls Bar */}
      <div className="flex items-center justify-between gap-2 pt-0.5 flex-wrap">
        <div className="flex items-center gap-2">
          {/* Other Statuses Menu */}
          <div className="relative" ref={actionsMenuRef}>
            <button
              type="button"
              onClick={onToggleActionsMenu}
              className="h-7 px-2.5 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-700 text-neutral-700 dark:text-neutral-300 border border-neutral-200 dark:border-neutral-700 rounded-md text-[11.5px] font-semibold transition-colors cursor-pointer flex items-center gap-1.5"
            >
              <span>Статус</span>
              <svg width="8" height="8" viewBox="0 0 10 10" fill="none" className="text-neutral-400">
                <path d="M2.5 3.5l2.5 2.5 2.5-2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>

            {isActionsMenuOpen && (
              <div className="absolute left-0 bottom-8 z-30 w-64 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-xl shadow-xl p-1.5 space-y-1.5 animate-in fade-in zoom-in-95 duration-100">
                {[35, 36, 37, 48].some(s => isStatusAllowed(s)) && (
                  <div>
                    <span className="text-[9.5px] uppercase font-bold text-neutral-400 dark:text-neutral-500 block px-2 mb-0.5">
                      Статусы ожидания
                    </span>
                    <div className="space-y-0.5">
                      {isStatusAllowed(35) && (
                        <button
                          type="button"
                          onClick={() => onSelectMenuStatus(35, 'Запрошена дополнительная информация у заявителя. Ожидаем ответа.', 5)}
                          className="w-full text-left px-2 py-1.5 rounded-md text-[12px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                        >
                          <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-amber-500" />
                          <span>Ожидание заявителя</span>
                        </button>
                      )}
                      {isStatusAllowed(36) && (
                        <button
                          type="button"
                          onClick={() => onSelectMenuStatus(36, 'Заявка переведена в ожидание поставки оборудования / ЗИП.', 5)}
                          className="w-full text-left px-2 py-1.5 rounded-md text-[12px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                        >
                          <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-amber-500" />
                          <span>Ожидание поставки / ЗИП</span>
                        </button>
                      )}
                      {isStatusAllowed(37) && (
                        <button
                          type="button"
                          onClick={() => onSelectMenuStatus(37, 'Заявка передана на исполнение сторонней организации / подрядчику.', 5)}
                          className="w-full text-left px-2 py-1.5 rounded-md text-[12px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                        >
                          <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-amber-500" />
                          <span>Ожидание подрядчика</span>
                        </button>
                      )}
                      {isStatusAllowed(48) && (
                        <button
                          type="button"
                          onClick={() => onSelectMenuStatus(48, 'Приносите системный блок / ноутбук в АБК 3, 112 каб. на аппаратную диагностику и обслуживание.', 10)}
                          className="w-full text-left px-2 py-1.5 rounded-md text-[12px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                        >
                          <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-amber-500" />
                          <span>Ожидание устройства (каб. 112)</span>
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {isStatusAllowed(26) && (
                  <div className="border-t border-neutral-100 dark:border-neutral-800 pt-1">
                    <span className="text-[9.5px] uppercase font-bold text-neutral-400 dark:text-neutral-500 block px-2 mb-0.5">
                      Перераспределение
                    </span>
                    <button
                      type="button"
                      onClick={() => onSelectMenuStatus(26, 'Заявка возвращена в статус Открыта для перераспределения.', 5)}
                      className="w-full text-left px-2 py-1.5 rounded-md text-[12px] hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium cursor-pointer flex items-center gap-2"
                    >
                      <span className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse bg-blue-500" />
                      <span>Вернуть в статус «Открыта»</span>
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          {isStatusAllowed(30) && (
            <button
              type="button"
              onClick={onCancelTicketClick}
              disabled={submitting || ticket.statusId === 30 || ticket.statusId === 29}
              className={`h-8.5 px-3 text-[12px] font-bold rounded-lg border transition-colors cursor-pointer whitespace-nowrap disabled:opacity-40 ${
                confirmingCancel
                  ? 'bg-rose-600 text-white border-rose-600 hover:bg-rose-700 animate-pulse'
                  : 'text-rose-700 dark:text-rose-400 border-rose-200 dark:border-rose-900/60 hover:bg-rose-50 dark:hover:bg-rose-950/40'
              }`}
              title={confirmingCancel ? 'Кликните еще раз для подтверждения отмены' : 'Отменить заявку'}
            >
              {confirmingCancel ? 'Подтвердить отмену?' : 'Отменить'}
            </button>
          )}

          {isStatusAllowed(27) && ticket.statusId !== 27 && (
            <button
              type="button"
              onClick={onTakeOwnership}
              disabled={submitting}
              className="h-8.5 px-3 text-[12px] font-semibold text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-lg transition-colors disabled:opacity-40 cursor-pointer whitespace-nowrap"
            >
              В работу
            </button>
          )}

          <button
            type="button"
            onClick={onExecuteMainAction}
            disabled={submitting || ticket.statusId === 29 || ticket.statusId === 30 || mainAction.statusId === 0 || !isStatusAllowed(mainAction.statusId)}
            className={`h-8.5 px-4 text-[12.5px] font-bold rounded-lg transition-all disabled:opacity-40 cursor-pointer flex items-center justify-center gap-1.5 shadow-xs whitespace-nowrap ${mainAction.buttonClass}`}
          >
            <span>{submitting ? 'Сохранение...' : mainAction.label}</span>
            <span className="text-[10.5px] opacity-70 font-mono font-normal">Ctrl+Enter</span>
          </button>
        </div>
      </div>
    </div>
  );
}
