import type { InspectorModuleProps } from './InspectorModuleProps';
import { useResolvedEntities } from '../useResolvedEntities';
import InspectorCard from '../primitives/InspectorCard';
import InspectorSectionHeader from '../primitives/InspectorSectionHeader';
import { IconBookOpen } from '../../Icons';

export default function KnowledgeBaseModule({
  ticket,
  details,
  onToast,
}: InspectorModuleProps) {
  const entities = useResolvedEntities(ticket, details);
  const { rag } = entities;

  const handleCopySolution = (solution: string) => {
    navigator.clipboard.writeText(solution).then(() => {
      onToast({ type: 'info', message: 'Текст решения скопирован в буфер' });
    });
  };

  return (
    <div className="space-y-3">
      <InspectorCard variant="subtle">
        <InspectorSectionHeader
          title="База знаний RAG & Прецеденты"
          icon={<IconBookOpen size={13} />}
          iconBgClass="bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400"
          badge={
            <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300">
              {rag.hasResults ? `Найдено ${rag.results.length}` : 'Семантический поиск'}
            </span>
          }
        />

        {/* RAG Precedents List */}
        {rag.hasResults ? (
          <div className="space-y-2">
            <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400">
              Похожие закрытые обращения (pgvector):
            </div>
            {rag.results.map((item: any, idx: number) => {
              const taskId = item.id || item.task_id;
              const title = item.name || item.title || item.problem || 'Решение по обращению';
              const similarityPct = Math.round(
                item.similarity_pct ||
                  (item.similarity ? item.similarity * 100 : 0) ||
                  (1 - (item.distance || 0.3)) * 100
              );

              return (
                <div
                  key={taskId || idx}
                  className="rounded-xl border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900 text-xs space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-neutral-900 dark:text-neutral-100 truncate font-mono">
                      #{taskId} {title}
                    </span>
                    <span className="font-mono text-[10px] font-bold text-emerald-600 dark:text-emerald-400 shrink-0">
                      {similarityPct}% совпадение
                    </span>
                  </div>

                  {item.solution && (
                    <p className="text-neutral-600 dark:text-neutral-300 line-clamp-3 text-[11.5px] leading-relaxed">
                      {item.solution}
                    </p>
                  )}

                  {item.solution && (
                    <div className="flex justify-end pt-1">
                      <button
                        type="button"
                        onClick={() => handleCopySolution(item.solution)}
                        className="text-purple-600 hover:text-purple-700 dark:text-purple-400 font-semibold text-[11px] cursor-pointer"
                      >
                        Копировать решение
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="py-6 text-center text-xs text-neutral-400">
            Прямых текстовых совпадений в базе знаний не обнаружено.
          </div>
        )}
      </InspectorCard>
    </div>
  );
}
