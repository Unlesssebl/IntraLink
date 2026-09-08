import React, { useState, useEffect } from 'react';
import type { DecisionEnvelope } from '../../lib/types';
import { IconAlertCircle, IconCheck, IconPencil } from '../Icons';

interface FactOverrideModalProps {
  isOpen: boolean;
  onClose: () => void;
  envelope: DecisionEnvelope | null | undefined;
  onSubmit: (facts: Record<string, any>) => Promise<boolean>;
  isSubmitting: boolean;
}

export default function FactOverrideModal({
  isOpen,
  onClose,
  envelope,
  onSubmit,
  isSubmitting,
}: FactOverrideModalProps) {
  const currentFacts = envelope?.facts_summary || {};

  const [pcName, setPcName] = useState('');
  const [printerAddress, setPrinterAddress] = useState('');
  const [login, setLogin] = useState('');
  const [customKey, setCustomKey] = useState('');
  const [customValue, setCustomValue] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);

  // Предзаполнение полей текущими значениями из envelope
  useEffect(() => {
    if (isOpen) {
      setPcName(String(currentFacts.pc_name?.value || ''));
      setPrinterAddress(String(currentFacts.printer_address?.value || currentFacts.printer_ip?.value || ''));
      setLogin(String(currentFacts.login?.value || currentFacts.account_name?.value || ''));
      setCustomKey('');
      setCustomValue('');
      setValidationError(null);
    }
  }, [isOpen, envelope]);

  // Закрытие по клавише Escape
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);

    const facts: Record<string, any> = {};

    if (pcName.trim()) {
      const cleanPc = pcName.trim();
      // Валидация сетевого имени компьютера
      if (!/^[a-zA-Z0-9_\-\.]{2,40}$/.test(cleanPc)) {
        setValidationError('Сетевое имя ПК может содержать только латиницу, цифры, дефис и подчеркивание.');
        return;
      }
      facts.pc_name = cleanPc;
    } else if (currentFacts.pc_name) {
      // Явный сброс в null, если оператор стёр значение
      facts.pc_name = null;
    }

    if (printerAddress.trim()) {
      facts.printer_address = printerAddress.trim();
    } else if (currentFacts.printer_address || currentFacts.printer_ip) {
      facts.printer_address = null;
    }

    if (login.trim()) {
      facts.login = login.trim();
    } else if (currentFacts.login || currentFacts.account_name) {
      facts.login = null;
    }

    if (customKey.trim()) {
      facts[customKey.trim()] = customValue.trim() || null;
    }

    if (Object.keys(facts).length === 0) {
      setValidationError('Укажите хотя бы один факт для изменения или сброса.');
      return;
    }

    const success = await onSubmit(facts);
    if (success) {
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="override-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-2xl dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 space-y-4">
        {/* Заголовок */}
        <div className="flex items-start justify-between gap-3 border-b border-neutral-100 pb-3 dark:border-neutral-800">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400">
                <IconPencil size={15} />
              </span>
              <h3 id="override-modal-title" className="text-sm font-bold text-neutral-900 dark:text-neutral-100">
                Корректировка фактов сценария (Fact-Override)
              </h3>
            </div>
            <p className="text-[11.5px] leading-relaxed text-neutral-500 dark:text-neutral-400">
              Ввод оператора имеет наивысший приоритет (SSOT). Сценарий немедленно пересчитается с новыми доказательствами.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-lg leading-none p-1"
            aria-label="Закрыть"
          >
            ✕
          </button>
        </div>

        {/* Ошибка валидации */}
        {validationError && (
          <div className="flex items-center gap-2 rounded-xl bg-rose-50 p-2.5 text-xs text-rose-800 dark:bg-rose-950/40 dark:text-rose-300 border border-rose-200 dark:border-rose-900">
            <IconAlertCircle size={14} className="shrink-0 text-rose-600" />
            <span>{validationError}</span>
          </div>
        )}

        {/* Форма */}
        <form onSubmit={handleSubmit} className="space-y-3.5">
          {/* Рабочая станция */}
          <div className="space-y-1">
            <label className="flex items-center justify-between text-xs font-semibold text-neutral-700 dark:text-neutral-300">
              <span>Имя рабочей станции (ПК)</span>
              {currentFacts.pc_name && (
                <span className="text-[10.5px] font-normal text-neutral-400">
                  Текущий: <strong className="font-mono text-neutral-600 dark:text-neutral-300">{String(currentFacts.pc_name.value ?? 'нет')}</strong>
                </span>
              )}
            </label>
            <input
              type="text"
              value={pcName}
              onChange={(e) => setPcName(e.target.value)}
              placeholder="Например: WS-0123, WKS124"
              className="w-full rounded-xl border border-neutral-200 bg-neutral-50/50 px-3 py-2 font-mono text-xs text-neutral-900 placeholder-neutral-400 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-800 dark:bg-neutral-950/40 dark:text-neutral-100"
            />
            <span className="text-[10.5px] text-neutral-400">Нормализуется автоматически (WS-0123 → WKS0123).</span>
          </div>

          {/* Принтер / IP адрес */}
          <div className="space-y-1">
            <label className="flex items-center justify-between text-xs font-semibold text-neutral-700 dark:text-neutral-300">
              <span>Сетевой принтер (IP или модель)</span>
              {(currentFacts.printer_address || currentFacts.printer_ip) && (
                <span className="text-[10.5px] font-normal text-neutral-400">
                  Текущий: <strong className="font-mono text-neutral-600 dark:text-neutral-300">{String((currentFacts.printer_address || currentFacts.printer_ip)?.value ?? 'нет')}</strong>
                </span>
              )}
            </label>
            <input
              type="text"
              value={printerAddress}
              onChange={(e) => setPrinterAddress(e.target.value)}
              placeholder="Например: 10.20.30.40 или HP LaserJet Pro M404"
              className="w-full rounded-xl border border-neutral-200 bg-neutral-50/50 px-3 py-2 font-mono text-xs text-neutral-900 placeholder-neutral-400 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-800 dark:bg-neutral-950/40 dark:text-neutral-100"
            />
          </div>

          {/* Логин / Учетная запись */}
          <div className="space-y-1">
            <label className="flex items-center justify-between text-xs font-semibold text-neutral-700 dark:text-neutral-300">
              <span>Учётная запись (логин)</span>
              {(currentFacts.login || currentFacts.account_name) && (
                <span className="text-[10.5px] font-normal text-neutral-400">
                  Текущий: <strong className="font-mono text-neutral-600 dark:text-neutral-300">{String((currentFacts.login || currentFacts.account_name)?.value ?? 'нет')}</strong>
                </span>
              )}
            </label>
            <input
              type="text"
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              placeholder="Например: ivanov.i"
              className="w-full rounded-xl border border-neutral-200 bg-neutral-50/50 px-3 py-2 font-mono text-xs text-neutral-900 placeholder-neutral-400 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-800 dark:bg-neutral-950/40 dark:text-neutral-100"
            />
          </div>

          {/* Произвольный факт */}
          <div className="border-t border-neutral-100 pt-2 dark:border-neutral-800 space-y-1.5">
            <div className="text-[11px] font-semibold text-neutral-600 dark:text-neutral-400">
              Дополнительный факт (опционально)
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input
                type="text"
                value={customKey}
                onChange={(e) => setCustomKey(e.target.value)}
                placeholder="Ключ (например: room)"
                className="rounded-xl border border-neutral-200 bg-neutral-50/50 px-2.5 py-1.5 font-mono text-xs text-neutral-900 placeholder-neutral-400 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-800 dark:bg-neutral-950/40 dark:text-neutral-100"
              />
              <input
                type="text"
                value={customValue}
                onChange={(e) => setCustomValue(e.target.value)}
                placeholder="Значение (например: 204)"
                className="rounded-xl border border-neutral-200 bg-neutral-50/50 px-2.5 py-1.5 font-mono text-xs text-neutral-900 placeholder-neutral-400 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-neutral-800 dark:bg-neutral-950/40 dark:text-neutral-100"
              />
            </div>
          </div>

          {/* Кнопки действий */}
          <div className="flex items-center justify-end gap-2 pt-2 border-t border-neutral-100 dark:border-neutral-800">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="min-h-9 px-3.5 rounded-xl border border-neutral-200 text-xs font-semibold text-neutral-700 hover:bg-neutral-50 outline-none transition-colors dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              Отмена
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="flex min-h-9 items-center gap-1.5 rounded-xl bg-blue-600 px-4 text-xs font-bold text-white shadow-sm outline-none transition-colors hover:bg-blue-500 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                  </svg>
                  <span>Сохранение...</span>
                </>
              ) : (
                <>
                  <IconCheck size={14} />
                  <span>Применить и пересчитать</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
