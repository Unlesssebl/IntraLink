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
    <div className="p-3 bg-[#13192b] border border-indigo-500/30 rounded-lg text-xs space-y-2 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 font-semibold text-indigo-300">
          <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
          <span>Аналогичное решение из базы знаний</span>
          <Badge variant="accent">
            #{match.task_id} ({Math.round(match.similarity * 100)}%)
          </Badge>
        </div>

        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            icon={<ArrowDownToLine className="w-3.5 h-3.5 text-indigo-300" />}
            onClick={() => onApplySolution(match.solution)}
            title="Вставить в комментарий"
          >
            Вставить в ответ
          </Button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-slate-400 hover:text-slate-200 p-1 rounded"
          >
            {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      <div className={`text-[11px] text-slate-300 ${expanded ? "" : "line-clamp-2"}`}>
        <strong className="text-slate-400">Решение: </strong>
        {match.solution}
      </div>
    </div>
  );
};
