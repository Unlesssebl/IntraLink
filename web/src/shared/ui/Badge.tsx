import React from "react";

export type BadgeVariant =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "accent"
  | "purple"
  | "micro";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  dot?: boolean;
  pulse?: boolean;
  pill?: boolean;
  isMicro?: boolean;
}

export const Badge: React.FC<BadgeProps> = ({
  variant = "neutral",
  dot = false,
  pulse = false,
  pill = false,
  isMicro = false,
  children,
  className = "",
  ...props
}) => {
  if (variant === "micro" || isMicro) {
    return (
      <span
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 border border-neutral-700/80 bg-neutral-800/80 text-neutral-300 rounded text-[10px] font-mono uppercase tracking-wider select-none ${className}`}
        {...props}
      >
        {children}
      </span>
    );
  }

  const variantStyles: Record<string, { badge: string; dot: string }> = {
    neutral: {
      badge: "bg-neutral-800/60 text-neutral-300 border-neutral-700/80",
      dot: "bg-neutral-400",
    },
    success: {
      badge: "bg-emerald-950/40 text-emerald-300 border-emerald-800/60",
      dot: "bg-emerald-500",
    },
    warning: {
      badge: "bg-amber-950/40 text-amber-300 border-amber-800/60",
      dot: "bg-amber-500",
    },
    danger: {
      badge: "bg-rose-950/40 text-rose-300 border-rose-800/60",
      dot: "bg-rose-500",
    },
    info: {
      badge: "bg-blue-950/40 text-blue-300 border-blue-800/60",
      dot: "bg-blue-500",
    },
    accent: {
      badge: "bg-blue-950/40 text-blue-300 border-blue-800/60",
      dot: "bg-blue-500",
    },
    purple: {
      badge: "bg-neutral-800/70 text-neutral-300 border-neutral-700/80",
      dot: "bg-neutral-400",
    },
  };

  const current = variantStyles[variant] || variantStyles.neutral;
  const radius = pill ? "rounded-full" : "rounded";

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 ${radius} text-[11px] font-medium border ${current.badge} ${className}`}
      {...props}
    >
      {dot && (
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          {pulse && (
            <span
              className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-60 ${current.dot}`}
            />
          )}
          <span
            className={`relative inline-flex rounded-full h-1.5 w-1.5 ${current.dot}`}
          />
        </span>
      )}
      {children}
    </span>
  );
};
