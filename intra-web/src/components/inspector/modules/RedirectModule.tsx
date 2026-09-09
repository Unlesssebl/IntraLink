import type { InspectorModuleProps } from './InspectorModuleProps';
import { useResolvedEntities } from '../useResolvedEntities';
import InspectorCard from '../primitives/InspectorCard';
import InspectorSectionHeader from '../primitives/InspectorSectionHeader';
import {
  IconRedirect,
  IconAlertTriangle,
  IconArrowRight,
} from '../../Icons';

export default function RedirectModule({
  ticket,
  details,
}: InspectorModuleProps) {
  const entities = useResolvedEntities(ticket, details);
  const { routing } = entities;

  return (
    <div className="space-y-3">
      <InspectorCard variant="subtle">
        <InspectorSectionHeader
          title="Маршрутизация & Редирект каталога"
          icon={<IconRedirect size={13} />}
          iconBgClass="bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400"
          badge={
            <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300">
              Перенаправление
            </span>
          }
        />

        {/* Downtime alert if critical */}
        {routing.isDowntime && (
          <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50/90 p-2.5 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
            <IconAlertTriangle
              size={15}
              className="mt-0.5 shrink-0 text-rose-600 dark:text-rose-400"
            />
            <div className="leading-tight">
              <span className="font-bold">Внимание: обнаружен производственный простой!</span>{' '}
              Заявка содержит маркеры срочности оборудования или склада. Рекомендуется
              немедленная прямая передача.
            </div>
          </div>
        )}

        {/* Comparison: Current vs Target */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <div className="rounded-xl border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
            <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 mb-1">
              Текущий раздел
            </div>
            <div className="font-semibold text-neutral-900 dark:text-neutral-100 truncate line-through text-neutral-400">
              {routing.currentService}
            </div>
          </div>

          <div className="rounded-xl border border-blue-200/70 bg-blue-50/40 p-2.5 shadow-2xs dark:border-blue-900/40 dark:bg-blue-950/30">
            <div className="text-[10px] font-bold uppercase tracking-wider text-blue-600 dark:text-blue-400 mb-1 flex items-center gap-1">
              <span>Рекомендуемый раздел</span>
              <IconArrowRight size={11} />
            </div>
            <div className="font-semibold text-blue-900 dark:text-blue-200 truncate">
              {routing.targetService}
            </div>
          </div>
        </div>

        {/* Justification */}
        <div className="p-2.5 rounded-xl bg-white dark:bg-neutral-900 border border-neutral-200/70 dark:border-neutral-800 text-xs">
          <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400 mb-1">
            Обоснование решения:
          </div>
          <p className="text-neutral-700 dark:text-neutral-300 leading-relaxed font-medium">
            {routing.reason}
          </p>
        </div>
      </InspectorCard>
    </div>
  );
}
