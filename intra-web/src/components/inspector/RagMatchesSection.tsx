import React from 'react';
import type { RAGMatchItem } from '../../lib/types';
import { IconBookOpen, IconChevronDown } from '../Icons';

interface RagMatchesSectionProps {
  kbMatches: RAGMatchItem[];
  isRagExpanded: boolean;
  onToggleRagExpanded: () => void;
  onInsertSolution: (solution: string, taskId: number) => void;
}

export default function RagMatchesSection({
  kbMatches,
  isRagExpanded,
  onToggleRagExpanded,
  onInsertSolution,
}: RagMatchesSectionProps) {
  if (!kbMatches || kbMatches.length === 0) return null;

  return (
    <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl overflow-hidden bg-white dark:bg-neutral-900 shadow-xs">
      <button
        type="button"
        onClick={onToggleRagExpanded}
        className="w-full px-3.5 py-2.5 flex items-center justify-between text-left hover:bg-neutral-50 dark:hover:bg-neutral-800/60 transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider text-purple-600 dark:text-purple-400 inline-flex items-center gap-1.5">
            <IconBookOpen size={13} className="shrink-0" />
            <span>База знаний RAG</span>
          </span>
          <span className="text-[10.5px] px-2 py-0.2 rounded-full bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300 font-semibold font-mono">
            {kbMatches.length} {kbMatches.length === 1 ? 'прецедент' : 'прецедента'}
          </span>
        </div>
        <div className="flex items-center gap-1 text-[11.5px] text-neutral-500">
          <span>{isRagExpanded ? 'Свернуть' : 'Развернуть'}</span>
          <IconChevronDown size={14} className={`transition-transform duration-200 ${isRagExpanded ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {isRagExpanded && (
        <div className="p-3.5 pt-1 border-t border-neutral-100 dark:border-neutral-800 space-y-2.5">
          {kbMatches.map((m: any, idx: number) => {
            const pct = Math.round((1 - (m.distance || 0.3)) * 100);
            return (
              <div
                key={m.task_id || idx}
                className="p-3 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50/60 dark:bg-neutral-950/40 text-xs space-y-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 font-semibold text-neutral-900 dark:text-neutral-100 truncate">
                    <span className="text-purple-600 dark:text-purple-400 font-mono">#{m.task_id}</span>
                    <span className="truncate">{m.name || m.problem}</span>
                  </div>
                  <span className="text-[10.5px] font-mono px-2 py-0.2 rounded bg-purple-100 text-purple-700 dark:bg-purple-900/60 dark:text-purple-300 font-semibold shrink-0">
                    {pct}% сходство
                  </span>
                </div>
                {m.problem && (
                  <p className="text-[11.5px] text-neutral-500 dark:text-neutral-400 line-clamp-2">
                    <strong>Симптом:</strong> {m.problem}
                  </p>
                )}
                {m.solution && (
                  <div className="bg-white dark:bg-neutral-900 p-2.5 rounded border border-neutral-200 dark:border-neutral-800 text-[12px] text-neutral-700 dark:text-neutral-300 leading-relaxed">
                    <strong>Решение:</strong> {m.solution}
                  </div>
                )}
                <div className="flex justify-end pt-0.5">
                  <button
                    type="button"
                    onClick={() => onInsertSolution(m.solution, m.task_id)}
                    className="text-[11.5px] text-purple-600 dark:text-purple-400 hover:underline font-semibold cursor-pointer"
                  >
                    Вставить решение в ответ →
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
