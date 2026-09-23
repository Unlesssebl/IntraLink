import React from "react";

export interface CardProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  noPadding?: boolean;
}

export const Card: React.FC<CardProps> = ({
  title,
  subtitle,
  action,
  noPadding = false,
  children,
  className = "",
  ...props
}) => {
  return (
    <div
      className={`bg-[#12151c] border border-[#232836] rounded-lg overflow-hidden ${className}`}
      {...props}
    >
      {(title || action) && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#1f2430]">
          <div>
            {typeof title === "string" ? (
              <h4 className="text-xs font-semibold text-slate-200 tracking-tight">
                {title}
              </h4>
            ) : (
              title
            )}
            {subtitle && (
              <div className="text-[11px] text-slate-400 mt-0.5">{subtitle}</div>
            )}
          </div>
          {action && <div className="flex items-center gap-1.5">{action}</div>}
        </div>
      )}
      <div className={noPadding ? "" : "p-4"}>{children}</div>
    </div>
  );
};
