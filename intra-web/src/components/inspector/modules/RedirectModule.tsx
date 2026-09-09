import type { InspectorModuleProps } from './InspectorModuleProps';
import {
  IconRedirect,
  IconAlertTriangle,
  IconArrowRight,
  IconCheck,
} from '../../Icons';

export default function RedirectModule({
  ticket,
  details,
  facts,
  onToast,
  onExecuteAction,
}: InspectorModuleProps) {
  const currentService = ticket.serviceName || 'Текущий сервис';
  const targetService = facts.target_service_name || details?.decision_envelope?.rule?.target_service || 'Целевой сервис каталога';
  const isDowntime = Boolean(facts.is_downtime || facts.production_impact);
  const reason = facts.redirect_reason || 'Тематика обращения относится к компетенции другой группы поддержки.';

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-neutral-200/80 bg-neutral-50/50 p-3.5 dark:border-neutral-800 dark:bg-neutral-950/40 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400">
              <IconRedirect size={13} />
            </span>
            <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-700 dark:text-neutral-300">
              Маршрутизация & Редирект каталога
            </span>
          </div>

          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300">
            Перенаправление
          </span>
        </div>

        {/* Downtime alert if critical */}
        {isDowntime && (
          <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50/90 p-2.5 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
            <IconAlertTriangle size={15} className="mt-0.5 shrink-0 text-rose-600 dark:text-rose-400" />
            <div className="leading-tight">
              <span className="font-bold">Внимание: обнаружен производственный простой!</span> Заявка содержит маркеры срочности оборудования или склада. Рекомендуется немедленная прямая передача.
            </div>
          </div>
        )}

        {/* Comparison: Current vs Target */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <div className="rounded-lg border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
            <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 mb-1">
              Текущий раздел
            </div>
            <div className="font-semibold text-neutral-900 dark:text-neutral-100 truncate line-through text-neutral-400">
              {currentService}
            </div>
          </div>

          <div className="rounded-lg border border-blue-200/70 bg-blue-50/40 p-2.5 shadow-2xs dark:border-blue-900/40 dark:bg-blue-950/30">
            <div className="text-[10px] font-bold uppercase tracking-wider text-blue-600 dark:text-blue-400 mb-1 flex items-center gap-1">
              <span>Рекомендуемый раздел</span>
              <IconArrowRight size={11} />
            </div>
            <div className="font-semibold text-blue-900 dark:text-blue-200 truncate">
              {targetService}
            </div>
          </div>
        </div>

        {/* Justification */}
        <div className="p-2.5 rounded-lg bg-white dark:bg-neutral-900 border border-neutral-200/70 dark:border-neutral-800 text-xs">
          <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 mb-1">
            Обоснование решения:
          </div>
          <p className="text-neutral-700 dark:text-neutral-300 leading-relaxed">
            {reason}
          </p>
        </div>
      </div>
    </div>
  );
}
