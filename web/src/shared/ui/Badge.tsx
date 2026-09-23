import React from "react";

export type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "accent" | "purple";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  dot?: boolean;
  pulse?: boolean;
}

export const Badge: React.FC<BadgeProps> = ({
  variant = "neutral",
  dot = false,
  pulse = false,
  children,
  className = "",
  ...props
}) => {
  const variantStyles = {
    neutral: {
      badge: "bg-slate-900/80 text-slate-300 border-slate-800",
      dot: "bg-slate-400",
    },
    success: {
      badge: "bg-emerald-950/40 text-emerald-300 border-emerald-800/50",
      dot: "bg-emerald-400",
    },
    warning: {
      badge: "bg-amber-950/40 text-amber-300 border-amber-800/50",
      dot: "bg-amber-400",
    },
    danger: {
      badge: "bg-red-950/40 text-red-300 border-red-800/50",
      dot: "bg-red-400",
    },
    accent: {
      badge: "bg-indigo-950/50 text-indigo-300 border-indigo-800/50",
      dot: "bg-indigo-400",
    },
    purple: {
      badge: "bg-purple-950/50 text-purple-300 border-purple-800/50",
      dot: "bg-purple-400",
    },
  };

  const current = variantStyles[variant];

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border ${current.badge} ${className}`}
      {...props}
    >
      {dot && (
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          {pulse && (
            <span
              className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${current.dot}`}
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
