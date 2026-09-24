import React from "react";
import { getStatusMeta } from "../statuses";

export interface StatusDotProps {
  statusId?: number | null;
  statusName?: string | null;
  size?: "sm" | "md";
  pulse?: boolean;
  className?: string;
}

export const StatusDot: React.FC<StatusDotProps> = ({
  statusId,
  statusName,
  size = "sm",
  pulse: forcedPulse,
  className = "",
}) => {
  const meta = getStatusMeta(statusId, statusName);
  const shouldPulse = forcedPulse !== undefined ? forcedPulse : Boolean(meta.pulse);
  const dimension = size === "sm" ? "h-1.5 w-1.5" : "h-2 w-2";

  return (
    <span
      className={`relative inline-flex items-center justify-center shrink-0 ${dimension} ${className}`}
      title={meta.name}
    >
      {shouldPulse && (
        <span
          className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-60 ${meta.dotColor}`}
        />
      )}
      <span className={`relative inline-flex rounded-full ${dimension} ${meta.dotColor}`} />
    </span>
  );
};
