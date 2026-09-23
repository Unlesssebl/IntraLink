import React from "react";

export interface StatusDotProps {
  statusId?: number | null;
  statusName?: string | null;
  size?: "sm" | "md";
  className?: string;
}

export const StatusDot: React.FC<StatusDotProps> = ({
  statusId,
  statusName = "",
  size = "sm",
  className = "",
}) => {
  const normName = (statusName || "").toLowerCase();

  let dotColor = "bg-slate-400";
  let pulse = false;

  if (statusId === 1 || normName.includes("нов")) {
    // New
    dotColor = "bg-indigo-400 shadow-[0_0_8px_rgba(129,140,248,0.8)]";
    pulse = true;
  } else if (statusId === 2 || normName.includes("работ")) {
    // In progress
    dotColor = "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]";
  } else if (statusId === 3 || normName.includes("ожид") || normName.includes("приостан")) {
    // Waiting
    dotColor = "bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.7)]";
  } else if (statusId === 4 || normName.includes("решен")) {
    // Resolved
    dotColor = "bg-teal-400 shadow-[0_0_8px_rgba(45,212,191,0.7)]";
  } else if (statusId === 5 || normName.includes("закр")) {
    // Closed
    dotColor = "bg-zinc-500";
  } else if (statusId === 30 || normName.includes("отмен")) {
    // Cancelled
    dotColor = "bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.7)]";
  }

  const dimension = size === "sm" ? "h-2 w-2" : "h-2.5 w-2.5";

  return (
    <span className={`relative inline-flex items-center justify-center shrink-0 ${dimension} ${className}`}>
      {pulse && (
        <span
          className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${dotColor.split(" ")[0]}`}
        />
      )}
      <span className={`relative inline-flex rounded-full ${dimension} ${dotColor}`} />
    </span>
  );
};
