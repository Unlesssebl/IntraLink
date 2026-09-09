import React from 'react';
import {
  IconBolt,
  IconRefresh,
  IconLock,
  IconRedirect,
  IconSparkles,
  IconPrinter,
  IconClock,
} from '../Icons';

export interface SmartAction {
  id: string;
  label: string;
  icon: React.ReactNode;
  hint?: string;
  onClick: () => void;
}

interface SmartActionBarProps {
  actions: SmartAction[];
  className?: string;
}

export default function SmartActionBar({ actions, className = '' }: SmartActionBarProps) {
  if (!actions || actions.length === 0) return null;

  return (
    <div className={`flex items-center gap-1.5 px-3 py-1.5 bg-neutral-100/80 dark:bg-neutral-800/60 border-b border-neutral-200 dark:border-neutral-700 overflow-x-auto text-xs ${className}`}>
      <span className="text-neutral-400 text-[10px] uppercase font-bold tracking-wider shrink-0 mr-1 flex items-center gap-1">
        <IconBolt size={11} className="text-amber-500" />
        Быстрые действия:
      </span>

      {actions.map((action) => (
        <button
          key={action.id}
          type="button"
          onClick={action.onClick}
          title={action.hint || action.label}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 text-neutral-800 dark:text-neutral-200 font-medium hover:bg-neutral-50 dark:hover:bg-neutral-800 shadow-2xs transition-colors shrink-0 cursor-pointer text-[11px]"
        >
          {action.icon}
          <span>{action.label}</span>
        </button>
      ))}
    </div>
  );
}
