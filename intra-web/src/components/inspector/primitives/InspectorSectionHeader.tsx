import React, { type ReactNode } from 'react';

interface InspectorSectionHeaderProps {
  title: string;
  icon?: ReactNode;
  iconBgClass?: string;
  badge?: ReactNode;
  rightAction?: ReactNode;
  className?: string;
}

export default function InspectorSectionHeader({
  title,
  icon,
  iconBgClass = 'bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400',
  badge,
  rightAction,
  className = '',
}: InspectorSectionHeaderProps) {
  return (
    <div className={`flex items-center justify-between gap-2.5 pb-0.5 ${className}`}>
      <div className="flex items-center gap-2 min-w-0">
        {icon && (
          <span
            className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${iconBgClass}`}
          >
            {icon}
          </span>
        )}
        <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-neutral-600 dark:text-neutral-300 truncate">
          {title}
        </span>
      </div>

      <div className="flex items-center gap-1.5 shrink-0">
        {badge}
        {rightAction}
      </div>
    </div>
  );
}
