import type { InspectorModuleProps } from './InspectorModuleProps';
import {
  IconBookOpen,
  IconSparkles,
  IconCopy,
  IconCheck,
} from '../../Icons';

export default function KnowledgeBaseModule({
  ticket,
  details,
  onToast,
}: InspectorModuleProps) {
  const ragResults: any[] = details?.rag_results || [];
  const aiDraft = details?.decision_envelope?.response?.text;

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-neutral-200/80 bg-neutral-50/50 p-3.5 dark:border-neutral-800 dark:bg-neutral-950/40 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400">
              <IconBookOpen size={13} />
            </span>
            <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-700 dark:text-neutral-300">
              База знаний RAG & Прецеденты
            </span>
          </div>

          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300">
            {ragResults.length > 0 ? `Найдено ${ragResults.length}` : 'Семантический поиск'}
          </span>
        </div>

        {/* AI Draft Suggestion Box if available */}
        {aiDraft && (
          <div className="rounded-lg border border-purple-200/80 bg-purple-50/40 p-2.5 dark:border-purple-900/40 dark:bg-purple-950/30 text-xs">
            <div className="flex items-center justify-between gap-2 text-[10.5px] font-bold uppercase tracking-wider text-purple-700 dark:text-purple-300 mb-1">
              <span className="flex items-center gap-1">
                <IconSparkles size={12} />
                Рекомендованный ответ AI Hub
              </span>
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard.writeText(aiDraft);
                  onToast({ type: 'info', message: 'Черновик ответа скопирован' });
                }}
                className="text-purple-600 dark:text-purple-400 hover:underline inline-flex items-center gap-0.5 cursor-pointer font-medium"
              >
                <IconCopy size={11} />
                <span>Копировать</span>
              </button>
            </div>
            <p className="text-neutral-800 dark:text-neutral-200 whitespace-pre-wrap leading-relaxed font-sans">
              {aiDraft}
            </p>
          </div>
        )}

        {/* RAG Precedents List */}
        {ragResults.length > 0 ? (
          <div className="space-y-2">
            <div className="text-[10px] font-bold uppercase tracking-wider text-neutral-400">
              Похожие закрытые обращения (pgvector):
            </div>
            {ragResults.map((item, idx) => (
              <div
                key={item.id || idx}
                className="rounded-lg border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900 text-xs space-y-1"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-neutral-900 dark:text-neutral-100 truncate">
                    #{item.id || item.task_id} {item.name || item.title || 'Решение по обращению'}
                  </span>
                  {item.similarity && (
                    <span className="font-mono text-[10px] text-emerald-600 dark:text-emerald-400 shrink-0">
                      {Math.round(item.similarity * 100)}% совпадение
                    </span>
                  )}
                </div>
                {item.solution && (
                  <p className="text-neutral-600 dark:text-neutral-300 line-clamp-3 text-[11.5px]">
                    {item.solution}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="py-4 text-center text-xs text-neutral-400">
            Прямых текстовых совпадений в базе знаний не обнаружено.
          </div>
        )}
      </div>
    </div>
  );
}
