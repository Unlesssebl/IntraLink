export type Status = 'new' | 'in_progress' | 'waiting' | 'resolved';
export type Priority = 'critical' | 'high' | 'medium' | 'low';
export type Category = 'network' | 'hardware' | 'software' | 'access' | 'email';
export type Page = 'queue';

export interface Operator {
  id: string;
  name: string;
  initials: string;
}

export interface TimelineEvent {
  id: string;
  type: 'created' | 'reply' | 'internal' | 'status_change' | 'assignment';
  author: string;
  content: string;
  timestamp: Date;
}

export interface Ticket {
  id: string;
  rawId: number;
  title: string;
  status: Status;
  statusId: number;
  statusName: string;
  priority: Priority;
  category: Category;
  serviceId?: number;
  serviceName: string;
  rootServiceId?: number;
  rootServiceName: string;
  servicePath?: string;
  assigneeId: string | null;
  requesterName: string;
  requesterLogin?: string;
  requesterPhone: string;
  host: string;
  ip: string;
  room?: string;
  department?: string;
  slaDeadline: Date;
  createdAt: Date;
  description: string;
  aiConfidence: number | null;
  aiSuggestion: string | null;
  timeline: TimelineEvent[];
  ruleType?: string;
  templateKey?: string;
  targetServiceName?: string;
  isRedirect?: boolean;
  targetStatusId?: number;
  targetStatusName?: string;
  isDuplicate?: boolean;
  duplicateInfo?: any;
  hasAttachments?: boolean;
  attachments?: Array<{ id: number; name: string; size?: number; url?: string; content_type?: string }>;
  expenses?: number;
  executors?: string;
  executorIds?: Array<number | string>;
}

export interface ToastMessage {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}

export const operators: Operator[] = [
  { id: '8664', name: 'Беликов Ален', initials: 'БА' },
  { id: '10502', name: 'Беликов Ален (assitant)', initials: 'БА' },
  { id: '1', name: 'Дежурный инженер 1-й линии', initials: 'ДЭ' },
];

export const savedFilters = [
  { id: 'all', name: 'Все заявки в очереди', type: 'all' },
  { id: 'new', name: 'Новые без исполнителя', type: 'new' },
  { id: 'duplicates', name: 'Дубликаты', type: 'duplicates' },
  { id: 'redirects', name: 'Редиректы в другие сервисы', type: 'redirects' },
  { id: 'repair', name: 'Каб. 112 (ремонт)', type: 'repair' },
  { id: 'wifi', name: 'Заявки на Wi-Fi', type: 'wifi' },
];

export const categoryLabel: Record<Category, string> = {
  network: 'Сеть',
  hardware: 'Оборудование',
  software: 'ПО',
  access: 'Доступ',
  email: 'Почта',
};

export const statusConfig: Record<Status, { label: string; className: string; dotClass: string }> = {
  new: {
    label: 'Новая',
    className: 'bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 border border-neutral-200/80 dark:border-neutral-700/80',
    dotClass: 'bg-blue-500',
  },
  in_progress: {
    label: 'В работе',
    className: 'bg-amber-50/70 dark:bg-amber-950/30 text-amber-800 dark:text-amber-300 border border-amber-200/60 dark:border-amber-800/40',
    dotClass: 'bg-amber-500',
  },
  waiting: {
    label: 'Ожидание',
    className: 'bg-purple-50/70 dark:bg-purple-950/30 text-purple-800 dark:text-purple-300 border border-purple-200/60 dark:border-purple-800/40',
    dotClass: 'bg-purple-400',
  },
  resolved: {
    label: 'Решена',
    className: 'bg-emerald-50/70 dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300 border border-emerald-200/60 dark:border-emerald-800/40',
    dotClass: 'bg-emerald-500',
  },
};

export const priorityConfig: Record<Priority, { label: string; className: string; dotClass: string; textClass: string }> = {
  critical: {
    label: 'Критичный',
    className: 'text-rose-700 dark:text-rose-400',
    dotClass: 'bg-rose-500',
    textClass: 'text-rose-700 dark:text-rose-400',
  },
  high: {
    label: 'Высокий',
    className: 'text-amber-700 dark:text-amber-400',
    dotClass: 'bg-amber-500',
    textClass: 'text-amber-700 dark:text-amber-400',
  },
  medium: {
    label: 'Средний',
    className: 'text-neutral-600 dark:text-neutral-400',
    dotClass: 'bg-neutral-400',
    textClass: 'text-neutral-600 dark:text-neutral-400',
  },
  low: {
    label: 'Низкий',
    className: 'text-neutral-400 dark:text-neutral-500',
    dotClass: 'bg-neutral-300 dark:bg-neutral-600',
    textClass: 'text-neutral-400 dark:text-neutral-500',
  },
};
