import React from 'react';
import type { DecisionEnvelope, DecisionFactSummary } from '../../lib/types';
import { IconCheck, IconAlertCircle, IconPencil, IconDatabase } from '../Icons';

interface FactBagSectionProps {
  envelope: DecisionEnvelope | null | undefined;
  onOpenOverride: () => void;
}

const FRIENDLY_FACT_NAMES: Record<string, string> = {
  pc_name: 'Рабочая станция (ПК)',
  printer_address: 'Сетевой принтер (IP / модель)',
  printer_ip: 'IP-адрес принтера',
  printer_name: 'Имя принтера',
  login: 'Учётная запись (логин)',
  account_name: 'Учётная запись AD',
  target_service: 'Целевой сервис каталога',
  file_path: 'Сетевой путь к файлу / шаре',
  service_id: 'ID сервиса',
};

const STATE_CONFIG: Record<
  DecisionFactSummary['state'],
  { label: string; bgClass: string; textClass: string; borderClass: string }
> = {
  valid: {
    label: 'Подтверждён',
    bgClass: 'bg-emerald-50 dark:bg-emerald-950/40',
    textClass: 'text-emerald-700 dark:text-emerald-300',
    borderClass: 'border-emerald-200 dark:border-emerald-800',
  },
  missing: {
    label: 'Не найден',
    bgClass: 'bg-amber-50 dark:bg-amber-950/40',
    textClass: 'text-amber-800 dark:text-amber-300',
    borderClass: 'border-amber-200 dark:border-amber-800',
  },
  invalid: {
    label: 'Невалиден',
    bgClass: 'bg-rose-50 dark:bg-rose-950/40',
    textClass: 'text-rose-700 dark:text-rose-300',
    borderClass: 'border-rose-200 dark:border-rose-800',
  },
  ambiguous: {
    label: 'Неоднозначен',
    bgClass: 'bg-purple-50 dark:bg-purple-950/40',
    textClass: 'text-purple-700 dark:text-purple-300',
    borderClass: 'border-purple-200 dark:border-purple-800',
  },
  conflicting: {
    label: 'Конфликт',
    bgClass: 'bg-rose-50 dark:bg-rose-950/40',
    textClass: 'text-rose-700 dark:text-rose-300',
    borderClass: 'border-rose-200 dark:border-rose-800',
  },
  stale: {
    label: 'Устарел',
    bgClass: 'bg-neutral-100 dark:bg-neutral-800',
    textClass: 'text-neutral-600 dark:text-neutral-400',
    borderClass: 'border-neutral-200 dark:border-neutral-700',
  },
};

const SOURCE_LABELS: Record<string, { label: string; isOperator?: boolean }> = {
  operator: { label: 'Оператор (SSOT)', isOperator: true },
  structured_field: { label: 'Поле заявки' },
  directory: { label: 'Active Directory' },
  diagnostic: { label: 'Сеть / WMI' },
  comment: { label: 'Переписка' },
  parser: { label: 'Парсер текста' },
  llm: { label: 'LLM Sensor' },
  unknown: { label: 'Автоанализ' },
};

export default function FactBagSection({ envelope, onOpenOverride }: FactBagSectionProps) {
  const facts = envelope?.facts_summary || {};
  const entries = Object.entries(facts);

  if (!envelope || entries.length === 0) {
    return (
      <div className="rounded-xl border border-neutral-200 p-4 text-center dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-950/20 space-y-2">
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Факты для текущего сценария ещё не извлечены или заявка не проанализирована.
        </p>
        <button
          type="button"
          onClick={onOpenOverride}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white shadow-xs hover:bg-blue-500"
        >
          <IconPencil size={13} />
          <span>Задать факты вручную</span>
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-xl border border-neutral-200 bg-neutral-50/50 p-3.5 dark:border-neutral-800 dark:bg-neutral-950/30">
      {/* Верхний бар FactBag */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400">
            <IconDatabase size={13} />
          </span>
          <span className="text-xs font-bold text-neutral-900 dark:text-neutral-100">
            Доказательная база сценария (FactBag)
          </span>
          <span className="text-[10.5px] font-mono px-1.5 py-0.2 rounded bg-neutral-200 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300">
            Ревизия #{envelope.facts_revision}
          </span>
        </div>

        <button
          type="button"
          onClick={onOpenOverride}
          className="inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50/80 px-2.5 text-[11.5px] font-semibold text-blue-700 outline-none transition-colors hover:bg-blue-100 focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-blue-900 dark:bg-blue-950/50 dark:text-blue-300 dark:hover:bg-blue-900/60"
        >
          <IconPencil size={12} />
          <span>Скорректировать факты</span>
        </button>
      </div>

      {/* Сетка фактов */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {entries.map(([key, fact]) => {
          const friendlyName = FRIENDLY_FACT_NAMES[key] || key;
          const stateCfg = STATE_CONFIG[fact.state] || STATE_CONFIG.missing;
          const srcInfo = SOURCE_LABELS[fact.source || 'unknown'] || { label: fact.source || 'Не указан' };
          const hasVal = fact.value !== null && fact.value !== undefined && String(fact.value).trim() !== '';

          return (
            <div
              key={key}
              className={`flex flex-col justify-between p-2.5 rounded-xl border bg-white dark:bg-neutral-900 shadow-2xs transition-shadow ${
                srcInfo.isOperator
                  ? 'border-blue-300 dark:border-blue-800 ring-1 ring-blue-400/20'
                  : 'border-neutral-200 dark:border-neutral-800'
              }`}
            >
              <div className="space-y-1">
                {/* Шапка факта: имя и бейдж статуса */}
                <div className="flex items-center justify-between gap-1.5">
                  <span className="text-[11px] font-semibold text-neutral-700 dark:text-neutral-300 truncate" title={friendlyName}>
                    {friendlyName}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border shrink-0 ${stateCfg.bgClass} ${stateCfg.textClass} ${stateCfg.borderClass}`}
                  >
                    {fact.state === 'valid' && <IconCheck size={10} />}
                    {fact.state !== 'valid' && <IconAlertCircle size={10} />}
                    <span>{stateCfg.label}</span>
                  </span>
                </div>

                {/* Значение факта */}
                <div className="pt-0.5">
                  {hasVal ? (
                    <span className="font-mono text-xs font-bold text-neutral-900 dark:text-neutral-100 break-all select-all">
                      {String(fact.value)}
                    </span>
                  ) : (
                    <span className="text-xs text-neutral-400 italic">
                      Не указано в заявке
                    </span>
                  )}
                </div>
              </div>

              {/* Подвал факта: источник и ссылка */}
              <div className="pt-2 mt-1 border-t border-neutral-100 dark:border-neutral-800/60 flex items-center justify-between gap-1 text-[10.5px]">
                <span
                  className={`px-1.5 py-0.5 rounded font-medium truncate ${
                    srcInfo.isOperator
                      ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-200 font-bold'
                      : 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400'
                  }`}
                  title={fact.source_ref || srcInfo.label}
                >
                  {srcInfo.label}
                </span>

                <button
                  type="button"
                  onClick={onOpenOverride}
                  className="text-neutral-400 hover:text-blue-600 dark:hover:text-blue-400 text-[10.5px] font-medium transition-colors"
                  title="Изменить значение"
                >
                  изменить
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
