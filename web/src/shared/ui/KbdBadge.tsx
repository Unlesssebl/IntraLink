import React from "react";

export interface KbdBadgeProps extends React.HTMLAttributes<HTMLElement> {
  children?: React.ReactNode;
  shortcut?: string;
  size?: "xs" | "sm";
}

export const KbdBadge: React.FC<KbdBadgeProps> = ({
  children,
  shortcut,
  size = "xs",
  className = "",
  ...props
}) => {
  const content = children || shortcut;
  const sizeClasses = size === "xs" ? "text-[10px] px-1.5 py-0.5" : "text-xs px-2 py-0.5";

  return (
    <kbd
      className={`inline-flex items-center justify-center font-mono font-semibold rounded bg-neutral-900 text-neutral-300 border border-neutral-750 shadow-xs select-none ${sizeClasses} ${className}`}
      {...props}
    >
      {content}
    </kbd>
  );
};
