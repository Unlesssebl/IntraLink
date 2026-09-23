import React from "react";

export interface StatusDotProps {
  statusId?: number | null;
  statusName?: string | null;
  size?: "sm" | "md";
  pulse?: boolean;
  className?: string;
}

export const StatusDot: React.FC<StatusDotProps> = ({
  statusId,
  statusName = "",
  size = "sm",
  pulse: forcedPulse,
  className = "",
}) => {
  const normName = (statusName || "").toLowerCase();

  // Brandbook 3.2 Semantic status tokens
  let dotColor = "bg-neutral-400";
  let pulse = false;

  if (statusId === 1 || normName.includes("нов")) {
    // New / In queue (Blue)
    dotColor = "bg-blue-500";
    pulse = true;
  } else if (statusId === 2 || normName.includes("работ")) {
    // In progress (Amber)
    dotColor = "bg-amber-500";
  } else if (statusId === 3 || normName.includes("ожид") || normName.includes("приостан")) {
    // Waiting / Paused (Neutral)
    dotColor = "bg-neutral-400";
  } else if (statusId === 4 || normName.includes("решен")) {
    // Resolved / Success (Emerald)
    dotColor = "bg-emerald-500";
  } else if (statusId === 5 || normName.includes("закр")) {
    // Closed (Neutral muted)
    dotColor = "bg-neutral-600";
  } else if (statusId === 30 || normName.includes("отмен")) {
    // Cancelled / Critical (Rose)
    dotColor = "bg-rose-500";
  }

  const shouldPulse = forcedPulse !== undefined ? forcedPulse : pulse;
  const dimension = size === "sm" ? "h-1.5 w-1.5" : "h-2 w-2";

  return (
    <span
      className={`relative inline-flex items-center justify-center shrink-0 ${dimension} ${className}`}
    >
      {shouldPulse && (
        <span
          className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-60 ${dotColor}`}
        />
      )}
      <span className={`relative inline-flex rounded-full ${dimension} ${dotColor}`} />
    </span>
  );
};
