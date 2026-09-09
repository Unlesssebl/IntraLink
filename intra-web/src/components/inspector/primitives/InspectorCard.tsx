import React, { type ReactNode } from 'react';

interface InspectorCardProps {
  children: ReactNode;
  className?: string;
  variant?: 'white' | 'subtle';
  onClick?: () => void;
}

export default function InspectorCard({
  children,
  className = '',
  variant = 'white',
  onClick,
}: InspectorCardProps) {
  const bgClass =
    variant === 'white'
      ? 'bg-white dark:bg-neutral-900 border-neutral-200/90 dark:border-neutral-800 shadow-xs'
      : 'bg-neutral-50/50 dark:bg-neutral-950/40 border-neutral-200/80 dark:border-neutral-800';

  return (
    <div
      onClick={onClick}
      className={`rounded-2xl border ${bgClass} p-3.5 space-y-3 transition-colors ${className}`}
    >
      {children}
    </div>
  );
}
