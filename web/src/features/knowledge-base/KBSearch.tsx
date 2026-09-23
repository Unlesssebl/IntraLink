import React, { useState } from "react";
import { Search, Sparkles, BookOpen, Copy, Check, ArrowRight } from "lucide-react";
import { Button, Input, Card, Badge } from "@/shared/ui";
import { kbApi, KBSearchResultItem, KBAskResponse } from "@/shared/api";

export const KBSearch: React.FC = () => {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"search" | "ask">("ask");
  const [loading, setLoading] = useState(false);
  const [searchResults, setSearchResults] = useState<KBSearchResultItem[]>([]);
  const [askResult, setAskResult] = useState<KBAskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;

    try {
      setLoading(true);
      setError(null);
      if (mode === "ask") {
        const res = await kbApi.ask(query.trim(), 4);
        setAskResult(res);
        setSearchResults([]);
      } else {
        const res = await kbApi.search(query.trim(), 6);
        setSearchResults(res);
        setAskResult(null);
      }
    } catch (err: any) {
      setError(err?.message || "Ошибка при поиске по базе знаний");
    } finally {
      setLoading(false);
    }
  };

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
            <h2 className="text-base font-semibold text-slate-100 flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-indigo-400" />
              База знаний решений (RAG)
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Семантический поиск по 10,000+ закрытым заявкам Helpdesk через BGE-M3 и pgvector
            </p>
          </div>

          {/* Mode Switcher */}
          <div className="flex items-center bg-[#151922] p-0.5 rounded-lg border border-[#242938]">
            <button
              onClick={() => setMode("ask")}
              className={`px-3 py-1 text-xs font-medium rounded-md transition-colors flex items-center gap-1.5 ${
                mode === "ask"
                  ? "bg-indigo-600 text-white shadow-xs"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              AI Синтез
            </button>
            <button
              onClick={() => setMode("search")}
              className={`px-3 py-1 text-xs font-medium rounded-md transition-colors flex items-center gap-1.5 ${
                mode === "search"
                  ? "bg-[#252b3b] text-white"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Search className="w-3.5 h-3.5" />
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
        <div className="p-3 bg-red-950/40 border border-red-800/60 rounded-lg text-xs text-red-300">
          {error}
        </div>
      )}

      {/* RAG Synthesized Answer */}
      {askResult && (
        <Card
          className="border-indigo-900/50 bg-[#121622]/80"
          title={
            <div className="flex items-center gap-2 text-indigo-300">
              <Sparkles className="w-4 h-4" />
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
            <div className="text-xs leading-relaxed text-slate-200 whitespace-pre-wrap font-sans">
              {askResult.answer}
            </div>

            {askResult.sources?.length > 0 && (
              <div className="border-t border-[#23293a] pt-3">
                <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider block mb-2">
                  Использованные исторические заявки:
                </span>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {askResult.sources.map((src) => (
                    <div
                      key={src.task_id}
                      className="p-2 bg-[#171c26] border border-[#252c3c] rounded text-[11px] space-y-1"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-medium text-indigo-400">
                          #{src.task_id}
                        </span>
                        <Badge variant="neutral">
                          {Math.round(src.similarity * 100)}% совпадение
                        </Badge>
                      </div>
                      <p className="text-slate-300 line-clamp-2">{src.problem}</p>
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
          <div className="text-xs text-slate-400 font-medium">
            Найдено аналогичных решений: {searchResults.length}
          </div>
          {searchResults.map((item) => (
            <Card
              key={item.task_id}
              title={
                <div className="flex items-center gap-2">
                  <span className="font-mono text-indigo-400 font-semibold">
                    #{item.task_id}
                  </span>
                  <span className="text-slate-200 truncate">{item.problem}</span>
                </div>
              }
              action={
                <div className="flex items-center gap-2">
                  <Badge variant="accent">
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
                  <div className="text-[11px] text-slate-400">
                    Сервис:{" "}
                    <span className="text-slate-300 font-medium">
                      {item.service_name}
                    </span>
                  </div>
                )}
                <div className="p-2.5 bg-[#0e1117] border border-[#202533] rounded font-mono text-[11px] text-slate-300 whitespace-pre-wrap">
                  {item.solution}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {!loading && !askResult && searchResults.length === 0 && query && (
        <div className="text-center py-10 text-xs text-slate-500">
          По запросу ничего не найдено в базе знаний. Попробуйте уточнить формулировку.
        </div>
      )}
    </div>
  );
};
