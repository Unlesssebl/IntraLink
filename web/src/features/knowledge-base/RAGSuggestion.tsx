import React, { useState } from "react";
import { Sparkles, ArrowDownToLine, ChevronDown, ChevronUp } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button, Badge } from "@/shared/ui";
import { kbApi } from "@/shared/api";

export interface RAGSuggestionProps {
  query: string;
  onApplySolution: (solutionText: string) => void;
}

export const RAGSuggestion: React.FC<RAGSuggestionProps> = ({
  query,
  onApplySolution,
}) => {
  const [expanded, setExpanded] = useState(false);

  const cleanQuery = query?.trim() || "";

  const { data: match, isLoading } = useQuery({
    queryKey: ["kb", "suggestion", cleanQuery],
    queryFn: async ({ signal }) => {
      const results = await kbApi.search(cleanQuery, 1, 0.72, signal);
      return results.length > 0 ? results[0] : null;
    },
    enabled: cleanQuery.length >= 5,
    staleTime: 5 * 60 * 1000, // 5 minutes cache
    gcTime: 15 * 60 * 1000,
  });

  if (isLoading || !match) return null;

  return (
    <div className="p-3 bg-[#121316] border border-neutral-700/80 rounded text-xs space-y-2 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 font-medium text-neutral-200">
          <Sparkles className="w-3.5 h-3.5 text-neutral-300" />
          <span>Аналогичное решение из базы знаний</span>
          <Badge variant="neutral">
            #{match.task_id} ({Math.round(match.similarity * 100)}%)
          </Badge>
        </div>

        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            icon={<ArrowDownToLine className="w-3.5 h-3.5 text-neutral-300" />}
            onClick={() => onApplySolution(match.solution)}
            title="Вставить в комментарий"
          >
            Вставить в ответ
          </Button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-neutral-400 hover:text-neutral-200 p-1 rounded hover:bg-neutral-800/60"
          >
            {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      <div className={`text-[11px] text-neutral-300 leading-relaxed ${expanded ? "" : "line-clamp-2"}`}>
        <strong className="text-neutral-400">Решение: </strong>
        {match.solution}
      </div>
    </div>
  );
};
