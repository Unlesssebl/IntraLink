import React from "react";
import { MessageSquare, Clock, AlertCircle, Play, Eye } from "lucide-react";
import { Button, Badge } from "@/shared/ui";
import { AgentPlan } from "@/features/autopilot/types";

export interface DialogueLoopCardProps {
  plan: AgentPlan;
  onForceResume: () => void;
  isResuming?: boolean;
}

export const DialogueLoopCard: React.FC<DialogueLoopCardProps> = ({
  plan,
  onForceResume,
  isResuming = false,
}) => {
  const dialogue = plan.dialogue_state;
  const rounds = dialogue?.rounds || 1;
  const isLimitReached = rounds >= 2;

  return (
    <div className="p-3.5 bg-blue-950/20 border border-blue-800/60 rounded-lg space-y-2.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-1 rounded bg-blue-900/40 border border-blue-700/60 text-blue-300">
            <MessageSquare className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-blue-100">
                Автономный диалоговый цикл
              </span>
              <Badge variant="info">Раунд {rounds}/2</Badge>
            </div>
            <div className="text-[10px] text-blue-300/80">
              Статус #6 «Приостановлена» ➔ Ожидание обратной связи
            </div>
          </div>
        </div>

        <Button
          size="sm"
          variant="secondary"
          loading={isResuming}
          onClick={onForceResume}
          icon={<Play className="w-3 h-3 text-blue-300" />}
          className="text-xs border-blue-800/60 hover:bg-blue-900/40 text-blue-200"
        >
          Возобновить вручную
        </Button>
      </div>

      <div className="p-2.5 bg-[#0e1218] border border-blue-900/50 rounded text-xs text-neutral-200 space-y-1.5">
        <div className="text-[10px] uppercase font-semibold text-blue-400 tracking-wider flex items-center gap-1">
          <Clock className="w-3 h-3" />
          Запрос на уточнение заявителю
        </div>
        <p className="text-[11px] text-neutral-300 leading-relaxed italic">
          "{plan.preconditions?.clarification_prompt || plan.suggested_comment || "Уточните, пожалуйста, имя компьютера или модель устройства."}"
        </p>
      </div>

      <div className="text-[11px] text-neutral-400 flex items-center justify-between">
        <span>
          При поступлении ответа заявителя автопилот возобновит исполнение в течение 30 секунд.
        </span>
        {isLimitReached && (
          <span className="text-amber-400 font-medium flex items-center gap-1 text-[10px]">
            <AlertCircle className="w-3 h-3 shrink-0" />
            Лимит раундов исчерпан
          </span>
        )}
      </div>
    </div>
  );
};
