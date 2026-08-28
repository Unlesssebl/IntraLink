import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiFetch } from '../api';
import { useToastStore } from './toast';
import { useSound } from '../composables/useSound';
import type {
  TaskItem,
  TaskDetails,
  TaskTemplate,
  TemplatesResponse,
  QueueResponse,
  SingleApplyPayload,
  BulkApplyRequest,
  BulkApplyResponse,
  HostDiagnostics,
  ServiceTabInfo,
} from '../types/task';

export type SortColumn = 'id' | 'score' | 'creator' | 'service' | 'pc_name' | 'created';
export type SortDirection = 'asc' | 'desc';
export type ConfidenceFilter = 'all' | 'high' | 'medium' | 'low';
export type HostFilter = 'all' | 'online' | 'offline';
export type ViewDensity = 'comfortable' | 'compact';

// Список 17 канонических корневых сервисов IntraService
export const INTRASERVICE_ROOT_SERVICES = [
  { id: 42, num: '01', name: '01. Учетные записи пользователей', short_name: '01. Учетные записи', icon: 'user' },
  { id: 18, num: '02', name: '02. Установка и настройка программ', short_name: '02. Настройка ПО', icon: 'tool' },
  { id: 19, num: '03', name: '03. Установка и обслуживание оргтехники', short_name: '03. Оргтехника и ПК', icon: 'printer' },
  { id: 20, num: '04', name: '04. Проблемы с сетью и интернетом', short_name: '04. Сеть и Wi-Fi', icon: 'wifi' },
  { id: 23, num: '05', name: '05. Вопросы по DIRECTUM, B2B', short_name: '05. Directum / B2B', icon: 'file' },
  { id: 15, num: '06', name: '06. Вопросы по 1С', short_name: '06. 1C:Предприятие', icon: 'chart' },
  { id: 60, num: '07', name: '07. Вопросы по терминалу сбора данных', short_name: '07. ТСД', icon: 'tool' },
  { id: 72, num: '08', name: '08. Информационная безопасность', short_name: '08. Инфобез (ИБ)', icon: 'shield' },
  { id: 24, num: '09', name: '09. Электронная цифровая подпись', short_name: '09. ЭЦП и СБИС', icon: 'shield' },
  { id: 64, num: '10', name: '10. Телефония', short_name: '10. Телефония', icon: 'headphones' },
  { id: 16, num: '11', name: '11. Общие вопросы', short_name: '11. Общие вопросы', icon: 'folder' },
  { id: 125, num: '12', name: '12. Архив КТД', short_name: '12. Архив КТД', icon: 'folder' },
  { id: 136, num: '14', name: '14. MDC системы', short_name: '14. MDC системы', icon: 'chart' },
  { id: 144, num: '15', name: '15. Вопросы по HelpDesk', short_name: '15. HelpDesk', icon: 'headphones' },
  { id: 188, num: '16', name: '16. Вопросы по IPS PDM\\PLM', short_name: '16. IPS PDM/PLM', icon: 'file' },
  { id: 189, num: '17', name: '17. АО «СКС«ТЭМПО»', short_name: '17. СКС «ТЭМПО»', icon: 'tool' },
];

export const useQueueStore = defineStore('queue', () => {
  const toast = useToastStore();
  const sound = useSound();

  const filterId = ref<number>(984);
  const tasks = ref<TaskItem[]>([]);
  const loading = ref<boolean>(false);
  const selectedTaskIds = ref<Set<number>>(new Set());
  const submittingIds = ref<Set<number>>(new Set());
  const doneIds = ref<Set<number>>(new Set());
  const activeDrawerTaskId = ref<number | null>(null);
  const drawerTaskDetails = ref<TaskDetails | null>(null);
  const drawerLoading = ref<boolean>(false);

  // Каталог корпоративных шаблонов
  const templates = ref<TaskTemplate[]>([]);
  const templatesMap = ref<Record<string, TaskTemplate>>({});
  const templatesLoaded = ref<boolean>(false);

  // Кэш пингов хостов
  const hostStatusMap = ref<Record<string, HostDiagnostics>>({});

  // Фильтры и навигация
  const aiSolutionFilter = ref<AISolutionFilter>('all');
  const activeServiceTab = ref<string>('all');
  const activeSubServiceTab = ref<string>('all');
  const searchQuery = ref<string>('');
  const viewMode = ref<'cards' | 'grid'>('cards');
  const density = ref<ViewDensity>('comfortable');

  // Каталог подсервисов по корневым сервисам (полученный с бэкенда)
  const subservicesByRoot = ref<Record<number, Array<{ id: number; name: string; parent_id?: number }>>>({});

  // Расширенные фильтры
  const confidenceFilter = ref<ConfidenceFilter>('all');
  const hostFilter = ref<HostFilter>('all');
  const hasAttachmentsOnly = ref<boolean>(false);
  const redirectOnly = ref<boolean>(false);

  // Сортировка
  const sortBy = ref<SortColumn>('id');
  const sortDirection = ref<SortDirection>('desc');

  // Хранилище предыдущих ID задач для детекции новых инцидентов
  const knownTaskIds = ref<Set<number>>(new Set());

  // Загрузка шаблонов
  const fetchTemplates = async () => {
    if (templatesLoaded.value) return;
    try {
      const data = await apiFetch<TemplatesResponse>('/admin/api/templates');
      if (data && data.templates) {
        templates.value = data.templates;
        templatesMap.value = data.map || {};
        templatesLoaded.value = true;
      }
    } catch (e) {
      console.error('Ошибка загрузки каталога шаблонов:', e);
    }
  };

  // Фоновая диагностика хостов
  const triggerHostDiagnostics = async (taskList: TaskItem[]) => {
    const hostsToCheck = new Set<string>();
    taskList.forEach(t => {
      if (t.pc_name && t.pc_name.trim() && !hostStatusMap.value[t.pc_name.trim()]) {
        hostsToCheck.add(t.pc_name.trim());
      }
    });

    for (const host of hostsToCheck) {
      hostStatusMap.value[host] = { loading: true, is_online: null, avg_rtt: null };
      apiFetch<HostDiagnostics>(`/admin/api/diag/${encodeURIComponent(host)}`)
        .then(res => {
          if (res) {
            hostStatusMap.value[host] = {
              loading: false,
              is_online: res.is_online,
              avg_rtt: res.avg_rtt,
              smb_ok: res.smb_ok,
              winrm_ok: res.winrm_ok,
              status_label: res.status_label,
            };
          } else {
            hostStatusMap.value[host] = { loading: false, is_online: false, avg_rtt: null };
          }
        })
        .catch(() => {
          hostStatusMap.value[host] = { loading: false, is_online: false, avg_rtt: null };
        });
    }
  };

  // Загрузка очереди заявок
  const fetchQueue = async (silent: boolean = false) => {
    if (!silent) loading.value = true;
    try {
      await fetchTemplates();
      const data = await apiFetch<QueueResponse>(`/admin/api/queue?filter_id=${filterId.value}&limit=100`);
      if (data && Array.isArray(data.tasks)) {
        if (data.subservices_by_root) {
          subservicesByRoot.value = data.subservices_by_root;
        }

        // Проверяем, появились ли новые задачи
        if (knownTaskIds.value.size > 0) {
          const newTasks = data.tasks.filter(t => !knownTaskIds.value.has(t.id));
          if (newTasks.length > 0) {
            sound.playNewTaskSound();
            toast.info(`В очередь поступило новых заявок: ${newTasks.length}`, 'Новые заявки');
          }
        }

        // Обновляем список известных ID
        knownTaskIds.value = new Set(data.tasks.map(t => t.id));
        tasks.value = data.tasks;

        // Запуск фоновой проверки пинга хостов
        triggerHostDiagnostics(data.tasks);
      }
    } catch (e: any) {
      console.error('Ошибка загрузки очереди заявок:', e);
      if (!silent) {
        toast.error('Не удалось загрузить очередь заявок из IntraService: ' + e.message);
      }
    } finally {
      if (!silent) loading.value = false;
    }
  };

  // Переключение активного корневого сервиса (сбрасывает подсервис на 'all')
  const selectServiceTab = (key: string) => {
    activeServiceTab.value = key;
    activeSubServiceTab.value = 'all';
  };

  // Подбор иконки для сервиса
  const getServiceIcon = (serviceName: string): string => {
    const s = (serviceName || '').toLowerCase();
    if (s.startsWith('01') || s.includes('учетн') || s.includes('пользовател') || s.includes('логин')) return 'user';
    if (s.startsWith('02') || s.includes('программ') || s.includes('установка') || s.includes('office')) return 'tool';
    if (s.startsWith('03') || s.includes('оргтехник') || s.includes('печать') || s.includes('принтер') || s.includes('мфу') || s.includes('компьютер')) return 'printer';
    if (s.startsWith('04') || s.includes('wi-fi') || s.includes('wifi') || s.includes('сеть') || s.includes('интернет') || s.includes('lan')) return 'wifi';
    if (s.startsWith('05') || s.includes('directum') || s.includes('директум') || s.includes('b2b')) return 'file';
    if (s.startsWith('06') || s.includes('1с') || s.includes('1c') || s.includes('зуп') || s.includes('бухгалтер') || s.includes('erp') || s.includes('упп')) return 'chart';
    if (s.startsWith('07') || s.includes('терминал') || s.includes('тсд')) return 'tool';
    if (s.startsWith('08') || s.includes('безопасност') || s.includes('иб') || s.includes('парол') || s.includes('perco') || s.includes('видео')) return 'shield';
    if (s.startsWith('09') || s.includes('эцп') || s.includes('подпись') || s.includes('сбис')) return 'shield';
    if (s.startsWith('10') || s.includes('телефон')) return 'headphones';
    if (s.startsWith('12') || s.includes('архив') || s.includes('ктд')) return 'folder';
    if (s.startsWith('14') || s.includes('mdc') || s.includes('диспетчер') || s.includes('dpa')) return 'chart';
    if (s.startsWith('15') || s.includes('helpdesk')) return 'headphones';
    if (s.startsWith('16') || s.includes('ips') || s.includes('pdm') || s.includes('plm')) return 'file';
    if (s.startsWith('17') || s.includes('тэмпо') || s.includes('скс') || s.includes('газ') || s.includes('вентиляц')) return 'tool';
    if (s.includes('1-я линия') || s.includes('первая линия')) return 'headphones';
    return 'folder';
  };

  // Статистика готовности решений AI
  const aiStats = computed(() => {
    let ready = 0;
    let manual = 0;
    tasks.value.forEach(t => {
      if (t.has_ai_solution) {
        ready++;
      } else {
        manual++;
      }
    });
    return {
      total: tasks.value.length,
      ready,
      manual,
    };
  });

  // Список доступных вкладок сервисов (17 корневых сервисов IntraService)
  const serviceTabs = computed<ServiceTabInfo[]>(() => {
    // Подсчет задач по root_service_name / root_service_id
    const countsByRootName: Record<string, number> = {};
    const countsByRootId: Record<number, number> = {};

    tasks.value.forEach(t => {
      const rName = t.root_service_name || '11. Общие вопросы';
      countsByRootName[rName] = (countsByRootName[rName] || 0) + 1;
      if (t.root_service_id) {
        countsByRootId[t.root_service_id] = (countsByRootId[t.root_service_id] || 0) + 1;
      }
    });

    // 1. Все сервисы (Всегда первая вкладка)
    const allTab: ServiceTabInfo = {
      id: 0,
      name: 'Все сервисы очереди',
      short_name: 'Все сервисы',
      key: 'all',
      icon: 'all',
      count: tasks.value.length,
    };

    // 2. 17 корневых сервисов IntraService
    const canonicalTabs: ServiceTabInfo[] = INTRASERVICE_ROOT_SERVICES.map(svc => {
      const count = countsByRootId[svc.id] || countsByRootName[svc.name] || 0;
      return {
        id: svc.id,
        name: svc.name,
        short_name: svc.short_name,
        key: svc.name,
        icon: svc.icon,
        count,
      };
    });

    // Если в очереди есть задачи с сервисами вне 17 стандартных
    const otherCounts: Record<string, { id: number; name: string; count: number }> = {};
    tasks.value.forEach(t => {
      const rName = t.root_service_name || 'Прочие сервисы';
      const existsInCanonical = INTRASERVICE_ROOT_SERVICES.some(
        s => s.name === rName || (t.root_service_id && s.id === t.root_service_id)
      );
      if (!existsInCanonical) {
        if (!otherCounts[rName]) {
          otherCounts[rName] = { id: t.root_service_id || 0, name: rName, count: 0 };
        }
        otherCounts[rName].count++;
      }
    });

    const otherTabs: ServiceTabInfo[] = Object.values(otherCounts).map(o => ({
      id: o.id,
      name: o.name,
      short_name: o.name,
      key: o.name,
      icon: getServiceIcon(o.name),
      count: o.count,
    }));

    // Сортировка: Сначала сервисы с открытыми заявками (> 0), затем остальные по порядку 01..17
    canonicalTabs.sort((a, b) => {
      if (a.count > 0 && b.count === 0) return -1;
      if (a.count === 0 && b.count > 0) return 1;
      return 0;
    });

    return [allTab, ...canonicalTabs, ...otherTabs];
  });

  // Список чипов подсервисов (2-й уровень вложенности)
  const subServiceTabs = computed<Array<{ id: number; name: string; key: string; count: number }>>(() => {
    if (activeServiceTab.value === 'all') return [];

    // Находим выбранный корневой сервис
    const activeRoot = INTRASERVICE_ROOT_SERVICES.find(
      s => s.name === activeServiceTab.value || s.short_name === activeServiceTab.value
    );
    const rootId = activeRoot ? activeRoot.id : null;

    // Задачи в текущем корневом сервисе
    const currentRootTasks = tasks.value.filter(
      t =>
        t.root_service_name === activeServiceTab.value ||
        (rootId && t.root_service_id === rootId) ||
        t.service_name === activeServiceTab.value
    );

    const counts: Record<string, { id: number; name: string; count: number }> = {};

    // 1. Добавляем известные подсервисы из каталога IntraService
    if (rootId && subservicesByRoot.value[rootId]) {
      subservicesByRoot.value[rootId].forEach(sub => {
        counts[sub.name] = { id: sub.id, name: sub.name, count: 0 };
      });
    }

    // 2. Подсчитываем реальные открытые задачи
    currentRootTasks.forEach(t => {
      const sName = t.service_name || 'Не указана';
      if (sName !== t.root_service_name) {
        if (!counts[sName]) {
          counts[sName] = { id: t.service_id || 0, name: sName, count: 0 };
        }
        counts[sName].count++;
      }
    });

    const subList = Object.values(counts);

    // Сортировка: сначала подсервисы с открытыми заявками (> 0)
    subList.sort((a, b) => b.count - a.count);

    return [
      {
        id: 0,
        name: 'Все подсервисы',
        key: 'all',
        count: currentRootTasks.length,
      },
      ...subList.map(s => ({
        id: s.id,
        name: s.name,
        key: s.name,
        count: s.count,
      })),
    ];
  });

  // Переключение сортировки
  const toggleSort = (col: SortColumn) => {
    if (sortBy.value === col) {
      sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc';
    } else {
      sortBy.value = col;
      sortDirection.value = col === 'score' || col === 'id' ? 'desc' : 'asc';
    }
  };

  // Сброс фильтров
  const resetFilters = () => {
    searchQuery.value = '';
    confidenceFilter.value = 'all';
    hostFilter.value = 'all';
    hasAttachmentsOnly.value = false;
    redirectOnly.value = false;
    activeServiceTab.value = 'all';
    activeSubServiceTab.value = 'all';
    aiSolutionFilter.value = 'all';
  };

  const hasActiveFilters = computed<boolean>(() => {
    return (
      searchQuery.value.trim() !== '' ||
      confidenceFilter.value !== 'all' ||
      hostFilter.value !== 'all' ||
      hasAttachmentsOnly.value ||
      redirectOnly.value ||
      activeServiceTab.value !== 'all' ||
      activeSubServiceTab.value !== 'all' ||
      aiSolutionFilter.value !== 'all'
    );
  });

  // Отфильтрованный и отсортированный список задач
  const filteredTasks = computed<TaskItem[]>(() => {
    let result = [...tasks.value];

    // 0. Фильтрация по потоку готовности ответа AI
    if (aiSolutionFilter.value === 'ready') {
      result = result.filter(t => t.has_ai_solution);
    } else if (aiSolutionFilter.value === 'manual') {
      result = result.filter(t => !t.has_ai_solution);
    }

    // 1. Фильтрация по выбранной вкладке корневого сервиса (1-й уровень)
    if (activeServiceTab.value !== 'all') {
      result = result.filter(
        t =>
          t.root_service_name === activeServiceTab.value ||
          t.service_name === activeServiceTab.value ||
          (t.root_service_id && String(t.root_service_id) === activeServiceTab.value)
      );
    }

    // 1.1. Фильтрация по выбранному подсервису (2-й уровень)
    if (activeServiceTab.value !== 'all' && activeSubServiceTab.value !== 'all') {
      result = result.filter(
        t =>
          t.service_name === activeSubServiceTab.value ||
          (t.service_id && String(t.service_id) === activeSubServiceTab.value)
      );
    }

    // 2. Поиск по тексту
    if (searchQuery.value.trim()) {
      const q = searchQuery.value.toLowerCase().trim();
      result = result.filter(
        t =>
          t.id.toString().includes(q) ||
          (t.name && t.name.toLowerCase().includes(q)) ||
          (t.description && t.description.toLowerCase().includes(q)) ||
          (t.creator && t.creator.toLowerCase().includes(q)) ||
          (t.service_name && t.service_name.toLowerCase().includes(q)) ||
          (t.root_service_name && t.root_service_name.toLowerCase().includes(q)) ||
          (t.service_path && t.service_path.toLowerCase().includes(q)) ||
          (t.target_service_name && t.target_service_name.toLowerCase().includes(q)) ||
          (t.pc_name && t.pc_name.toLowerCase().includes(q)) ||
          (t.room && t.room.toLowerCase().includes(q)) ||
          (t.phone && t.phone.toLowerCase().includes(q))
      );
    }

    // 3. Фильтрация по уровню уверенности
    if (confidenceFilter.value === 'high') {
      result = result.filter(t => t.score >= 9);
    } else if (confidenceFilter.value === 'medium') {
      result = result.filter(t => t.score >= 6 && t.score < 9);
    } else if (confidenceFilter.value === 'low') {
      result = result.filter(t => t.score < 6);
    }

    // 4. Фильтрация по статусу ПК
    if (hostFilter.value === 'online') {
      result = result.filter(t => {
        const diag = hostStatusMap.value[t.pc_name?.trim()];
        return diag && diag.is_online === true;
      });
    } else if (hostFilter.value === 'offline') {
      result = result.filter(t => {
        const diag = hostStatusMap.value[t.pc_name?.trim()];
        return !t.pc_name || (diag && diag.is_online === false);
      });
    }

    // 5. Только с вложениями
    if (hasAttachmentsOnly.value) {
      result = result.filter(t => t.has_attachments);
    }

    // 6. Только редиректы
    if (redirectOnly.value) {
      result = result.filter(t => t.is_redirect);
    }

    // 7. Сортировка
    result.sort((a, b) => {
      let valA: any;
      let valB: any;

      switch (sortBy.value) {
        case 'id':
          valA = a.id;
          valB = b.id;
          break;
        case 'score':
          valA = a.score;
          valB = b.score;
          break;
        case 'creator':
          valA = (a.creator || '').toLowerCase();
          valB = (b.creator || '').toLowerCase();
          break;
        case 'service':
          valA = (a.service_name || '').toLowerCase();
          valB = (b.service_name || '').toLowerCase();
          break;
        case 'pc_name':
          valA = (a.pc_name || '').toLowerCase();
          valB = (b.pc_name || '').toLowerCase();
          break;
        case 'created':
          valA = a.created || '';
          valB = b.created || '';
          break;
        default:
          valA = a.id;
          valB = b.id;
      }

      if (valA < valB) return sortDirection.value === 'asc' ? -1 : 1;
      if (valA > valB) return sortDirection.value === 'asc' ? 1 : -1;
      return 0;
    });

    return result;
  });

  // Выбор шаблона для задачи
  const selectTemplateForTask = (task: TaskItem, templateKey: string) => {
    const tmpl = templatesMap.value[templateKey];
    if (tmpl) {
      task.template_key = templateKey;
      task.target_status_id = tmpl.status_id;
      task.target_status_name = tmpl.status_name;
      
      // Динамическая подстановка переменных
      let rendered = tmpl.template || '';
      if (rendered.includes('{pc_name}')) {
        rendered = rendered.replace(/{pc_name}/g, task.pc_name || 'вашем ПК');
      }
      if (rendered.includes('{target_service}')) {
        rendered = rendered.replace(/{target_service}/g, task.target_service_name || 'соответствующий раздел');
      }
      
      task.suggested_comment = rendered;
      task.expenses = tmpl.expenses || 10;
      task.badge_color = (tmpl.badge_color as any) || 'primary';
    }
  };

  // Сброс комментария к исходному AI-решению
  const resetTaskComment = (task: TaskItem) => {
    if (task.original_comment) {
      task.suggested_comment = task.original_comment;
    }
  };

  // Открытие шторки детального просмотра
  const openTaskDrawer = async (taskId: number) => {
    activeDrawerTaskId.value = taskId;
    drawerLoading.value = true;
    drawerTaskDetails.value = null;

    try {
      const data = await apiFetch<TaskDetails>(`/admin/api/tasks/${taskId}/details`);
      drawerTaskDetails.value = data;
    } catch (e: any) {
      toast.error(`Не удалось загрузить детали заявки #${taskId}: ` + e.message);
    } finally {
      drawerLoading.value = false;
    }
  };

  const closeTaskDrawer = () => {
    activeDrawerTaskId.value = null;
    drawerTaskDetails.value = null;
  };

  // Применение одиночного действия к заявке
  const applySingleAction = async (task: TaskItem) => {
    submittingIds.value.add(task.id);
    try {
      const payload: SingleApplyPayload = {
        status_id: task.target_status_id || 27,
        comment: task.suggested_comment || '',
        minutes: task.expenses || 10,
        executor_ids: '8664,10502',
        is_private: !!task.is_private,
      };

      const res = await apiFetch<{ success: boolean; final_status_id?: number }>(
        `/admin/api/tasks/${task.id}/apply`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        }
      );

      if (res && res.success) {
        doneIds.value.add(task.id);
        selectedTaskIds.value.delete(task.id);
        sound.playSuccessSound();
        toast.success(`Заявка #${task.id} переведена в статус ${res.final_status_id || task.target_status_id}`);

        setTimeout(() => {
          tasks.value = tasks.value.filter(t => t.id !== task.id);
          doneIds.value.delete(task.id);
        }, 2500);
      }
    } catch (e: any) {
      toast.error(`Ошибка применения к заявке #${task.id}: ` + e.message);
    } finally {
      submittingIds.value.delete(task.id);
    }
  };

  // Пакетное применение ко всем выбранным
  const applyBulkSelected = async () => {
    const selectedTasks = tasks.value.filter(t => selectedTaskIds.value.has(t.id));
    if (selectedTasks.length === 0) return;

    const payload: BulkApplyRequest = {
      tasks: selectedTasks.map(t => ({
        task_id: t.id,
        status_id: t.target_status_id || 27,
        comment: t.suggested_comment || '',
        minutes: t.expenses || 10,
        executor_ids: '8664,10502',
        is_private: !!t.is_private,
      })),
    };

    selectedTasks.forEach(t => submittingIds.value.add(t.id));

    try {
      const res = await apiFetch<BulkApplyResponse>('/admin/api/tasks/bulk-apply', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (res) {
        sound.playSuccessSound();
        toast.success(`Пакетно обработано ${res.success_count} из ${res.total} заявок`);
        if (res.failed_count > 0) {
          toast.warning(`Не удалось обработать ${res.failed_count} заявок`);
        }

        const appliedIds = new Set(res.applied.map(a => a.task_id));
        appliedIds.forEach(id => {
          doneIds.value.add(id);
          selectedTaskIds.value.delete(id);
        });

        setTimeout(() => {
          tasks.value = tasks.value.filter(t => !appliedIds.has(t.id));
          appliedIds.forEach(id => doneIds.value.delete(id));
        }, 2500);
      }
    } catch (e: any) {
      toast.error('Ошибка пакетного применения: ' + e.message);
    } finally {
      selectedTasks.forEach(t => submittingIds.value.delete(t.id));
    }
  };

  // Управление множественным выбором
  const toggleSelect = (id: number) => {
    if (selectedTaskIds.value.has(id)) {
      selectedTaskIds.value.delete(id);
    } else {
      selectedTaskIds.value.add(id);
    }
  };

  const selectAllFiltered = (list: TaskItem[]) => {
    list.forEach(t => selectedTaskIds.value.add(t.id));
  };

  const selectAllConfident = (list: TaskItem[]) => {
    list.filter(t => t.score >= 9).forEach(t => selectedTaskIds.value.add(t.id));
  };

  const deselectAll = () => {
    selectedTaskIds.value.clear();
  };

  return {
    filterId,
    tasks,
    loading,
    selectedTaskIds,
    submittingIds,
    doneIds,
    activeDrawerTaskId,
    drawerTaskDetails,
    drawerLoading,
    templates,
    templatesMap,
    hostStatusMap,
    aiSolutionFilter,
    activeServiceTab,
    activeSubServiceTab,
    searchQuery,
    viewMode,
    density,
    confidenceFilter,
    hostFilter,
    hasAttachmentsOnly,
    redirectOnly,
    sortBy,
    sortDirection,
    aiStats,
    serviceTabs,
    subServiceTabs,
    filteredTasks,
    hasActiveFilters,
    fetchTemplates,
    fetchQueue,
    selectServiceTab,
    selectTemplateForTask,
    resetTaskComment,
    openTaskDrawer,
    closeTaskDrawer,
    applySingleAction,
    applyBulkSelected,
    toggleSelect,
    selectAllFiltered,
    selectAllConfident,
    deselectAll,
    toggleSort,
    resetFilters,
  };
});
