import { apiFetch } from './api';
import type { Ticket, Status, Priority, Category, TimelineEvent } from '../data/mock';
import type {
  TaskItem,
  TaskDetails,
  QueueResponse,
  HostDiagnostics,
  SingleApplyPayload,
  BulkApplyItemPayload,
  BulkApplyResponse,
  SmartBulkApplyItemPayload,
  TicketAIPlan,
} from './types';

export function mapStatusIdToStatus(statusId: number, statusName?: string): Status {
  if (statusId === 1 || (statusName && /новая/i.test(statusName))) return 'new';
  if (statusId === 2 || statusId === 27 || (statusName && /работе|выполнен/i.test(statusName))) return 'in_progress';
  if (statusId === 3 || statusId === 10 || statusId === 35 || statusId === 48 || (statusName && /ожидан|отложен|уточнен/i.test(statusName))) return 'waiting';
  if (statusId === 4 || statusId === 5 || statusId === 29 || statusId === 30 || (statusName && /решен|закрыт|отменен/i.test(statusName))) return 'resolved';
  return 'new';
}

export function mapStatusToStatusId(status: Status): number {
  switch (status) {
    case 'new': return 1;
    case 'in_progress': return 27;
    case 'waiting': return 35;
    case 'resolved': return 29;
  }
}

export function mapCategory(serviceName: string = ''): Category {
  const s = serviceName.toLowerCase();
  if (/сеть|vpn|доступ|диск|папк|учетн|ad|домен|wifi|wlan/i.test(s)) return 'access';
  if (/1с|по|программ|office|браузер|клиент|directum/i.test(s)) return 'software';
  if (/принтер|сканер|мфу|картридж|оргтехника|оборудован|монитор|клавиатур/i.test(s)) return 'hardware';
  if (/почт|outlook|письм|exchange/i.test(s)) return 'email';
  if (/интернет|коммутатор|wifi|сетев|маршрутизатор|ip/i.test(s)) return 'network';
  return 'software';
}

export function buildTicketAIPlan(task: TaskItem): TicketAIPlan {
  const ruleType = task.rule_type || '';
  const templateKey = task.template_key || '';
  const isRedirect = task.is_redirect || ruleType.startsWith('redirect') || templateKey.includes('redirect');
  const isDuplicate = (task as any).is_duplicate || ruleType === 'duplicate_task';
  const isWifi = ruleType === 'wlan_access' || templateKey === 'wifi_access' || /wi-?fi|wlan/i.test(task.name);
  const isRepair = ruleType === 'hardware_repair' || templateKey === 'hardware_repair';
  const isOffline = ruleType === 'pc_offline' || templateKey === 'pc_offline';
  const is1c = /1с|1c/i.test(task.name) || /1с|1c/i.test(task.service_name || '');
  const isPrinter = /принтер|мфу|печать/i.test(task.name) || /принтер|мфу/i.test(task.service_name || '');

  if (isWifi) {
    return {
      actionType: 'grant_wlan',
      actionBadge: 'Wi-Fi доступ',
      actionTitle: 'Выдача доступа к Wi-Fi в AD (WLAN-WORKNET)',
      targetStatusId: 29,
      targetStatusName: 'Выполнена',
      comment: task.suggested_comment || 'Доступ к беспроводной корпоративной сети WLAN-WORKNET успешно предоставлен. Используйте логин и пароль от вашей учетной записи на ПК.',
      expensesMinutes: task.expenses || 10,
      requiresDomainJob: true,
      domainJob: {
        action: 'grant_wlan',
        identity: task.creator_login || task.pc_name || '',
      },
      confidenceScore: 0.98,
      badgeClass: 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-300 dark:border-emerald-800',
    };
  }

  if (isDuplicate) {
    const masterId = (task as any).duplicate_info?.master_task_id || '';
    return {
      actionType: 'duplicate',
      actionBadge: masterId ? `Дубликат #${masterId}` : 'Дубликат',
      actionTitle: `Отмена дубликата (привязка к #${masterId || '...'})`,
      targetStatusId: 30,
      targetStatusName: 'Отменена',
      comment: task.suggested_comment || `Заявка отменена как повторная (дубликат инцидента #${masterId}). Все работы ведутся в основной заявке. По вопросам звоните на 49-87.`,
      expensesMinutes: task.expenses || 5,
      requiresDomainJob: false,
      confidenceScore: 0.99,
      badgeClass: 'bg-neutral-100 text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200 border-neutral-300 dark:border-neutral-700',
    };
  }

  if (isRedirect) {
    const targetSvc = task.target_service_name || 'соответствующий раздел';
    return {
      actionType: 'redirect',
      actionBadge: 'Редирект',
      actionTitle: `Перенаправление в «${targetSvc}»`,
      targetStatusId: 30,
      targetStatusName: 'Отменена',
      comment: task.suggested_comment || `Заявка отменена, т. к. создана не в подходящем разделе. Требуется оставить заявку в подходящем разделе: ${targetSvc}. По вопросам звоните на 49-87.`,
      expensesMinutes: task.expenses || 5,
      requiresDomainJob: false,
      confidenceScore: 0.95,
      badgeClass: 'bg-amber-50 text-amber-900 dark:bg-amber-950/60 dark:text-amber-200 border-amber-300 dark:border-amber-800',
    };
  }

  if (isRepair) {
    return {
      actionType: 'hardware_repair',
      actionBadge: 'В ремонт (Каб. 112)',
      actionTitle: 'Приглашение на диагностику в каб. 112',
      targetStatusId: 48,
      targetStatusName: 'Ожидание устройства',
      comment: task.suggested_comment || 'Приносите системный блок / ноутбук в АБК 3, 112 каб. на аппаратную диагностику и обслуживание.',
      expensesMinutes: task.expenses || 10,
      requiresDomainJob: false,
      confidenceScore: 0.94,
      badgeClass: 'bg-indigo-50 text-indigo-900 dark:bg-indigo-950/60 dark:text-indigo-200 border-indigo-300 dark:border-indigo-800',
    };
  }

  if (isOffline) {
    return {
      actionType: 'offline_host',
      actionBadge: 'Уточнить у заявителя',
      actionTitle: 'Запрос включения ПК у заявителя',
      targetStatusId: 35,
      targetStatusName: 'Требует уточнения',
      comment: task.suggested_comment || 'Не вижу ПК в сети.\n1. Убедитесь в корректности имени ПК;\n2. Перезагрузите компьютер;\n3. Проверьте подключение сетевого кабеля.\nПожалуйста, напишите в комментариях к заявке, когда ПК будет включен и доступен в сети.',
      expensesMinutes: task.expenses || 5,
      requiresDomainJob: false,
      confidenceScore: 0.92,
      badgeClass: 'bg-rose-50 text-rose-900 dark:bg-rose-950/60 dark:text-rose-200 border-rose-300 dark:border-rose-800',
    };
  }

  if (is1c) {
    return {
      actionType: 'clear_1c_cache',
      actionBadge: 'Очистка кэша 1С',
      actionTitle: 'Очистка кэша и сессий 1С на рабочей станции',
      targetStatusId: 27,
      targetStatusName: 'В работе',
      comment: task.suggested_comment || 'Ваша заявка принята в работу. Выполняется проверка информационной базы и очистка кэша 1С на рабочей станции.',
      expensesMinutes: task.expenses || 10,
      requiresDomainJob: false,
      confidenceScore: 0.90,
      badgeClass: 'bg-blue-50 text-blue-900 dark:bg-blue-950/60 dark:text-blue-200 border-blue-300 dark:border-blue-800',
    };
  }

  if (isPrinter) {
    return {
      actionType: 'install_printer',
      actionBadge: 'Настройка печати',
      actionTitle: 'Диагностика и настройка печати / МФУ',
      targetStatusId: 27,
      targetStatusName: 'В работе',
      comment: task.suggested_comment || 'Ваша заявка принята в работу. Выполняется проверка сетевого порта и очереди печати принтера на рабочей станции.',
      expensesMinutes: task.expenses || 10,
      requiresDomainJob: false,
      confidenceScore: 0.90,
      badgeClass: 'bg-purple-50 text-purple-900 dark:bg-purple-950/60 dark:text-purple-200 border-purple-300 dark:border-purple-800',
    };
  }

  const rawTargetName = task.target_status_name || 'В работу';
  const cleanTargetName = rawTargetName.replace(/\s*\(\d+\)/g, '').replace(/\s*[→—–-]\s*\d+/g, '').trim() || 'В работу';

  return {
    actionType: 'standard',
    actionBadge: cleanTargetName,
    actionTitle: `Перевод в статус «${cleanTargetName}»`,
    targetStatusId: task.target_status_id || 27,
    targetStatusName: cleanTargetName,
    comment: task.suggested_comment || 'Принято в работу специалистом 1-й линии техподдержки.',
    expensesMinutes: task.expenses || 10,
    requiresDomainJob: false,
    confidenceScore: 0.85,
    badgeClass: 'bg-neutral-100 text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200 border-neutral-300 dark:border-neutral-700',
  };
}

export function mapTaskToTicket(task: TaskItem): Ticket {
  const createdDate = task.created ? new Date(task.created) : new Date();
  const slaDeadline = new Date(createdDate.getTime() + 4 * 3600000);

  const timeline: TimelineEvent[] = [
    {
      id: `ev-created-${task.id}`,
      type: 'created',
      author: task.creator || 'Пользователь',
      content: `Создана заявка: ${task.name}`,
      timestamp: createdDate,
    },
  ];

  if (task.suggested_comment) {
    timeline.push({
      id: `ev-ai-${task.id}`,
      type: 'internal',
      author: 'AI Rule Engine',
      content: task.suggested_comment,
      timestamp: createdDate,
    });
  }

  let priority: Priority = 'medium';
  if (task.score >= 9) priority = 'critical';
  else if (task.score >= 7) priority = 'high';
  else if (task.score <= 3) priority = 'low';

  const aiPlan = buildTicketAIPlan(task);

  return {
    id: `HD-${task.id}`,
    rawId: task.id,
    title: task.name,
    status: mapStatusIdToStatus(task.status_id, task.status_name),
    statusId: task.status_id,
    statusName: task.status_name,
    priority,
    category: mapCategory(task.service_name || task.target_service_name),
    serviceId: task.service_id,
    serviceName: task.service_name || '1-я линия техподдержки',
    rootServiceId: task.root_service_id,
    rootServiceName: task.root_service_name || 'Общие вопросы',
    servicePath: task.service_path,
    assigneeId: null,
    requesterName: task.creator || 'Не указан',
    requesterLogin: task.creator_login,
    requesterPhone: task.phone || '',
    host: task.pc_name || '',
    ip: '',
    room: task.room,
    department: task.department,
    slaDeadline,
    createdAt: createdDate,
    description: task.description || 'Без описания',
    aiConfidence: task.score ? Math.round(task.score * 10) : null,
    aiSuggestion: task.suggested_comment || null,
    timeline,
    ruleType: task.rule_type,
    templateKey: task.template_key,
    targetServiceName: task.target_service_name,
    isRedirect: task.is_redirect,
    targetStatusId: task.target_status_id,
    targetStatusName: task.target_status_name,
    isDuplicate: (task as any).is_duplicate,
    duplicateInfo: (task as any).duplicate_info,
    hasAttachments: task.has_attachments,
    attachments: task.attachments,
    expenses: task.expenses || 10,
    executors: task.executors || '',
    executorIds: task.executor_ids || [],
    aiPlan,
  };
}

export async function fetchQueue(filterId = 984, limit = 50): Promise<{
  tickets: Ticket[];
  rawTasks: TaskItem[];
  total: number;
  rootServices: Array<{ id: number; name: string }>;
  subservicesByRoot: Record<number, Array<{ id: number; name: string; parent_id?: number }>>;
}> {
  const data = await apiFetch<any>(`/api/v1/triage/batch?filter_id=${filterId}&limit=${limit}`);
  const tasks = data.tasks || [];
  const normalizedTasks: TaskItem[] = tasks.map((item: any) => {
    if (item.task_id && item.suggested_action) {
      const t = item.task || {};
      const action = item.suggested_action || {};
      return {
        id: item.task_id,
        name: item.name || t.Name || '',
        description: t.Description || '',
        created: item.created || t.Created || '',
        creator: item.creator || t.Creator || '',
        creator_login: t.CreatorLogin || '',
        phone: item.creator_phone || '',
        pc_name: item.pc_name || '',
        room: item.room || '',
        department: t.Department || '',
        status_id: item.status_id || t.StatusId || 26,
        status_name: item.status_name || t.StatusName || 'Новая',
        service_id: item.service_id || t.ServiceId || 0,
        service_name: item.service_name || t.ServiceName || '',
        target_status_id: action.target_status_id || 27,
        target_status_name: action.target_status_name || 'В работе',
        suggested_comment: action.comment || '',
        rule_type: action.rule_type || '',
        template_key: action.template_key || '',
        target_service_name: action.target_service_name || '',
        is_redirect: action.is_redirect || false,
        has_attachments: item.has_attachments || false,
        attachments: t._attachments_list || [],
        expenses: action.expenses || 10,
        score: action.score || 8,
        is_duplicate: item.is_duplicate || false,
        duplicate_info: item.duplicate_info || null,
        telemetry: item.telemetry || null,
        circuit: item.circuit || 'green',
      } as any;
    }
    return item;
  });

  const tickets = normalizedTasks.map(mapTaskToTicket);
  return {
    tickets,
    rawTasks: normalizedTasks,
    total: data.total_open || tickets.length,
    rootServices: [],
    subservicesByRoot: {},
  };
}

export async function fetchDiagnostics(host: string): Promise<HostDiagnostics> {
  if (!host) throw new Error('Хост не указан');
  return apiFetch<HostDiagnostics>(`/admin/api/diag/${encodeURIComponent(host)}`);
}

export async function fetchTaskDetails(taskId: number): Promise<TaskDetails> {
  const data = await apiFetch<any>(`/api/v1/triage/tasks/${taskId}`);
  const task = data.task || {};
  const history = data.history || [];
  return {
    task,
    comments: history,
    attachments: task._attachments_list || [],
    ai_suggested_resolution: data.ai_suggested_resolution,
    kb_matches: data.kb_matches,
    telemetry: data.telemetry,
    ...data,
  } as TaskDetails;
}

export async function applyTask(taskId: number, payload: SingleApplyPayload): Promise<any> {
  return apiFetch('/api/v1/triage/apply', {
    method: 'POST',
    body: JSON.stringify({
      task_ids: [taskId],
      status_id: payload.status_id,
      comment: payload.comment || '',
      expenses: payload.minutes || 10,
      executor_ids: payload.executor_ids,
    }),
  });
}

export async function bulkApplyTasks(tasks: BulkApplyItemPayload[]): Promise<BulkApplyResponse> {
  const taskIds = tasks.map(t => t.task_id);
  if (tasks.length > 0 && tasks.every(t => t.status_id === tasks[0].status_id && t.comment === tasks[0].comment)) {
    const res = await apiFetch<any>('/api/v1/triage/apply', {
      method: 'POST',
      body: JSON.stringify({
        task_ids: taskIds,
        status_id: tasks[0].status_id,
        comment: tasks[0].comment || '',
        expenses: tasks[0].minutes || 10,
        executor_ids: tasks[0].executor_ids,
      }),
    });
    const results = res.results || [];
    return {
      total: tasks.length,
      success_count: results.filter((r: any) => r.status === 'success').length,
      failed_count: results.filter((r: any) => r.status !== 'success').length,
      applied: results.filter((r: any) => r.status === 'success'),
      failed: results.filter((r: any) => r.status !== 'success'),
    };
  }

  const applied: any[] = [];
  const failed: any[] = [];
  for (const item of tasks) {
    try {
      const res = await apiFetch<any>('/api/v1/triage/apply', {
        method: 'POST',
        body: JSON.stringify({
          task_ids: [item.task_id],
          status_id: item.status_id,
          comment: item.comment || '',
          expenses: item.minutes || 10,
          executor_ids: item.executor_ids,
        }),
      });
      applied.push({ task_id: item.task_id, res });
    } catch (err: any) {
      failed.push({ task_id: item.task_id, error: err.message });
    }
  }
  return {
    total: tasks.length,
    success_count: applied.length,
    failed_count: failed.length,
    applied,
    failed,
  };
}

export async function fetchTemplatesCatalog(): Promise<{ templates: any[]; map: Record<string, any> }> {
  try {
    const res = await apiFetch<any>('/api/v1/rules-admin/templates-catalog');
    if (res && res.templates) return res;
  } catch {
    // Fallback to /api/v1/rules-admin/templates
  }
  try {
    const data = await apiFetch<any[]>('/api/v1/rules-admin/templates');
    const rawList = Array.isArray(data) ? data : ((data as any)?.templates || []);
    const templates = rawList.map((t: any) => ({
      ...t,
      template: t.template_text || t.template || '',
    }));
    const map: Record<string, any> = {};
    templates.forEach((t: any) => {
      map[t.key] = t;
    });
    return { templates, map };
  } catch {
    return { templates: [], map: {} };
  }
}

// ---------------------------------------------------------------------------
// Execution Broker (Windows Domain RPC)
// ---------------------------------------------------------------------------

export async function enqueueExecution(payload: {
  action: string;
  task_id?: number;
  params?: Record<string, any>;
  auto_close_ticket?: boolean;
}): Promise<{ status: string; job_id: string; action: string; task_id?: number }> {
  return apiFetch('/api/v1/commands', {
    method: 'POST',
    body: JSON.stringify({
      type: payload.action,
      target: { task_id: payload.task_id },
      params: payload.params || {},
      auto_close_ticket: payload.auto_close_ticket ?? true,
      source: 'web',
    }),
  });
}

export async function submitCommand(payload: {
  type: string;
  target?: Record<string, any>;
  params?: Record<string, any>;
  mode?: 'auto' | 'confirm' | 'dry_run';
  priority?: number;
  idempotency_key?: string;
  auto_close_ticket?: boolean;
}): Promise<{ status: string; job_id: string; command_type: string; task_id?: number }> {
  return apiFetch('/api/v1/commands', {
    method: 'POST',
    body: JSON.stringify({
      type: payload.type,
      target: payload.target || {},
      params: payload.params || {},
      mode: payload.mode || 'auto',
      priority: payload.priority || 5,
      idempotency_key: payload.idempotency_key,
      auto_close_ticket: payload.auto_close_ticket ?? true,
      source: 'web',
    }),
  });
}

export async function confirmCommand(
  jobId: string,
  decision: 'approve' | 'reject',
  reason?: string
): Promise<{ status: string; job_id: string; decision: string }> {
  return apiFetch(`/api/v1/commands/${jobId}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ decision, reason }),
  });
}

export async function cancelCommand(
  jobId: string,
  reason?: string
): Promise<{ status: string; job_id: string }> {
  return apiFetch(`/api/v1/commands/${jobId}/cancel?reason=${encodeURIComponent(reason || 'Отменено')}`, {
    method: 'POST',
  });
}

export async function getExecutionJobStatus(jobId: string): Promise<{
  job_id: string;
  action?: string;
  command_type?: string;
  task_id?: number;
  status: 'queued' | 'running' | 'confirm_required' | 'success' | 'failed' | 'cancelled';
  error_message?: string;
  result?: any;
}> {
  return apiFetch(`/api/v1/commands/${jobId}`);
}

export function streamJobEvents(
  jobId: string,
  callbacks: {
    onProgress?: (data: { phase: string; pct: number; detail?: string }) => void;
    onConfirmRequired?: (data: { prompt: string; details: any }) => void;
    onResult?: (data: { status: string; message?: string; data?: any }) => void;
    onError?: (err: any) => void;
  }
): () => void {
  const es = new EventSource(`/api/v1/events/stream?job_id=${jobId}`);

  es.addEventListener('progress', (e) => {
    try {
      const parsed = JSON.parse(e.data);
      if (callbacks.onProgress) callbacks.onProgress(parsed.data || parsed);
    } catch {}
  });

  es.addEventListener('confirm_required', (e) => {
    try {
      const parsed = JSON.parse(e.data);
      if (callbacks.onConfirmRequired) callbacks.onConfirmRequired(parsed.data || parsed);
    } catch {}
  });

  es.addEventListener('result', (e) => {
    try {
      const parsed = JSON.parse(e.data);
      if (callbacks.onResult) callbacks.onResult(parsed.data || parsed);
      es.close();
    } catch {}
  });

  es.addEventListener('error', (err) => {
    if (callbacks.onError) callbacks.onError(err);
  });

  return () => {
    es.close();
  };
}

export async function pollExecutionJob(
  jobId: string,
  maxWaitMs = 25000,
  intervalMs = 1000
): Promise<{
  job_id: string;
  action: string;
  task_id: number;
  status: 'success' | 'failed';
  error_message?: string;
  result?: any;
}> {
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    const job = await getExecutionJobStatus(jobId);
    if (job.status === 'success') {
      return job as any;
    }
    if (job.status === 'failed' || job.status === 'cancelled') {
      throw new Error(job.error_message || 'Ошибка или отмена при выполнении действия');
    }
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
  throw new Error('Таймаут ожидания выполнения задачи в домене. Задача осталась в очереди воркера.');
}


// ---------------------------------------------------------------------------
// Settings & System Health Management
// ---------------------------------------------------------------------------

export async function fetchSystemStatus(): Promise<{
  status: 'healthy' | 'degraded' | 'unhealthy';
  intraservice_connected: boolean;
  circuit_breaker_state: 'CLOSED' | 'OPEN' | 'HALF_OPEN';
  service_user_configured: boolean;
  service_user_login?: string;
  redis_connected: boolean;
  db_connected: boolean;
  worker_running: boolean;
  catalog_services_count?: number;
}> {
  return apiFetch('/admin/api/status');
}

export async function fetchDomainAuth(): Promise<{ is_configured: boolean; username: string | null }> {
  return apiFetch('/admin/api/domain-auth');
}

export async function saveDomainAuth(payload: { username: string; password?: string }): Promise<{ status: string }> {
  return apiFetch('/admin/api/domain-auth', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchTelegramUsers(): Promise<{ users: Array<{ tg_user_id: number; username?: string; full_name?: string; is_active: boolean }> }> {
  return apiFetch('/admin/api/users');
}

export async function addTelegramUser(payload: { tg_user_id: number; username?: string; full_name?: string }): Promise<{ status: string }> {
  return apiFetch('/admin/api/users/add', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function toggleTelegramUser(tgUserId: number): Promise<{ status: string; is_active: boolean }> {
  return apiFetch(`/admin/api/users/${tgUserId}/toggle`, {
    method: 'POST',
  });
}

export async function deleteTelegramUser(tgUserId: number): Promise<{ status: string }> {
  return apiFetch(`/admin/api/users/${tgUserId}`, {
    method: 'DELETE',
  });
}

export async function restartWorkerService(): Promise<{ status: string }> {
  return apiFetch('/admin/api/worker/restart', {
    method: 'POST',
  });
}

export async function smartBulkApplyTasks(
  items: SmartBulkApplyItemPayload[],
  onProgress?: (completed: number, total: number, currentTicket: number) => void
): Promise<{ success_count: number; failed_count: number; errors: Array<{ task_id: number; error: string }> }> {
  let success_count = 0;
  let failed_count = 0;
  const errors: Array<{ task_id: number; error: string }> = [];

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (onProgress) onProgress(i, items.length, item.task_id);

    try {
      // 1. Если требуется доменное исполнение (например, grant_wlan)
      if (item.requires_domain_job && item.domain_job) {
        const job = await enqueueExecution({
          action: item.domain_job.action,
          task_id: item.task_id,
          params: item.domain_job.params || { username: item.domain_job.identity },
          auto_close_ticket: false,
        });
        await pollExecutionJob(job.job_id, 15000, 1000);
      }

      // 2. Применяем решение к заявке в IntraService
      await applyTask(item.task_id, {
        status_id: item.status_id,
        comment: item.comment,
        minutes: item.minutes,
        executor_ids: item.executor_ids || '8664,10502',
        is_private: item.is_private || false,
      });

      success_count++;
    } catch (err: any) {
      failed_count++;
      errors.push({ task_id: item.task_id, error: err.message || String(err) });
    }
  }

  if (onProgress) onProgress(items.length, items.length, 0);
  return { success_count, failed_count, errors };
}


