import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Search, Sparkles, BookOpen, Copy, Check } from "lucide-react";
import { Button, Input, Card, Badge } from "@/shared/ui";
import { kbApi, KBSearchResultItem, KBAskResponse } from "@/shared/api";

export const KBSearch: React.FC = () => {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"search" | "ask">("ask");
  const [searchResults, setSearchResults] = useState<KBSearchResultItem[]>([]);
  const [askResult, setAskResult] = useState<KBAskResponse | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const searchMutation = useMutation({
    mutationFn: async ({ q, m }: { q: string; m: "search" | "ask" }) => {
      if (m === "ask") {
        const data = await kbApi.ask(q, 4);
        return { type: "ask" as const, askData: data, searchData: null };
      } else {
        const data = await kbApi.search(q, 6);
        return { type: "search" as const, askData: null, searchData: data };
      }
    },
    onSuccess: (res) => {
      if (res.type === "ask") {
        setAskResult(res.askData);
        setSearchResults([]);
      } else {
        setSearchResults(res.searchData || []);
        setAskResult(null);
      }
    },
  });

  const handleSearch = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;
    searchMutation.mutate({ q: query.trim(), m: mode });
  };

  const loading = searchMutation.isPending;
  const error = searchMutation.error
    ? (searchMutation.error as any)?.message || "Ошибка при поиске по базе знаний"
    : null;

  const copyToClipboard = (text: string, id: number) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <div className="space-y-4 max-w-4xl mx-auto">
      {/* Search Header */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-neutral-100 flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-neutral-400" />
              База знаний решений (RAG)
            </h2>
            <p className="text-xs text-neutral-400 mt-0.5">
              Семантический поиск по 10,000+ закрытым заявкам Helpdesk через BGE-M3 и pgvector
            </p>
          </div>

          {/* Mode Switcher */}
          <div className="flex items-center bg-[#121316] p-0.5 rounded border border-neutral-800">
            <button
              onClick={() => setMode("ask")}
              className={`px-3 py-1 text-xs font-medium rounded transition-colors flex items-center gap-1.5 ${
                mode === "ask"
                  ? "bg-neutral-800 text-neutral-100 font-medium border border-neutral-700 shadow-xs"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              <Sparkles className="w-3.5 h-3.5 text-neutral-400" />
              AI Синтез
            </button>
            <button
              onClick={() => setMode("search")}
              className={`px-3 py-1 text-xs font-medium rounded transition-colors flex items-center gap-1.5 ${
                mode === "search"
                  ? "bg-neutral-800 text-neutral-100 font-medium border border-neutral-700 shadow-xs"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              <Search className="w-3.5 h-3.5 text-neutral-400" />
              Похожие заявки
            </button>
          </div>
        </div>

        {/* Input Bar */}
        <form onSubmit={handleSearch} className="flex gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              mode === "ask"
                ? "Задайте вопрос по регламентам или проблемам (например: Как настроить подписание документов в СБИС?)..."
                : "Ключевые слова проблемы (например: Directum зависает при старте, ошибка 0x80004005)..."
            }
            icon={<Search className="w-4 h-4" />}
            autoFocus
          />
          <Button type="submit" variant="primary" loading={loading}>
            {mode === "ask" ? "Спросить AI" : "Найти"}
          </Button>
        </form>
      </div>

      {error && (
        <div className="p-3 bg-rose-950/40 border border-rose-800/60 rounded text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* RAG Synthesized Answer */}
      {askResult && (
        <Card
          className="border-neutral-700/80 bg-[#121316]"
          title={
            <div className="flex items-center gap-2 text-neutral-200 font-medium">
              <Sparkles className="w-4 h-4 text-neutral-300" />
              <span>Рекомендованное решение (LiteLLM RAG)</span>
            </div>
          }
          action={
            <Button
              size="sm"
              variant="ghost"
              onClick={() => copyToClipboard(askResult.answer, -1)}
            >
              {copiedId === -1 ? (
                <>
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Скопировано</span>
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5" />
                  <span>Копировать ответ</span>
                </>
              )}
            </Button>
          }
        >
          <div className="space-y-4">
            <div className="text-xs leading-relaxed text-neutral-200 whitespace-pre-wrap font-sans">
              {askResult.answer}
            </div>

            {askResult.sources?.length > 0 && (
              <div className="border-t border-neutral-800/80 pt-3">
                <span className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider block mb-2">
                  Использованные исторические заявки:
                </span>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {askResult.sources.map((src) => (
                    <div
                      key={src.task_id}
                      className="p-2.5 bg-[#101114] border border-neutral-800/80 rounded text-[11px] space-y-1"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-medium text-neutral-200">
                          #{src.task_id}
                        </span>
                        <Badge variant="neutral">
                          {Math.round(src.similarity * 100)}% совпадение
                        </Badge>
                      </div>
                      <p className="text-neutral-400 line-clamp-2">{src.problem}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Semantic Search Results List */}
      {searchResults.length > 0 && (
        <div className="space-y-3">
          <div className="text-xs text-neutral-400 font-medium">
            Найдено аналогичных решений: {searchResults.length}
          </div>
          {searchResults.map((item) => (
            <Card
              key={item.task_id}
              title={
                <div className="flex items-center gap-2">
                  <span className="font-mono text-neutral-300 font-medium">
                    #{item.task_id}
                  </span>
                  <span className="text-neutral-200 truncate">{item.problem}</span>
                </div>
              }
              action={
                <div className="flex items-center gap-2">
                  <Badge variant="neutral">
                    {Math.round(item.similarity * 100)}% сходство
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => copyToClipboard(item.solution, item.task_id)}
                  >
                    {copiedId === item.task_id ? (
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="w-3.5 h-3.5" />
                    )}
                  </Button>
                </div>
              }
            >
              <div className="space-y-2">
                {item.service_name && (
                  <div className="text-[11px] text-neutral-400">
                    Сервис:{" "}
                    <span className="text-neutral-300 font-medium">
                      {item.service_name}
                    </span>
                  </div>
                )}
                <div className="p-2.5 bg-[#0a0b0d] border border-neutral-800 rounded font-mono text-[11px] text-neutral-300 whitespace-pre-wrap">
                  {item.solution}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {!loading && !askResult && searchResults.length === 0 && query && (
        <div className="text-center py-10 text-xs text-neutral-500">
          По запросу ничего не найдено в базе знаний. Попробуйте уточнить формулировку.
        </div>
      )}
    </div>
  );
};
