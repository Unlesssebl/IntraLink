import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiFetch } from '../api';
import { useToastStore } from './toast';

export const useQueueStore = defineStore('queue', () => {
  const toast = useToastStore();

  const filterId = ref(984);
  const tasks = ref([]);
  const loading = ref(false);
  const selectedTaskIds = ref(new Set());
  const submittingIds = ref(new Set());
  const doneIds = ref(new Set());
  const activeDrawerTaskId = ref(null);
  const drawerTaskDetails = ref(null);
  const drawerLoading = ref(false);

  // Каталог корпоративных шаблонов
  const templates = ref([]);
  const templatesMap = ref({});
  const templatesLoaded = ref(false);

  // Кэш пингов хостов: { [pc_name]: { is_online: bool, avg_rtt: string, smb_ok: bool, winrm_ok: bool, loading: bool } }
  const hostStatusMap = ref({});

  // Активная вкладка сервиса
  const activeServiceTab = ref('all'); // 'all' или имя конкретного сервиса IntraService
  const searchQuery = ref('');
  const viewMode = ref('cards'); // 'cards' | 'grid'

  // Загрузка шаблонов
  const fetchTemplates = async () => {
    if (templatesLoaded.value) return;
    try {
      const data = await apiFetch('/admin/api/templates');
      if (data && data.templates) {
        templates.value = data.templates;
        templatesMap.value = data.map || {};
        templatesLoaded.value = true;
      }
    } catch (e) {
      console.error('Ошибка загрузки каталога шаблонов:', e);
    }
  };

  // Загрузка очереди заявок
  const fetchQueue = async () => {
    loading.value = true;
    await fetchTemplates();
    try {
      const data = await apiFetch(`/admin/api/queue?filter_id=${filterId.value}&limit=50`);
      if (data && Array.isArray(data.tasks)) {
        tasks.value = data.tasks;
        // Запуск фоновой проверки пинга хостов
        triggerHostDiagnostics(data.tasks);
      }
    } catch (e) {
      console.error('Ошибка загрузки очереди заявок:', e);
      toast.error('Не удалось загрузить очередь заявок из IntraService: ' + e.message);
    } finally {
      loading.value = false;
    }
  };

  // Фоновая диагностика хостов
  const triggerHostDiagnostics = async (taskList) => {
    const hostsToCheck = new Set();
    taskList.forEach(t => {
      if (t.pc_name && t.pc_name.trim() && !hostStatusMap.value[t.pc_name]) {
        hostsToCheck.add(t.pc_name.trim());
      }
    });

    for (const host of hostsToCheck) {
      hostStatusMap.value[host] = { loading: true, is_online: null, avg_rtt: null };
      apiFetch(`/admin/api/diag/${encodeURIComponent(host)}`)
        .then(res => {
          if (res) {
            hostStatusMap.value[host] = {
              loading: false,
              is_online: res.is_online,
              avg_rtt: res.avg_rtt,
              smb_ok: res.smb_ok,
              winrm_ok: res.winrm_ok,
            };
          }
        })
        .catch(() => {
          hostStatusMap.value[host] = { loading: false, is_online: false, avg_rtt: null };
        });
    }
  };

  // Подбор иконки для сервиса
  const getServiceIcon = (serviceName) => {
    const s = (serviceName || '').toLowerCase();
    if (s.includes('1с') || s.includes('1c') || s.includes('зуп') || s.includes('бухгалтер')) return '📊';
    if (s.includes('directum') || s.includes('директум')) return '📄';
    if (s.includes('оргтехник') || s.includes('печать') || s.includes('принтер')) return '🖨️';
    if (s.includes('безопасност') || s.includes('иб') || s.includes('парол')) return '🛡️';
    if (s.includes('wi-fi') || s.includes('wifi') || s.includes('сеть')) return '📶';
    if (s.includes('ремонт') || s.includes('обслуживан') || s.includes('железо')) return '🛠️';
    if (s.includes('1-я линия') || s.includes('первая линия') || s.includes('helpdesk')) return '🎧';
    return '📂';
  };

  // Список реальных сервисов Helpdesk в текущей очереди для вкладок
  const serviceTabs = computed(() => {
    const map = {};
    tasks.value.forEach(t => {
      const sName = t.service_name || '1-я линия технической поддержки';
      const sId = t.service_id || 0;
      if (!map[sName]) {
        map[sName] = { 
          id: sId, 
          name: sName, 
          icon: getServiceIcon(sName),
          count: 0 
        };
      }
      map[sName].count++;
    });

    const list = Object.values(map).sort((a, b) => b.count - a.count);
    return [
      { id: 0, name: 'Все сервисы', key: 'all', icon: '📋', count: tasks.value.length },
      ...list.map(s => ({ ...s, key: s.name }))
    ];
  });

  // Отфильтрованные задачи для активной вкладки сервиса и поисковой строки
  const filteredTasks = computed(() => {
    let list = tasks.value;

    if (activeServiceTab.value !== 'all') {
      list = list.filter(t => t.service_name === activeServiceTab.value);
    }

    if (searchQuery.value.trim()) {
      const q = searchQuery.value.toLowerCase().trim();
      list = list.filter(t => {
        return (
          t.id.toString().includes(q) ||
          (t.name && t.name.toLowerCase().includes(q)) ||
          (t.description && t.description.toLowerCase().includes(q)) ||
          (t.creator && t.creator.toLowerCase().includes(q)) ||
          (t.service_name && t.service_name.toLowerCase().includes(q)) ||
          (t.target_service_name && t.target_service_name.toLowerCase().includes(q)) ||
          (t.pc_name && t.pc_name.toLowerCase().includes(q)) ||
          (t.room && t.room.toLowerCase().includes(q)) ||
          (t.phone && t.phone.toLowerCase().includes(q))
        );
      });
    }
    return list;
  });

  // Применение шаблона к задаче
  const selectTemplateForTask = (task, templateKey) => {
    const tmpl = templatesMap.value[templateKey];
    if (tmpl) {
      task.template_key = templateKey;
      task.target_status_id = tmpl.status_id;
      task.target_status_name = tmpl.status_name;
      task.suggested_comment = tmpl.template;
      task.expenses = tmpl.expenses || 10;
      task.badge_color = tmpl.badge_color || 'primary';
    }
  };

  // Открытие шторки задачи
  const openTaskDrawer = async (taskId) => {
    activeDrawerTaskId.value = taskId;
    drawerLoading.value = true;
    drawerTaskDetails.value = null;
    try {
      const details = await apiFetch(`/admin/api/tasks/${taskId}/details`);
      drawerTaskDetails.value = details;
    } catch (e) {
      toast.error(`Не удалось загрузить детали заявки #${taskId}: ` + e.message);
    } finally {
      drawerLoading.value = false;
    }
  };

  const closeTaskDrawer = () => {
    activeDrawerTaskId.value = null;
    drawerTaskDetails.value = null;
  };

  // Применение единичного действия
  const applySingleAction = async (task) => {
    submittingIds.value.add(task.id);
    try {
      const payload = {
        status_id: task.target_status_id || 27,
        comment: task.suggested_comment || '',
        minutes: task.expenses || 10,
        executor_ids: "8664,10502",
        is_private: false,
      };

      const res = await apiFetch(`/admin/api/tasks/${task.id}/apply`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (res && res.success) {
        doneIds.value.add(task.id);
        selectedTaskIds.value.delete(task.id);
        toast.success(`Заявка #${task.id} переведена в статус ${res.final_status_id || task.target_status_id}`);
        setTimeout(() => {
          tasks.value = tasks.value.filter(t => t.id !== task.id);
          doneIds.value.delete(task.id);
        }, 2500);
      }
    } catch (e) {
      toast.error(`Ошибка применения к заявке #${task.id}: ` + e.message);
    } finally {
      submittingIds.value.delete(task.id);
    }
  };

  // Пакетное применение выбранных задач
  const applyBulkSelected = async () => {
    const selectedList = tasks.value.filter(t => selectedTaskIds.value.has(t.id));
    if (selectedList.length === 0) return;

    const payload = {
      tasks: selectedList.map(t => ({
        task_id: t.id,
        status_id: t.target_status_id || 27,
        comment: t.suggested_comment || '',
        minutes: t.expenses || 10,
        executor_ids: "8664,10502",
        is_private: false,
      }))
    };

    selectedList.forEach(t => submittingIds.value.add(t.id));

    try {
      const res = await apiFetch('/admin/api/tasks/bulk-apply', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (res) {
        toast.success(`Пакетно обработано ${res.success_count} из ${res.total} заявок`);
        if (res.failed_count > 0) {
          toast.warning(`Не удалось обработать ${res.failed_count} заявок`);
        }

        const successIds = new Set(res.applied.map(a => a.task_id));
        successIds.forEach(id => {
          doneIds.value.add(id);
          selectedTaskIds.value.delete(id);
        });

        setTimeout(() => {
          tasks.value = tasks.value.filter(t => !successIds.has(t.id));
          successIds.forEach(id => doneIds.value.delete(id));
        }, 2500);
      }
    } catch (e) {
      toast.error('Ошибка пакетного применения: ' + e.message);
    } finally {
      selectedList.forEach(t => submittingIds.value.delete(t.id));
    }
  };

  // Выборка / Снятие выбора
  const toggleSelect = (taskId) => {
    if (selectedTaskIds.value.has(taskId)) {
      selectedTaskIds.value.delete(taskId);
    } else {
      selectedTaskIds.value.add(taskId);
    }
  };

  const selectAllFiltered = (filteredList) => {
    filteredList.forEach(t => selectedTaskIds.value.add(t.id));
  };

  const selectAllConfident = (filteredList) => {
    filteredList.filter(t => t.score >= 9).forEach(t => selectedTaskIds.value.add(t.id));
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
    activeServiceTab,
    searchQuery,
    viewMode,
    serviceTabs,
    filteredTasks,
    fetchTemplates,
    fetchQueue,
    selectTemplateForTask,
    openTaskDrawer,
    closeTaskDrawer,
    applySingleAction,
    applyBulkSelected,
    toggleSelect,
    selectAllFiltered,
    selectAllConfident,
    deselectAll,
  };
});
