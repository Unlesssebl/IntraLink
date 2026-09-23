import React, { useState, useEffect } from "react";
import {
  Inbox,
  RefreshCw,
  Sparkles,
  CheckCircle,
  Copy,
  ChevronRight,
  Filter,
  CheckCheck,
  AlertTriangle,
} from "lucide-react";
import { Button, Badge, Card, Modal } from "@/shared/ui";
import { HostBadge } from "@/features/diagnostics/HostBadge";
import {
  ticketsApi,
  triageApi,
  TicketListItem,
  TriageResult,
} from "@/shared/api";

export interface TriageQueueProps {
  onSelectTicket: (ticketId: number) => void;
  selectedTicketId: number | null;
}

export const TriageQueue: React.FC<TriageQueueProps> = ({
  onSelectTicket,
  selectedTicketId,
}) => {
  const [tickets, setTickets] = useState<TicketListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // AI Classification cache: ticketId -> TriageResult
  const [triageResults, setTriageResults] = useState<Record<number, TriageResult>>({});
  const [classifyingId, setClassifyingId] = useState<number | null>(null);

  const fetchQueue = async () => {
    try {
      setLoading(true);
      setError(null);
      // Filter 984 = standard 1st line queue
      const list = await ticketsApi.list({ filter_id: 984, limit: 30 });
      setTickets(list);
    } catch (err: any) {
      setError(err?.message || "Не удалось загрузить очередь заявок");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchQueue();
  }, []);

  const handleClassify = async (e: React.MouseEvent, t: TicketListItem) => {
    e.stopPropagation();
    try {
      setClassifyingId(t.id);
      const res = await triageApi.classify({
        task_id: t.id,
        name: t.name,
        description: t.name, // or detailed if available
        applicant_name: t.applicant_name || undefined,
        pc_name: t.pc_name || undefined,
        service_name: t.service_name || undefined,
      });
      setTriageResults((prev) => ({ ...prev, [t.id]: res }));
    } catch (err: any) {
      alert(`Ошибка анализа AI: ${err?.message}`);
    } finally {
      setClassifyingId(null);
    }
  };

  const handleTakeQuick = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    try {
      await ticketsApi.take(id);
      fetchQueue();
    } catch (err: any) {
      alert(`Ошибка: ${err?.message}`);
    }
  };

  return (
    <div className="space-y-4">
      {/* Action Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Inbox className="w-5 h-5 text-indigo-400" />
            <h2 className="text-base font-semibold text-slate-100">
              Очередь 1-й линии (Filter 984)
            </h2>
          </div>
          <Badge variant="accent">{tickets.length} заявок</Badge>
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            loading={loading}
            icon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={fetchQueue}
          >
            Обновить
          </Button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-950/40 border border-red-800/60 rounded-lg text-xs text-red-300">
          {error}
        </div>
      )}

      {/* Tickets List */}
      <div className="space-y-2">
        {tickets.map((t) => {
          const isSelected = selectedTicketId === t.id;
          const aiResult = triageResults[t.id];
          const isClassifying = classifyingId === t.id;

          return (
            <div
              key={t.id}
              onClick={() => onSelectTicket(t.id)}
              className={`p-3.5 rounded-lg border transition-all cursor-pointer ${
                isSelected
                  ? "bg-[#161a25] border-indigo-500/80 shadow-md ring-1 ring-indigo-500/30"
                  : "bg-[#11141c] border-[#202533] hover:border-[#2e3547] hover:bg-[#141822]"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                {/* Left side: ID, Title, applicant */}
                <div className="space-y-1.5 flex-1 min-w-0">
                  <div className="flex items-center gap-2.5 flex-wrap">
                    <span className="font-mono text-xs font-bold text-indigo-400">
                      #{t.id}
                    </span>
                    <span className="text-xs font-semibold text-slate-100 truncate">
                      {t.name}
                    </span>
                    {t.service_name && (
                      <Badge variant="neutral">{t.service_name}</Badge>
                    )}
                    {aiResult?.is_duplicate && (
                      <Badge variant="danger" dot>
                        Дубликат #{aiResult.duplicate_of_task_id}
                      </Badge>
                    )}
                  </div>

                  <div className="flex items-center gap-4 text-[11px] text-slate-400">
                    <span>
                      Заявитель:{" "}
                      <strong className="text-slate-300 font-medium">
                        {t.applicant_name || "Не указан"}
                      </strong>
                    </span>
                    <div className="flex items-center gap-1.5">
                      <span>ПК:</span>
                      <HostBadge host={t.pc_name} />
                    </div>
                    <span className="font-mono text-slate-500">
                      {new Date(t.created).toLocaleTimeString("ru-RU", {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  </div>

                  {/* AI Recommendation Banner if analyzed */}
                  {aiResult && (
                    <div className="mt-2 p-2.5 bg-[#171b28] border border-indigo-900/50 rounded-md text-xs space-y-1.5 animate-in fade-in duration-150">
                      <div className="flex items-center justify-between text-[11px]">
                        <span className="flex items-center gap-1 text-indigo-300 font-semibold">
                          <Sparkles className="w-3 h-3 text-indigo-400" />
                          Вердикт: {aiResult.category} ({Math.round(aiResult.confidence * 100)}%)
                        </span>
                        {aiResult.suggested_service_id && (
                          <span className="text-amber-400">
                            Рекомендован сервис ID: {aiResult.suggested_service_id}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-slate-300 leading-relaxed">
                        {aiResult.reasoning}
                      </p>
                      {aiResult.draft_reply && (
                        <div className="p-2 bg-[#0e1118] border border-[#232938] rounded text-[11px] font-mono text-slate-300">
                          {aiResult.draft_reply}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Right side: Actions */}
                <div className="flex items-center gap-2 shrink-0">
                  {!aiResult && (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={isClassifying}
                      icon={<Sparkles className="w-3.5 h-3.5 text-indigo-400" />}
                      onClick={(e) => handleClassify(e, t)}
                      title="AI Экспресс-разбор"
                    >
                      AI Разбор
                    </Button>
                  )}

                  <Button
                    size="sm"
                    variant="secondary"
                    icon={<CheckCircle className="w-3.5 h-3.5 text-emerald-400" />}
                    onClick={(e) => handleTakeQuick(e, t.id)}
                    title="Взять в работу"
                  >
                    В работу
                  </Button>

                  <ChevronRight className="w-4 h-4 text-slate-500" />
                </div>
              </div>
            </div>
          );
        })}

        {!loading && tickets.length === 0 && (
          <div className="text-center py-16 bg-[#11141c] border border-[#202533] rounded-lg space-y-2">
            <CheckCheck className="w-8 h-8 text-emerald-400 mx-auto" />
            <h4 className="text-sm font-semibold text-slate-200">
              Очередь 1-й линии пуста
            </h4>
            <p className="text-xs text-slate-400">
              Все поступившие заявки обработаны. Отличная работа!
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
