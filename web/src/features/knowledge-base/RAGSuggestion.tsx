import React, { useState, useEffect } from "react";
import { Sparkles, ArrowDownToLine, ChevronDown, ChevronUp } from "lucide-react";
import { Button, Badge } from "@/shared/ui";
import { kbApi, KBSearchResultItem } from "@/shared/api";

export interface RAGSuggestionProps {
  query: string;
  onApplySolution: (solutionText: string) => void;
}

export const RAGSuggestion: React.FC<RAGSuggestionProps> = ({
  query,
  onApplySolution,
}) => {
  const [match, setMatch] = useState<KBSearchResultItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!query || query.length < 5) {
      setMatch(null);
      return;
    }

    let isMounted = true;
    const searchKB = async () => {
      try {
        setLoading(true);
        const results = await kbApi.search(query, 1, 0.72);
        if (isMounted && results.length > 0) {
          setMatch(results[0]);
        } else if (isMounted) {
          setMatch(null);
        }
      } catch {
        if (isMounted) setMatch(null);
      } finally {
        if (isMounted) setLoading(false);
      }
    };

    const timer = setTimeout(searchKB, 300);
    return () => {
      isMounted = false;
      clearTimeout(timer);
    };
  }, [query]);

  if (loading || !match) return null;

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
