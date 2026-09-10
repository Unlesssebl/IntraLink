import React from 'react';
import type { ExecutionPlan, PlanStep, StepStatus } from '../../lib/types';
import {
  IconCheck,
  IconAlertCircle,
  IconClock,
  IconAlertTriangle,
  IconSparkles,
} from '../Icons';

interface DiagnosticPlanViewProps {
  plan: ExecutionPlan;
  className?: string;
}

const statusConfig: Record<
  StepStatus,
  { label: string; bgClass: string; textClass: string; borderClass: string; icon: 'check' | 'clock' | 'alert' | 'none' }
> = {
  completed: {
    label: 'Выполнен',
    bgClass: 'bg-emerald-50 dark:bg-emerald-950/40',
    textClass: 'text-emerald-700 dark:text-emerald-300',
    borderClass: 'border-emerald-200 dark:border-emerald-800',
    icon: 'check',
  },
  ready: {
    label: 'Готов',
    bgClass: 'bg-blue-50 dark:bg-blue-950/40',
    textClass: 'text-blue-700 dark:text-blue-300',
    borderClass: 'border-blue-200 dark:border-blue-800',
    icon: 'none',
  },
  running: {
    label: 'Выполняется',
    bgClass: 'bg-blue-50 dark:bg-blue-950/40',
    textClass: 'text-blue-700 dark:text-blue-300',
    borderClass: 'border-blue-300 dark:border-blue-700',
    icon: 'clock',
  },
  waiting_input: {
    label: 'Ожидает данных',
    bgClass: 'bg-amber-50 dark:bg-amber-950/40',
    textClass: 'text-amber-800 dark:text-amber-300',
    borderClass: 'border-amber-200 dark:border-amber-800',
    icon: 'clock',
  },
  failed: {
    label: 'Ошибка',
    bgClass: 'bg-rose-50 dark:bg-rose-950/40',
    textClass: 'text-rose-700 dark:text-rose-300',
    borderClass: 'border-rose-200 dark:border-rose-800',
    icon: 'alert',
  },
  unsupported: {
    label: 'Не поддерживается',
    bgClass: 'bg-purple-50 dark:bg-purple-950/40',
    textClass: 'text-purple-700 dark:text-purple-300',
    borderClass: 'border-purple-200 dark:border-purple-800',
    icon: 'alert',
  },
  skipped: {
    label: 'Пропущен',
    bgClass: 'bg-neutral-100 dark:bg-neutral-800',
    textClass: 'text-neutral-600 dark:text-neutral-400',
    borderClass: 'border-neutral-200 dark:border-neutral-700',
    icon: 'none',
  },
  not_started: {
    label: 'Не начат',
    bgClass: 'bg-neutral-50 dark:bg-neutral-850',
    textClass: 'text-neutral-500 dark:text-neutral-400',
    borderClass: 'border-neutral-200 dark:border-neutral-700',
    icon: 'none',
  },
};

const phaseConfig: Record<string, { label: string; textClass: string; bgClass: string }> = {
  collecting: { label: 'Сбор данных', textClass: 'text-amber-700 dark:text-amber-300', bgClass: 'bg-amber-50 dark:bg-amber-950/50 border-amber-200' },
  ready: { label: 'Готов к действию', textClass: 'text-blue-700 dark:text-blue-300', bgClass: 'bg-blue-50 dark:bg-blue-950/50 border-blue-200' },
  dispatched: { label: 'Отправлен', textClass: 'text-sky-700 dark:text-sky-300', bgClass: 'bg-sky-50 dark:bg-sky-950/50 border-sky-200' },
  running: { label: 'Выполняется', textClass: 'text-blue-700 dark:text-blue-300', bgClass: 'bg-blue-50 dark:bg-blue-950/50 border-blue-200' },
  verifying: { label: 'Верификация', textClass: 'text-purple-700 dark:text-purple-300', bgClass: 'bg-purple-50 dark:bg-purple-950/50 border-purple-200' },
  completed: { label: 'План завершён', textClass: 'text-emerald-700 dark:text-emerald-300', bgClass: 'bg-emerald-50 dark:bg-emerald-950/50 border-emerald-200' },
  blocked: { label: 'Заблокирован', textClass: 'text-rose-700 dark:text-rose-300', bgClass: 'bg-rose-50 dark:bg-rose-950/50 border-rose-200' },
};

const executorConfig: Record<string, { label: string; badgeClass: string }> = {
  windows: {
    label: 'Агент Windows',
    badgeClass: 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-800',
  },
  backend: {
    label: 'Бэкенд',
    badgeClass: 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-950/40 dark:text-indigo-300 dark:border-indigo-800',
  },
  engineer: {
    label: 'Инженер',
    badgeClass: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800',
  },
};

export default function DiagnosticPlanView({ plan, className = '' }: DiagnosticPlanViewProps) {
  if (!plan || !plan.steps || plan.steps.length === 0) {
    return null;
  }

  const phase = phaseConfig[plan.phase] || {
    label: plan.phase,
    textClass: 'text-neutral-700 dark:text-neutral-300',
    bgClass: 'bg-neutral-100 dark:bg-neutral-800 border-neutral-200',
  };

  const isExpired = plan.expires_at ? new Date(plan.expires_at).getTime() < Date.now() : false;
  const completedCount = plan.steps.filter((s) => s.status === 'completed').length;
  const totalCount = plan.steps.length;

  return (
    <div
      className={`rounded-xl border border-neutral-200/90 bg-neutral-50/50 p-3.5 dark:border-neutral-800 dark:bg-neutral-950/30 space-y-3 ${className}`}
    >
      {/* Шапка плана: Заголовок, статус фазы, прогресс */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 border-b border-neutral-200/70 dark:border-neutral-800">
        <div className="flex items-center gap-2">
          <IconSparkles size={14} className="text-blue-600 dark:text-blue-400 shrink-0" />
          <span className="text-xs font-bold text-neutral-900 dark:text-neutral-100">
            {plan.title || 'Диагностический план'}
          </span>
          <span className="font-mono text-[10.5px] font-semibold text-neutral-400 dark:text-neutral-500">
            ({completedCount}/{totalCount})
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Фаза плана */}
          <span
            className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold ${phase.bgClass} ${phase.textClass}`}
          >
            {phase.label}
          </span>

          {/* Индикатор актуальности TTL */}
          {plan.expires_at && (
            <span
              className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium ${
                isExpired
                  ? 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-800'
                  : 'bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700'
              }`}
              title={isExpired ? 'Срок действия плана и собранных фактов истек' : `Действителен до ${new Date(plan.expires_at).toLocaleTimeString()}`}
            >
              <IconClock size={10} />
              <span>{isExpired ? 'План устарел' : `до ${new Date(plan.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`}</span>
            </span>
          )}
        </div>
      </div>

      {/* Чеклист шагов */}
      <div className="space-y-2">
        {plan.steps.map((step: PlanStep, idx: number) => {
          const st = statusConfig[step.status] || statusConfig.not_started;
          const exec = executorConfig[step.executor || 'engineer'] || executorConfig.engineer;

          return (
            <div
              key={step.id || idx}
              className={`rounded-lg border p-2.5 transition-colors ${st.bgClass} ${st.borderClass} space-y-1.5`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-start gap-2 min-w-0">
                  {/* Иконка статуса */}
                  <span className="mt-0.5 shrink-0">
                    {st.icon === 'check' ? (
                      <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-600 text-white shadow-2xs">
                        <IconCheck size={10} />
                      </span>
                    ) : st.icon === 'clock' ? (
                      <span className="flex h-4 w-4 items-center justify-center rounded-full bg-blue-600 text-white animate-spin">
                        <IconClock size={10} />
                      </span>
                    ) : st.icon === 'alert' ? (
                      <span className="flex h-4 w-4 items-center justify-center rounded-full bg-rose-600 text-white shadow-2xs">
                        <IconAlertTriangle size={10} />
                      </span>
                    ) : (
                      <span className="flex h-4 w-4 items-center justify-center rounded-full border border-neutral-300 dark:border-neutral-600 text-[10px] font-mono text-neutral-400">
                        {idx + 1}
                      </span>
                    )}
                  </span>

                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-xs font-bold text-neutral-900 dark:text-neutral-100">
                        {step.title}
                      </span>

                      {/* Метка обязательности для закрытия */}
                      {step.required_for_resolution && (
                        <span className="inline-flex items-center rounded px-1.5 py-0.2 text-[9.5px] font-bold uppercase tracking-wider text-amber-800 bg-amber-100/80 border border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800">
                          Обязателен для закрытия
                        </span>
                      )}
                    </div>

                    {/* Описание шага */}
                    {step.description && (
                      <p className="text-[11px] text-neutral-600 dark:text-neutral-400 mt-0.5 leading-relaxed">
                        {step.description}
                      </p>
                    )}
                  </div>
                </div>

                {/* Бейджи исполнителя и статуса */}
                <div className="flex items-center gap-1.5 shrink-0">
                  <span
                    className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-semibold ${exec.badgeClass}`}
                  >
                    {exec.label}
                  </span>
                  <span
                    className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-bold ${st.bgClass} ${st.textClass} ${st.borderClass}`}
                  >
                    {st.label}
                  </span>
                </div>
              </div>

              {/* Ошибка или причина неподдерживаемости */}
              {(step.error_message || step.unsupported_reason) && (
                <div className="rounded-md border border-rose-200 bg-white p-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-neutral-900 dark:text-rose-300 flex items-start gap-1.5">
                  <IconAlertCircle size={12} className="shrink-0 mt-0.5 text-rose-600" />
                  <span className="break-words font-medium">
                    {step.error_message || step.unsupported_reason}
                  </span>
                </div>
              )}

              {/* Результат шага */}
              {step.result_summary && !step.error_message && (
                <div className="text-[11px] text-neutral-700 dark:text-neutral-300 pl-6 font-mono">
                  {step.result_summary}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
