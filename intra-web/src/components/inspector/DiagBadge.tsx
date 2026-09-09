import type { DiagStatus } from '../../lib/types';

interface DiagBadgeProps {
  status: DiagStatus;
  label?: string;
}

export default function DiagBadge({ status, label }: DiagBadgeProps) {
  const cls = {
    ok: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/80',
    fail: 'bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border-rose-200 dark:border-rose-800/80',
    checking: 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200 dark:border-amber-800/80 animate-pulse',
    idle: 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700',
  }[status];

  const defaultLabel = { ok: 'ONLINE', fail: 'OFFLINE', checking: '...', idle: '—' }[status];

  return (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border font-semibold inline-block ${cls}`}>
      {label || defaultLabel}
    </span>
  );
}
