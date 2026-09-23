import React from "react";
import { Loader2 } from "lucide-react";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost" | "outline";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  variant = "secondary",
  size = "md",
  loading = false,
  icon,
  children,
  className = "",
  disabled,
  ...props
}) => {
  const baseClasses =
    "inline-flex items-center justify-center font-medium transition-colors duration-150 focus:outline-none focus:ring-1 focus:ring-neutral-400 disabled:opacity-50 disabled:cursor-not-allowed select-none rounded";

  const sizeClasses: Record<ButtonSize, string> = {
    sm: "px-2.5 py-1 text-xs gap-1.5",
    md: "px-3 py-1.5 text-xs gap-2",
    lg: "px-4 py-2 text-sm gap-2",
  };

  const variantClasses: Record<ButtonVariant, string> = {
    primary:
      "bg-neutral-100 hover:bg-white text-neutral-950 font-medium shadow-xs border border-neutral-200",
    secondary:
      "bg-neutral-900/70 hover:bg-neutral-800 text-neutral-200 hover:text-white border border-neutral-750 hover:border-neutral-600",
    danger:
      "bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 hover:text-rose-100 border border-rose-800/60",
    ghost:
      "bg-transparent hover:bg-neutral-800/60 text-neutral-400 hover:text-neutral-200",
    outline:
      "bg-transparent hover:bg-neutral-800/50 text-neutral-300 hover:text-white border border-neutral-700 hover:border-neutral-500",
  };

  return (
    <button
      className={`${baseClasses} ${sizeClasses[size]} ${variantClasses[variant]} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
      ) : (
        icon && <span className="shrink-0">{icon}</span>
      )}
      {children}
    </button>
  );
};
