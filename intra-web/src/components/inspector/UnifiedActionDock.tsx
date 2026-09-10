import React, { useState, useRef, useEffect } from 'react';
import {
  IconCheck,
  IconChevronDown,
  IconSparkles,
  IconLock,
  IconGlobe,
  IconRefresh,
  IconClose,
  IconClock,
} from '../Icons';

export interface TemplateItem {
  key: string;
  name: string;
  template?: string;
  minutes?: number;
  status_id?: number;
}

export interface UnifiedActionDockProps {
  replyText: string;
  setReplyText: (val: string) => void;
  replyMode: 'reply' | 'internal';
  setReplyMode: (mode: 'reply' | 'internal') => void;
  expenses: number;
  setExpenses: (val: number) => void;
  selectedTemplateKey: string;
  setSelectedTemplateKey: (key: string) => void;
  templates: TemplateItem[];
  selectedStatusOverride: number | null;
  setSelectedStatusOverride: (statusId: number | null) => void;
  primaryActionLabel: string;
  targetStatusId: number;
  targetStatusName: string;
  actionUnavailable: boolean;
  submitting: boolean;
  requiresComment: boolean;
  commentMissing: boolean;
  pendingCommand: any;
  onApplyDecision: () => Promise<void>;
  onCancelTicket: () => Promise<void>;
  onTakeTicket: () => Promise<void>;
  onReanalyze?: () => Promise<void>;
  reanalyzing?: boolean;
  pendingNewAiDraft: string | null;
  onApplyNewDraft: () => void;
  onDismissNewDraft: () => void;
  originalAiDraft?: string;
  isAiDraftApplied?: boolean;
  isAiDraftEdited?: boolean;
  recommendedStatusId?: number | null;
  recommendedStatusName?: string;
  recommendedExpenses?: number;
  onRestoreAiDraft?: (syncMetadata?: boolean) => void;
  onAppendAiDraft?: () => void;
  snippets?: Array<{ label: string; text: string }>;
  insertSnippet: (snippet: string) => void;
}

export default function UnifiedActionDock({
  replyText,
  setReplyText,
  replyMode,
  setReplyMode,
  expenses,
  setExpenses,
  selectedTemplateKey,
  setSelectedTemplateKey,
  templates,
  selectedStatusOverride,
  setSelectedStatusOverride,
  primaryActionLabel,
  targetStatusId,
  targetStatusName,
  actionUnavailable,
  submitting,
  requiresComment,
  commentMissing,
  pendingCommand,
  onApplyDecision,
  onCancelTicket,
  onTakeTicket,
  onReanalyze,
  reanalyzing = false,
  pendingNewAiDraft,
  onApplyNewDraft,
  onDismissNewDraft,
  originalAiDraft = '',
  isAiDraftApplied = false,
  isAiDraftEdited = false,
  recommendedStatusId = null,
  recommendedStatusName = 'В работе',
  recommendedExpenses = 10,
  onRestoreAiDraft,
  onAppendAiDraft,
  snippets = [],
  insertSnippet,
}: UnifiedActionDockProps) {
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isTemplatesOpen, setIsTemplatesOpen] = useState(false);
  const [isAiMenuOpen, setIsAiMenuOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const templateMenuRef = useRef<HTMLDivElement>(null);
  const aiMenuRef = useRef<HTMLDivElement>(null);

  // Сброс таймера подтверждения очистки
  useEffect(() => {
    if (!confirmClear) return;
    const timer = setTimeout(() => setConfirmClear(false), 3000);
    return () => clearTimeout(timer);
  }, [confirmClear]);

  const handleClearClick = () => {
    if (replyText.trim().length <= 20) {
      setReplyText('');
      setConfirmClear(false);
      return;
    }
    if (!confirmClear) {
      setConfirmClear(true);
      return;
    }
    setReplyText('');
    setConfirmClear(false);
  };

  // Закрытие выпадающих списков при клике снаружи и клавише Escape
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
      if (templateMenuRef.current && !templateMenuRef.current.contains(event.target as Node)) {
        setIsTemplatesOpen(false);
      }
      if (aiMenuRef.current && !aiMenuRef.current.contains(event.target as Node)) {
        setIsAiMenuOpen(false);
      }
    }

    function handleGlobalKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsDropdownOpen(false);
        setIsTemplatesOpen(false);
        setIsAiMenuOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleGlobalKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleGlobalKeyDown);
    };
  }, []);

  // Хоткей Ctrl+Enter / Cmd+Enter для отправки
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      if (!actionUnavailable && !submitting) {
        e.preventDefault();
        onApplyDecision();
      }
    }
  };

  // Безопасное применение шаблона без случайного затирания текста
  const handleSelectTemplate = (tmpl: TemplateItem) => {
    setSelectedTemplateKey(tmpl.key);
    setIsTemplatesOpen(false);

    if (tmpl.minutes) {
      setExpenses(tmpl.minutes);
    }
    if (tmpl.status_id) {
      setSelectedStatusOverride(tmpl.status_id);
    }

    if (tmpl.template) {
      if (!replyText.trim()) {
        setReplyText(tmpl.template);
      } else if (!replyText.includes(tmpl.template)) {
        // Добавляем к текущему тексту, если текст уже набран
        const sep = replyText.trim() ? '\n\n' : '';
        setReplyText(`${replyText.trim()}${sep}${tmpl.template}`);
      }
    }
  };

  // Обработчик кнопки «AI-ответ»
  const handleAiButtonClick = () => {
    // 1. Если ответа нейросети вообще нет: запустить анализ
    if (!originalAiDraft) {
      if (onReanalyze && !reanalyzing) {
        onReanalyze();
      }
      return;
    }

    // 2. Если поле ввода пустое: мгновенная подстановка оригинала
    if (!replyText.trim()) {
      if (onRestoreAiDraft) {
        onRestoreAiDraft(false);
      }
      return;
    }

    // 3. Если поле не пустое: открыть/закрыть контекстный поповер
    setIsAiMenuOpen((prev) => !prev);
  };

  const selectedTemplate = templates.find((t) => t.key === selectedTemplateKey);

  return (
    <div className="sticky bottom-0 z-20 border-t border-neutral-200/90 bg-white/95 p-3.5 backdrop-blur-md shadow-lg dark:border-neutral-800 dark:bg-neutral-900/95 space-y-2.5">
      {/* Конфликт черновика при повторном AI-анализе */}
      {pendingNewAiDraft && (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-purple-200 bg-purple-50 p-2.5 text-xs text-purple-900 dark:border-purple-900 dark:bg-purple-950/40 dark:text-purple-200 animate-in fade-in duration-150">
          <div className="flex items-center gap-2 min-w-0">
            <IconSparkles size={14} className="shrink-0 text-purple-600 dark:text-purple-400" />
            <span className="truncate">
              AI сформулировал новый вариант ответа. Обновить черновик?
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              onClick={onApplyNewDraft}
              className="rounded-md bg-purple-600 px-2 py-1 font-semibold text-white hover:bg-purple-500 transition-colors"
            >
              Применить
            </button>
            <button
              type="button"
              onClick={onDismissNewDraft}
              className="rounded-md border border-purple-200 bg-white px-2 py-1 text-purple-700 hover:bg-purple-100 transition-colors dark:border-purple-800 dark:bg-neutral-900 dark:text-purple-300"
            >
              Оставить мой
            </button>
          </div>
        </div>
      )}

      {/* Верхняя строка управления формой: Режим приватности, Шаблоны, Списание времени */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2 flex-wrap">
          {/* Режим приватности (Ответ заявителю / Служебный комментарий) */}
          <div className="inline-flex rounded-lg border border-neutral-200 bg-neutral-100 p-0.5 dark:border-neutral-700 dark:bg-neutral-800">
            <button
              type="button"
              onClick={() => setReplyMode('reply')}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 font-semibold transition-colors ${replyMode === 'reply'
                  ? 'bg-white text-neutral-900 shadow-2xs dark:bg-neutral-900 dark:text-neutral-100'
                  : 'text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200'
                }`}
              title="Ответ будет отправлен заявителю в IntraService"
            >
              <IconGlobe size={12} />
              <span>Ответ заявителю</span>
            </button>
            <button
              type="button"
              onClick={() => {
                if (targetStatusId === 29) return;
                setReplyMode('internal');
              }}
              disabled={targetStatusId === 29}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 font-semibold transition-colors ${
                replyMode === 'internal'
                  ? 'bg-amber-100 text-amber-900 shadow-2xs dark:bg-amber-950 dark:text-amber-200'
                  : targetStatusId === 29
                  ? 'opacity-40 cursor-not-allowed text-neutral-400'
                  : 'text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200'
              }`}
              title={
                targetStatusId === 29
                  ? 'Закрытие заявки (статус 29) запрещено со скрытым комментарием'
                  : 'Служебная заметка, видна только инженерам'
              }
            >
              <IconLock size={12} />
              <span>Скрытый комментарий</span>
            </button>
          </div>

          {/* Выпадающий список регламентных шаблонов */}
          {templates.length > 0 && (
            <div className="relative" ref={templateMenuRef}>
              <button
                type="button"
                onClick={() => setIsTemplatesOpen((prev) => !prev)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-2.5 py-1.5 font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 transition-colors"
              >
                <span>{selectedTemplate ? selectedTemplate.name : 'Шаблоны регламентов'}</span>
                <IconChevronDown size={12} className="text-neutral-400" />
              </button>

              {isTemplatesOpen && (
                <div className="absolute left-0 bottom-full mb-1 w-72 max-h-60 overflow-y-auto rounded-xl border border-neutral-200 bg-white p-1 shadow-xl dark:border-neutral-800 dark:bg-neutral-900 z-30 space-y-0.5 animate-in fade-in duration-100">
                  <div className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-neutral-400">
                    Регламентные сценарии
                  </div>
                  {templates.map((tmpl) => (
                    <button
                      key={tmpl.key}
                      type="button"
                      onClick={() => handleSelectTemplate(tmpl)}
                      className={`flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors ${selectedTemplateKey === tmpl.key
                          ? 'bg-blue-50 font-semibold text-blue-700 dark:bg-blue-950/60 dark:text-blue-300'
                          : 'text-neutral-700 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800'
                        }`}
                    >
                      <span className="truncate">{tmpl.name}</span>
                      {tmpl.minutes && (
                        <span className="shrink-0 font-mono text-[10.5px] text-neutral-400 ml-1">
                          {tmpl.minutes}м
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Кнопка вставки ответа нейросети с контекстным поповером */}
          <div className="relative" ref={aiMenuRef}>
            <button
              type="button"
              onClick={handleAiButtonClick}
              disabled={reanalyzing}
              aria-haspopup="menu"
              aria-expanded={isAiMenuOpen}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 font-medium text-xs transition-colors cursor-pointer ${
                isAiDraftApplied
                  ? 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300 dark:hover:bg-emerald-900/50'
                  : 'border-purple-200 bg-purple-50/70 text-purple-700 hover:bg-purple-100 dark:border-purple-900/60 dark:bg-purple-950/40 dark:text-purple-300 dark:hover:bg-purple-900/50'
              } disabled:opacity-50`}
              title={
                !originalAiDraft
                  ? 'Запустить AI-анализ заявки и сформировать ответ'
                  : isAiDraftApplied
                  ? 'Ответ нейросети уже подставлен (нажмите для меню управления)'
                  : 'Вставить сформированный ответ нейросети'
              }
            >
              {reanalyzing ? (
                <IconRefresh size={12} className="animate-spin text-purple-500" />
              ) : isAiDraftApplied ? (
                <IconCheck size={12} className="text-emerald-600 dark:text-emerald-400" />
              ) : (
                <IconSparkles size={12} className="text-purple-600 dark:text-purple-400" />
              )}
              <span>{reanalyzing ? 'Анализ...' : 'AI-ответ'}</span>
              {originalAiDraft ? (
                <IconChevronDown
                  size={11}
                  className={`text-purple-400 transition-transform ${isAiMenuOpen ? 'rotate-180' : ''}`}
                />
              ) : null}
            </button>

            {isAiMenuOpen && originalAiDraft && (
              <div
                role="menu"
                className="absolute left-0 bottom-full mb-1.5 w-80 rounded-xl border border-neutral-200 bg-white p-1.5 shadow-2xl dark:border-neutral-800 dark:bg-neutral-900 z-30 space-y-1 animate-in fade-in duration-100"
              >
                <div className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-neutral-400 border-b border-neutral-100 dark:border-neutral-800 flex items-center justify-between">
                  <span>Ответ нейросети</span>
                  {isAiDraftEdited && (
                    <span className="text-amber-600 dark:text-amber-400 font-semibold normal-case">
                      текст изменён
                    </span>
                  )}
                  {isAiDraftApplied && (
                    <span className="text-emerald-600 dark:text-emerald-400 font-semibold normal-case">
                      актуален
                    </span>
                  )}
                </div>

                {/* Действие 1: Восстановить оригинальный ответ */}
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setIsAiMenuOpen(false);
                    onRestoreAiDraft?.(false);
                  }}
                  className="flex w-full items-start gap-2 rounded-lg p-2 text-left text-xs transition-colors hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 cursor-pointer"
                >
                  <IconRefresh size={13} className="text-blue-500 shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <div className="font-semibold text-neutral-900 dark:text-neutral-100">
                      Восстановить оригинальный ответ
                    </div>
                    <div className="text-[11px] text-neutral-400 leading-tight">
                      Заменит набранный текст, сохранив текущий статус и трудозатраты
                    </div>
                  </div>
                </button>

                {/* Действие 2: Восстановить всё (текст + статус + время) */}
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setIsAiMenuOpen(false);
                    onRestoreAiDraft?.(true);
                  }}
                  className="flex w-full items-start gap-2 rounded-lg p-2 text-left text-xs transition-colors hover:bg-purple-50 dark:hover:bg-purple-950/50 text-purple-900 dark:text-purple-200 cursor-pointer"
                >
                  <IconSparkles size={13} className="text-purple-600 dark:text-purple-400 shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <div className="font-semibold">
                      Восстановить всё (текст + статус + время)
                    </div>
                    <div className="text-[11px] text-purple-700/80 dark:text-purple-300/80 leading-tight">
                      Синхронизирует статус ({recommendedStatusName}) и трудозатраты ({recommendedExpenses}м)
                    </div>
                  </div>
                </button>

                {/* Действие 3: Добавить в конец */}
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setIsAiMenuOpen(false);
                    onAppendAiDraft?.();
                  }}
                  className="flex w-full items-start gap-2 rounded-lg p-2 text-left text-xs transition-colors hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 cursor-pointer"
                >
                  <IconChevronDown size={13} className="text-neutral-400 shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <div className="font-semibold text-neutral-900 dark:text-neutral-100">
                      Добавить в конец текста
                    </div>
                    <div className="text-[11px] text-neutral-400 leading-tight">
                      Допишет ответ нейросети после вашего текста с новой строки
                    </div>
                  </div>
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Списание времени (минуты) */}
        <div className="flex items-center gap-1.5 ml-auto">
          <IconClock size={12} className="text-neutral-400" />
          <span className="text-neutral-500 dark:text-neutral-400">Трудозатраты:</span>
          <input
            type="number"
            min={0}
            step={5}
            value={expenses}
            onChange={(e) => setExpenses(Math.max(0, parseInt(e.target.value, 10) || 0))}
            className="w-14 rounded-md border border-neutral-200 bg-white px-1.5 py-0.5 text-center font-mono text-xs font-bold text-neutral-900 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100"
          />
          <span className="text-neutral-400 text-[11px]">мин</span>
        </div>
      </div>

      {/* Поле ввода комментария */}
      <div className="relative">
        <textarea
          rows={4}
          value={replyText}
          onChange={(e) => setReplyText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Текст ответа или решения... (Ctrl+Enter для быстрой отправки)"
          className={`w-full min-h-[115px] max-h-[280px] resize-y rounded-xl border p-3 text-xs leading-relaxed text-neutral-900 outline-none transition-colors placeholder:text-neutral-400 focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-neutral-100 ${commentMissing
              ? 'border-rose-400 bg-rose-50/20 dark:border-rose-800 dark:bg-rose-950/20'
              : replyMode === 'internal'
                ? 'border-amber-300 bg-amber-50/40 dark:border-amber-800/80 dark:bg-amber-950/20'
                : 'border-neutral-200 bg-neutral-50/50 dark:border-neutral-700 dark:bg-neutral-950/60'
            }`}
        />

        {replyText && (
          <div className="absolute top-2.5 right-2.5 z-10">
            {confirmClear ? (
              <button
                type="button"
                onClick={handleClearClick}
                className="inline-flex items-center gap-1 rounded bg-rose-600 px-2 py-0.5 text-[10.5px] font-bold text-white shadow-xs hover:bg-rose-500 transition-all animate-in fade-in cursor-pointer"
                title="Подтвердить удаление всего набранного текста"
              >
                <span>Очистить?</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={handleClearClick}
                className="rounded p-1 text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors cursor-pointer"
                title="Очистить текст (при длинном тексте потребуется подтверждение)"
              >
                <IconClose size={12} />
              </button>
            )}
          </div>
        )}
      </div>

      {/* Подсказка валидации, если комментарий обязателен для закрытия */}
      {commentMissing && (
        <div className="text-[11px] font-medium text-rose-600 dark:text-rose-400">
          Для закрытия, отмены, запроса уточнения или ожидания устройства необходимо заполнить комментарий.
        </div>
      )}

      {/* Подсказка валидации: запрет закрытия со скрытым комментарием */}
      {targetStatusId === 29 && replyMode === 'internal' && (
        <div className="text-[11px] font-medium text-rose-600 dark:text-rose-400">
          Закрытие заявки (статус «Выполнена») запрещено со скрытым комментарием. Переключите режим на «Ответ заявителю».
        </div>
      )}

      {/* Быстрые сниппеты (если доступны) */}
      {snippets.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
          <span className="text-[10.5px] font-medium text-neutral-400">Сниппеты:</span>
          {snippets.map((snip, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => insertSnippet(snip.text)}
              className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-neutral-50 px-2 py-0.5 text-[11px] font-medium text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-750 transition-colors"
            >
              <IconSparkles size={10} className="text-blue-500" />
              <span>{snip.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Строка главных действий: Основная кнопка + Split меню альтернатив */}
      <div className="flex items-center justify-between gap-2 pt-1 border-t border-neutral-100 dark:border-neutral-800">
        <div className="text-[11px] text-neutral-500 dark:text-neutral-400">
          Целевой статус:{' '}
          <span className="font-semibold text-neutral-800 dark:text-neutral-200">
            {targetStatusName}
          </span>
        </div>

        <div className="flex items-center gap-1" ref={dropdownRef}>
          {/* Главная кнопка действия */}
          <button
            type="button"
            onClick={onApplyDecision}
            disabled={actionUnavailable || submitting}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-l-xl bg-blue-600 px-4 text-xs font-bold text-white shadow-xs transition-colors hover:bg-blue-500 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <>
                <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                <span>Применение...</span>
              </>
            ) : (
              <>
                <IconCheck size={14} />
                <span>{primaryActionLabel}</span>
              </>
            )}
          </button>

          {/* Кнопка открытия альтернативных действий (Split dropdown) */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setIsDropdownOpen((prev) => !prev)}
              disabled={submitting}
              className="inline-flex min-h-9 w-8 items-center justify-center rounded-r-xl border-l border-blue-700 bg-blue-600 text-white transition-colors hover:bg-blue-500 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50"
              title="Другие действия по заявке"
            >
              <IconChevronDown size={14} className={`transition-transform ${isDropdownOpen ? 'rotate-180' : ''}`} />
            </button>

            {isDropdownOpen && (
              <div className="absolute right-0 bottom-full mb-1 w-56 rounded-xl border border-neutral-200 bg-white p-1 shadow-2xl dark:border-neutral-800 dark:bg-neutral-900 z-30 space-y-0.5 animate-in fade-in duration-100">
                <button
                  type="button"
                  onClick={() => {
                    setIsDropdownOpen(false);
                    onTakeTicket();
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800"
                >
                  <span>Взять в работу (В работе)</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsDropdownOpen(false);
                    setSelectedStatusOverride(29);
                    if (replyMode === 'internal') {
                      setReplyMode('reply');
                    }
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium text-emerald-700 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-950/40"
                >
                  <span>Отметить выполненной</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsDropdownOpen(false);
                    setSelectedStatusOverride(35);
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium text-amber-700 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950/40"
                >
                  <span>Запросить уточнение (Статус 35)</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsDropdownOpen(false);
                    setSelectedStatusOverride(48);
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium text-amber-700 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950/40"
                >
                  <span>Ожидание устройства (каб. 112)</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsDropdownOpen(false);
                    onCancelTicket();
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium text-rose-700 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40"
                >
                  <span>Отменить заявку</span>
                </button>

                {onReanalyze && (
                  <div className="border-t border-neutral-100 pt-1 dark:border-neutral-800">
                    <button
                      type="button"
                      onClick={() => {
                        setIsDropdownOpen(false);
                        onReanalyze();
                      }}
                      disabled={reanalyzing}
                      className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium text-purple-700 hover:bg-purple-50 dark:text-purple-400 dark:hover:bg-purple-950/40"
                    >
                      <IconRefresh size={12} className={reanalyzing ? 'animate-spin' : ''} />
                      <span>Повторный AI-анализ</span>
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
