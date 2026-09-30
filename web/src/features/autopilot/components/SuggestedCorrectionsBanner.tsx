import React from "react";
import { ArrowRight, Check, CheckCheck, MessageSquare, Sparkles, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { autopilotApi } from "../api";
import type { SuggestedCorrection, TicketAutomation } from "../types";
import { Badge, Button, useToast } from "@/shared/ui";

export interface SuggestedCorrectionsBannerProps {
  ticketId: number;
  automation: TicketAutomation;
  corrections: SuggestedCorrection[];
  onApplied?: () => Promise<unknown> | void;
}

export const SuggestedCorrectionsBanner: React.FC<SuggestedCorrectionsBannerProps> = ({
  ticketId,
  automation,
  corrections,
  onApplied,
}) => {
  const [dismissedKeys, setDismissedKeys] = React.useState<Set<string>>(new Set());
  const [busyKey, setBusyKey] = React.useState<string | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();

  const activeCorrections = React.useMemo(() => {
    const entities = automation.case_frame?.entities || {};
    return (corrections || []).filter((c) => {
      if (dismissedKeys.has(c.param_key)) return false;
      const suggested = c.suggested_value.trim();
      if (!suggested) return false;
      // If the entity in the ticket already has this exact suggested value, it is already applied
      const currentEntityVal = (entities[c.param_key] || c.current_value || "").trim();
      return suggested.toLowerCase() !== currentEntityVal.toLowerCase();
    });
  }, [corrections, dismissedKeys, automation.case_frame?.entities]);

  if (activeCorrections.length === 0) {
    return null;
  }

  const applyFacts = async (facts: Record<string, string>, actionKey: string) => {
    if (busyKey) return;
    setBusyKey(actionKey);
    try {
      await autopilotApi.correctFacts(ticketId, automation, facts);
      // Immediately dismiss applied keys locally so the banner/buttons disappear instantly
      setDismissedKeys((prev) => new Set([...prev, ...Object.keys(facts)]));
      await queryClient.invalidateQueries({ queryKey: ["autopilot", "automation", ticketId] });
      if (onApplied) {
        await onApplied();
      }
      toast.success(
        Object.keys(facts).length > 1
          ? "Уточнённые параметры успешно применены"
          : "Параметр успешно обновлён"
      );
    } catch (cause) {
      toast.error(
        cause instanceof Error ? cause.message : "Не удалось применить исправление"
      );
    } finally {
      setBusyKey(null);
    }
  };

  const applySingle = (corr: SuggestedCorrection) => {
    return applyFacts({ [corr.param_key]: corr.suggested_value }, corr.param_key);
  };

  const applyAll = () => {
    const allFacts = Object.fromEntries(
      activeCorrections.map((c) => [c.param_key, c.suggested_value])
    );
    return applyFacts(allFacts, "__all__");
  };

  const dismissSingle = (paramKey: string) => {
    setDismissedKeys((prev) => new Set([...prev, paramKey]));
  };

  return (
    <div className="rounded-xl border border-violet-700/40 bg-gradient-to-br from-violet-950/30 via-[#12131c] to-[#0f1118] p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-violet-900/30 pb-3">
        <div className="flex items-center gap-2.5">
          <div className="rounded-lg border border-violet-500/30 bg-violet-500/10 p-1.5 text-violet-300">
            <Sparkles className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h4 className="text-xs font-semibold text-neutral-100">
                Заявитель уточнил данные в переписке
              </h4>
              <Badge variant="purple" isMicro>
                HITL Уточнение
              </Badge>
            </div>
            <p className="mt-0.5 text-[11px] text-neutral-400">
              В комментариях обнаружены актуальные значения параметров для запуска сценария
            </p>
          </div>
        </div>

        {activeCorrections.length > 1 && (
          <Button
            size="sm"
            variant="primary"
            loading={busyKey === "__all__"}
            disabled={Boolean(busyKey)}
            onClick={applyAll}
            icon={<CheckCheck className="h-3.5 w-3.5" />}
          >
            Применить все ({activeCorrections.length})
          </Button>
        )}
      </div>

      <div className="mt-3 space-y-2.5">
        {activeCorrections.map((corr) => {
          const isBusy = busyKey === corr.param_key;
          return (
            <div
              key={corr.param_key}
              className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border border-neutral-800/80 bg-[#0c0e14]/80 p-3 transition hover:border-neutral-700/80"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-neutral-300">
                    {corr.param_label || corr.param_key}:
                  </span>
                  <div className="flex items-center gap-1.5 font-mono text-xs">
                    {corr.current_value ? (
                      <span className="text-neutral-500 line-through decoration-rose-500/60">
                        {corr.current_value}
                      </span>
                    ) : (
                      <span className="text-neutral-500 italic">не заполнено</span>
                    )}
                    <ArrowRight className="h-3 w-3 text-neutral-600 shrink-0" />
                    <span className="font-semibold text-emerald-400 bg-emerald-950/30 px-1.5 py-0.5 rounded border border-emerald-800/40">
                      {corr.suggested_value}
                    </span>
                  </div>
                </div>

                {corr.text_span && (
                  <div className="mt-1.5 flex items-start gap-1.5 text-[11px] text-neutral-400">
                    <MessageSquare className="h-3 w-3 mt-0.5 shrink-0 text-violet-400/70" />
                    <span className="italic line-clamp-2">
                      «{corr.text_span}»
                    </span>
                  </div>
                )}
              </div>

              <div className="flex items-center gap-1.5 self-end sm:self-center shrink-0">
                <Button
                  size="sm"
                  variant="secondary"
                  loading={isBusy}
                  disabled={Boolean(busyKey)}
                  onClick={() => applySingle(corr)}
                  icon={<Check className="h-3.5 w-3.5 text-emerald-400" />}
                >
                  Применить
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={Boolean(busyKey)}
                  onClick={() => dismissSingle(corr.param_key)}
                  title="Отклонить уточнение"
                  className="px-2 text-neutral-400 hover:text-neutral-200"
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
