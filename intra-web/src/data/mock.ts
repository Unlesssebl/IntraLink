export type Status = 'new' | 'in_progress' | 'waiting' | 'resolved';
export type Priority = 'critical' | 'high' | 'medium' | 'low';
export type Category = 'network' | 'hardware' | 'software' | 'access' | 'email';
export type Page = 'queue' | 'settings' | 'timeline';


export interface TimelineEvent {
  id: string;
  type: 'created' | 'reply' | 'internal' | 'status_change' | 'assignment';
  author: string;
  content: string;
  timestamp: Date;
}

import type { AnalysisState, DecisionEnvelope, TicketAIPlan } from '../lib/types';

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
  aiPlan?: TicketAIPlan;
  circuit?: 'red' | 'yellow' | 'green';
  hasKbMatches?: boolean;
  hasAiSolution?: boolean;
  hasRuleEngine?: boolean;
  // Сценарный пайплайн и классификация очереди
  isProcessed?: boolean;
  scenarioKey?: string;
  envelope?: DecisionEnvelope | null;
  analysis?: AnalysisState;
}

export interface ScenarioBadgeConfig {
  label: string;
  badgeClass: string;
}

export const scenarioBadgeConfigs: Record<string, ScenarioBadgeConfig> = {
  create_user: {
    label: 'Учетная запись',
    badgeClass: 'bg-blue-50/90 dark:bg-blue-950/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-900',
  },
  user_creation: {
    label: 'Учетная запись',
    badgeClass: 'bg-blue-50/90 dark:bg-blue-950/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-900',
  },
  install_printer: {
    label: 'Принтер / МФУ',
    badgeClass: 'bg-purple-50/90 dark:bg-purple-950/40 text-purple-800 dark:text-purple-300 border-purple-200 dark:border-purple-900',
  },
  printer_installation: {
    label: 'Принтер / МФУ',
    badgeClass: 'bg-purple-50/90 dark:bg-purple-950/40 text-purple-800 dark:text-purple-300 border-purple-200 dark:border-purple-900',
  },
  grant_wlan: {
    label: 'Wi-Fi доступ',
    badgeClass: 'bg-emerald-50/90 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',
  },
  wlan_access: {
    label: 'Wi-Fi доступ',
    badgeClass: 'bg-emerald-50/90 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',
  },
  redirect: {
    label: 'Перенаправление',
    badgeClass: 'bg-amber-50/90 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  offline_host: {
    label: 'Хост офлайн',
    badgeClass: 'bg-rose-50/90 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-900',
  },
  physical_device: {
    label: 'Каб. 112 (Ремонт)',
    badgeClass: 'bg-indigo-50/90 dark:bg-indigo-950/40 text-indigo-800 dark:text-indigo-300 border-indigo-200 dark:border-indigo-900',
  },
  hardware_repair: {
    label: 'Каб. 112 (Ремонт)',
    badgeClass: 'bg-indigo-50/90 dark:bg-indigo-950/40 text-indigo-800 dark:text-indigo-300 border-indigo-200 dark:border-indigo-900',
  },
  file_lock: {
    label: 'Блокировка файла',
    badgeClass: 'bg-amber-50/90 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  rag_consultation: {
    label: 'База знаний (RAG)',
    badgeClass: 'bg-cyan-50/90 dark:bg-cyan-950/40 text-cyan-800 dark:text-cyan-300 border-cyan-200 dark:border-cyan-900',
  },
  duplicate_task: {
    label: 'Дубликат',
    badgeClass: 'bg-neutral-100 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border-neutral-300 dark:border-neutral-700',
  },
  duplicate: {
    label: 'Дубликат',
    badgeClass: 'bg-neutral-100 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border-neutral-300 dark:border-neutral-700',
  },
  consultation: {
    label: 'Консультация',
    badgeClass: 'bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700',
  },
};

export interface ToastMessage {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}


export const getStatusDotClass = (statusIdOrName: number | Status | string): string => {
  if (typeof statusIdOrName === 'number') {
    switch (statusIdOrName) {
      case 31: return 'bg-blue-500'; // Открыта (начальный статус IntraService)
      case 26: return 'bg-blue-500'; // Совместимость со старыми данными
      case 27: return 'bg-cyan-500'; // В работе (Бирюзовый/Cyan)
      case 35:
      case 36:
      case 37:
      case 48: return 'bg-amber-500'; // Ожидание (Оранжевый)
      case 29: return 'bg-emerald-500'; // Выполнена (Зеленый)
      case 28:
      case 30: return 'bg-neutral-500 dark:bg-neutral-400'; // Закрыта / Отменена
      default: return 'bg-blue-500';
    }
  }
  if (statusIdOrName === 'new') return 'bg-blue-500';
  if (statusIdOrName === 'in_progress') return 'bg-cyan-500';
  if (statusIdOrName === 'waiting') return 'bg-amber-500';
  if (statusIdOrName === 'resolved') return 'bg-emerald-500';
  return 'bg-blue-500';
};

export const statusConfig: Record<Status, { label: string; className: string; dotClass: string }> = {
  new: {
    label: 'Открыта',
    className: 'bg-blue-50/90 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300 border-blue-200/80 dark:border-blue-800/60',
    dotClass: 'bg-blue-500',
  },
  in_progress: {
    label: 'В работе',
    className: 'bg-cyan-50/90 text-cyan-800 dark:bg-cyan-950/60 dark:text-cyan-300 border-cyan-200/80 dark:border-cyan-800/60',
    dotClass: 'bg-cyan-500',
  },
  waiting: {
    label: 'Ожидание',
    className: 'bg-amber-50/90 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200/80 dark:border-amber-800/60',
    dotClass: 'bg-amber-500',
  },
  resolved: {
    label: 'Выполнена',
    className: 'bg-emerald-50/90 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200/80 dark:border-emerald-800/60',
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
