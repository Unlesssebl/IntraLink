/**
 * IntraService Canonical Status System (SSOT)
 * Single Source of Truth for Status IDs, names, color tokens, and badge variants.
 */

export type StatusBadgeVariant = "neutral" | "warning" | "success" | "danger" | "info";

export interface StatusMeta {
  id: number;
  name: string;
  dotColor: string;
  variant: StatusBadgeVariant;
  pulse?: boolean;
}

export const STATUS_MAP: Record<number, StatusMeta> = {
  1: {
    id: 1,
    name: "Новая",
    dotColor: "bg-blue-500",
    variant: "info",
    pulse: true,
  },
  2: {
    id: 2,
    name: "В работе",
    dotColor: "bg-amber-500",
    variant: "warning",
    pulse: false,
  },
  3: {
    id: 3,
    name: "Выполнена",
    dotColor: "bg-emerald-500",
    variant: "success",
    pulse: false,
  },
  4: {
    id: 4,
    name: "Закрыта",
    dotColor: "bg-neutral-500",
    variant: "neutral",
    pulse: false,
  },
  5: {
    id: 5,
    name: "Отклонена",
    dotColor: "bg-rose-500",
    variant: "danger",
    pulse: false,
  },
  6: {
    id: 6,
    name: "Приостановлена",
    dotColor: "bg-neutral-400",
    variant: "neutral",
    pulse: false,
  },
  7: {
    id: 7,
    name: "Переоткрыта",
    dotColor: "bg-amber-500",
    variant: "warning",
    pulse: true,
  },
  30: {
    id: 30,
    name: "Отменена",
    dotColor: "bg-rose-500",
    variant: "danger",
    pulse: false,
  },
};

const DEFAULT_STATUS_META: StatusMeta = {
  id: 0,
  name: "Неизвестно",
  dotColor: "bg-neutral-400",
  variant: "neutral",
  pulse: false,
};

/**
 * Return resolved status metadata by status ID and optional name.
 * Always returns consistent human-readable name, badge variant, and dot color.
 */
export function getStatusMeta(
  statusId?: number | null,
  statusName?: string | null
): StatusMeta {
  if (statusId && STATUS_MAP[statusId]) {
    const meta = STATUS_MAP[statusId];
    return {
      ...meta,
      name: statusName?.trim() || meta.name,
    };
  }

  // Fallback by name substring if statusId is missing or unknown
  if (statusName) {
    const norm = statusName.toLowerCase();
    if (norm.includes("нов")) return { ...STATUS_MAP[1], name: statusName };
    if (norm.includes("работ")) return { ...STATUS_MAP[2], name: statusName };
    if (norm.includes("выполн") || norm.includes("решен")) return { ...STATUS_MAP[3], name: statusName };
    if (norm.includes("закр")) return { ...STATUS_MAP[4], name: statusName };
    if (norm.includes("отклон")) return { ...STATUS_MAP[5], name: statusName };
    if (norm.includes("приостан") || norm.includes("ожид")) return { ...STATUS_MAP[6], name: statusName };
    if (norm.includes("переоткр")) return { ...STATUS_MAP[7], name: statusName };
    if (norm.includes("отмен")) return { ...STATUS_MAP[30], name: statusName };
  }

  return {
    ...DEFAULT_STATUS_META,
    name: statusName?.trim() || (statusId ? `Статус #${statusId}` : "Неизвестно"),
  };
}

export function isStatusInWork(statusId?: number | null): boolean {
  return statusId === 2;
}

export function isStatusResolved(statusId?: number | null): boolean {
  return statusId === 3 || statusId === 4;
}

export function isStatusCancelled(statusId?: number | null): boolean {
  return statusId === 30 || statusId === 5;
}
