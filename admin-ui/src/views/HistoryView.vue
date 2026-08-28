<template>
  <section class="screen active">
    <!-- Строка метрик -->
    <StatsRow :items="statsItems" />

    <!-- Панель журнала -->
    <div class="card mb-0">
      <div class="card-header history-header">
        <div class="header-titles">
          <div class="card-title">📜 Журнал операций и принтеров</div>
          <div class="card-subtitle">Фоновые задачи установки, WMI/WinRM сессии и статусы очередей</div>
        </div>

        <div class="history-tools">
          <!-- Поиск по журналу -->
          <div class="search-input-wrap">
            <svg viewBox="0 0 24 24" width="15" height="15" class="search-icon">
              <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" fill="none"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
            <input 
              v-model="searchQuery" 
              type="text" 
              class="search-input" 
              placeholder="Поиск по ПК, ID, модели..."
            />
          </div>

          <button class="btn btn-primary btn-sm" :disabled="loading" @click="fetchHistory">
            <svg v-if="loading" class="spin" viewBox="0 0 24 24" width="14" height="14">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none" opacity="0.3"/>
              <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-width="4" fill="none"/>
            </svg>
            <svg v-else viewBox="0 0 24 24" width="14" height="14">
              <polyline points="23 4 23 10 17 10" stroke="currentColor" stroke-width="2" fill="none"/>
              <polyline points="1 20 1 14 7 14" stroke="currentColor" stroke-width="2" fill="none"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" stroke="currentColor" stroke-width="2" fill="none"/>
            </svg>
            <span>Обновить</span>
          </button>
        </div>
      </div>

      <!-- Фильтры по статусам -->
      <div class="history-filter-tabs">
        <button 
          v-for="st in statusFilters" 
          :key="st.key" 
          class="history-tab"
          :class="{ active: currentStatusFilter === st.key }"
          @click="currentStatusFilter = st.key"
        >
          {{ st.label }}
          <span class="tab-count">{{ getStatusCount(st.key) }}</span>
        </button>
      </div>
      
      <!-- Таблица задач -->
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>№ задачи</th>
              <th>Компьютер</th>
              <th>Модель принтера</th>
              <th>Тип</th>
              <th>Статус</th>
              <th width="220">Действия</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading && jobs.length === 0" class="empty-row">
              <td colspan="6">Загрузка журнала из Redis...</td>
            </tr>
            <tr v-else-if="filteredJobs.length === 0" class="empty-row">
              <td colspan="6">Задачи не найдены</td>
            </tr>
            
            <template v-for="job in filteredJobs" :key="job.task_id">
              <!-- Основная строка задачи -->
              <tr :id="`job-row-${job.task_id}`">
                <td class="td-mono">
                  <span class="job-id-pill">#{{ job.task_id }}</span>
                </td>
                <td>
                  <strong>{{ job.target_pc || '—' }}</strong>
                </td>
                <td>{{ job.model_key || '—' }}</td>
                <td>
                  <span class="conn-type-badge">{{ (job.connection_type || '—').toUpperCase() }}</span>
                </td>
                <td>
                  <span class="badge" :class="`badge-${stateBadge(job.state)}`">{{ formatStateLabel(job.state) }}</span>
                  <div v-if="job.error_message" class="error-sub-text">
                    {{ job.error_message }}
                  </div>
                  
                  <!-- Предложение установки универсального драйвера -->
                  <div v-if="job.state === 'failed' && isMissingDriverError(job.error_message) && !dismissedPrompts[job.task_id]" 
                       class="hp-universal-banner">
                    <div class="banner-title">
                      Драйвер не найден. Установить универсальный драйвер HP?
                    </div>
                    <div class="banner-actions">
                      <button 
                        class="btn btn-success btn-xs" 
                        @click="restartJob(job.task_id, 'hp_universal_upd')"
                      >
                        ✓ Установить HP UPD
                      </button>
                      <button 
                        class="btn btn-ghost btn-xs" 
                        @click="dismissPrompt(job.task_id)"
                      >
                        Скрыть
                      </button>
                    </div>
                  </div>
                </td>
                <td>
                  <!-- Действия для задач, ожидающих подтверждения -->
                  <div v-if="job.state === 'waiting_approval'" class="approval-actions">
                    <button 
                      class="btn btn-success btn-xs" 
                      @click="sendJobAction(job.task_id, 'approve')" 
                      title="Запустить установку с этими параметрами"
                    >
                      ✓ Подтвердить
                    </button>
                    <button 
                      class="btn btn-outline btn-xs btn-danger-outline" 
                      @click="sendJobAction(job.task_id, 'reject')" 
                      title="Отменить автоматическую установку"
                    >
                      Отклонить
                    </button>
                  </div>
                  
                  <!-- Обычные действия -->
                  <div v-else class="job-actions">
                    <button 
                      class="btn btn-outline btn-xs" 
                      :class="{ 'btn-primary': activeLogModalTaskId === job.task_id }"
                      @click="openLogsModal(job.task_id)"
                    >
                      📟 Логи
                    </button>
                    <button 
                      class="btn btn-outline btn-xs" 
                      @click="restartJob(job.task_id)"
                      title="Перезапустить установку повторно"
                    >
                      Рестарт
                    </button>
                    <button 
                      class="btn btn-ghost btn-xs text-red" 
                      @click="deleteJob(job.task_id)" 
                      title="Удалить задачу из истории"
                    >
                      ✕
                    </button>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Модальное окно просмотра логов SSE -->
    <Teleport to="body">
      <div v-if="activeLogModalTaskId" class="log-modal-backdrop" @click="closeLogsModal">
        <div class="log-modal-panel" @click.stop>
          <div class="log-modal-header">
            <div class="log-modal-title">
              <span class="terminal-dot red"></span>
              <span class="terminal-dot yellow"></span>
              <span class="terminal-dot green"></span>
              <span class="title-text">📟 Терминал логов: Задание #{{ activeLogModalTaskId }}</span>
            </div>
            <div class="log-modal-controls">
              <button class="btn btn-outline btn-xs" @click="copyLogsToClipboard">
                Копировать логи
              </button>
              <button class="btn-ghost btn-xs" @click="closeLogsModal">
                ✕
              </button>
            </div>
          </div>

          <div ref="terminalBodyRef" class="log-modal-body">
            <div 
              v-for="(line, idx) in currentLogLines" 
              :key="idx" 
              class="terminal-line"
              :class="getLineClass(line)"
            >
              {{ line }}
            </div>
            <div v-if="currentLogLines.length === 0" class="terminal-line term-line-sys">
              [SYSTEM] Ожидание вывода логов от воркера...
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, inject, nextTick } from 'vue';
import { apiFetch } from '../api';
import StatsRow from '../components/StatsRow.vue';
import { useToastStore } from '../stores/toast';
import { usePolling } from '../composables/usePolling';

const toastStore = useToastStore();

const jobs = ref([]);
const loading = ref(true);
const searchQuery = ref('');
const currentStatusFilter = ref('all');

const activeLogModalTaskId = ref(null);
const currentLogLines = ref([]);
let activeSse = null;
const terminalBodyRef = ref(null);

const dismissedPrompts = ref({});
const dismissPrompt = (taskId) => {
  dismissedPrompts.value[taskId] = true;
};

const isMissingDriverError = (errorMsg) => {
  if (!errorMsg) return false;
  return errorMsg.toLowerCase().includes('универсальный драйвер') || errorMsg.toLowerCase().includes('драйвер не найден');
};

const counts = ref({ done: 0, active: 0, failed: 0, waiting: 0 });

const statsItems = computed(() => [
  { value: counts.value.done, label: 'Успешно установлено', color: 'green', icon: 'check' },
  { value: counts.value.active, label: 'В процессе установки', color: 'blue', icon: 'inbox' },
  { value: counts.value.waiting, label: 'Ожидают подтверждения', color: 'yellow', icon: 'clock' },
  { value: counts.value.failed, label: 'Ошибок установки', color: 'red', icon: 'error' }
]);

const statusFilters = [
  { key: 'all', label: 'Все задачи' },
  { key: 'waiting_approval', label: '⏳ Ожидают решения' },
  { key: 'in_progress', label: '⚡ В процессе' },
  { key: 'failed', label: '❌ Ошибки' },
  { key: 'done', label: '✅ Успешные' },
];

const inProgressStates = ['probing', 'copying', 'installing', 'verifying', 'routing', 'parsing', 'waiting', 'pending'];

const getStatusCount = (key) => {
  if (key === 'all') return jobs.value.length;
  if (key === 'waiting_approval') return jobs.value.filter(j => j.state === 'waiting_approval').length;
  if (key === 'in_progress') return jobs.value.filter(j => inProgressStates.includes(j.state)).length;
  if (key === 'failed') return jobs.value.filter(j => j.state === 'failed').length;
  if (key === 'done') return jobs.value.filter(j => j.state === 'done').length;
  return 0;
};

const filteredJobs = computed(() => {
  let list = jobs.value;

  // Фильтр по табам
  if (currentStatusFilter.value === 'waiting_approval') {
    list = list.filter(j => j.state === 'waiting_approval');
  } else if (currentStatusFilter.value === 'in_progress') {
    list = list.filter(j => inProgressStates.includes(j.state));
  } else if (currentStatusFilter.value === 'failed') {
    list = list.filter(j => j.state === 'failed');
  } else if (currentStatusFilter.value === 'done') {
    list = list.filter(j => j.state === 'done');
  }

  // Поиск
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase().trim();
    list = list.filter(j => {
      return (
        j.task_id?.toString().includes(q) ||
        (j.target_pc && j.target_pc.toLowerCase().includes(q)) ||
        (j.model_key && j.model_key.toLowerCase().includes(q)) ||
        (j.connection_type && j.connection_type.toLowerCase().includes(q))
      );
    });
  }

  return list;
});

const stateBadge = (state) => {
  const m = {
    done: 'done', failed: 'failed',
    pending: 'pending', waiting_approval: 'pending', waiting: 'pending',
    routing: 'progress', parsing: 'progress', probing: 'progress',
    copying: 'progress', installing: 'progress', verifying: 'progress'
  };
  return m[state] || 'progress';
};

const formatStateLabel = (state) => {
  const labels = {
    done: 'Выполнена',
    failed: 'Сбой',
    waiting_approval: 'Требует решения',
    probing: 'Диагностика WMI',
    copying: 'Копирование драйвера',
    installing: 'Установка принтера',
    verifying: 'Верификация',
    routing: 'Маршрутизация',
    parsing: 'Анализ',
  };
  return labels[state] || state;
};

const getLineClass = (line) => {
  if (line.includes('[ERROR]') || line.includes('FAIL') || line.includes('Ошибка')) return 'term-line-err';
  if (line.includes('[OK]') || line.includes('done') || line.includes('успешно')) return 'term-line-ok';
  if (line.includes('[WARN]') || line.includes('Внимание')) return 'term-line-warn';
  if (line.includes('[SYSTEM]')) return 'term-line-sys';
  return 'term-line-info';
};

// Загрузка данных журнала
const fetchHistory = async () => {
  try {
    const data = await apiFetch('/admin/api/print-jobs');
    jobs.value = data || [];
    
    // Обновляем статистику
    let done = 0, active = 0, failed = 0, waiting = 0;
    jobs.value.forEach(j => {
      if (j.state === 'done') done++;
      else if (j.state === 'failed') failed++;
      else if (j.state === 'waiting_approval') waiting++;
      else if (inProgressStates.includes(j.state)) active++;
    });

    counts.value = { done, active, failed, waiting };
  } catch (err) {
    console.error('Ошибка загрузки истории:', err);
    toastStore.error('Не удалось загрузить журнал операций');
  } finally {
    loading.value = false;
  }
};

// Подтверждение/отклонение задачи
const sendJobAction = async (taskId, action) => {
  const actionNames = { approve: 'подтвердить', reject: 'отклонить' };
  if (!confirm(`Вы действительно хотите ${actionNames[action] || action} установку для задачи #${taskId}?`)) return;
  
  try {
    await apiFetch(`/admin/api/print-jobs/${taskId}/action`, {
      method: 'POST',
      body: JSON.stringify({ action })
    });
    toastStore.success(`Действие «${action}» отправлено воркеру для задачи #${taskId}`);
    await fetchHistory();
  } catch (err) {
    toastStore.error(err.message);
  }
};

// Перезапуск задачи
const restartJob = async (taskId, modelKey = null) => {
  try {
    let url = `/admin/api/print-jobs/${taskId}/restart`;
    if (modelKey) {
      url += `?model_key=${encodeURIComponent(modelKey)}`;
    }
    await apiFetch(url, { method: 'POST' });
    toastStore.success(`Задача #${taskId} отправлена на повторный запуск`);
    await fetchHistory();
  } catch (err) {
    toastStore.error('Ошибка при перезапуске: ' + err.message);
  }
};

// Удаление задачи
const deleteJob = async (taskId) => {
  if (!confirm(`Удалить задачу #${taskId} из истории Redis?`)) return;
  
  try {
    await apiFetch(`/admin/api/print-jobs/${taskId}`, { method: 'DELETE' });
    toastStore.success(`Задача #${taskId} удалена`);
    await fetchHistory();
  } catch (err) {
    toastStore.error(err.message);
  }
};

// Модальное окно логов SSE
const openLogsModal = (jobId) => {
  if (activeSse) {
    activeSse.close();
    activeSse = null;
  }

  activeLogModalTaskId.value = jobId;
  currentLogLines.value = ['[SYSTEM] Подключение к SSE потоку логов...'];

  activeSse = new EventSource(`/admin/api/print-jobs/${jobId}/logs`);
  let firstMsg = true;

  activeSse.onmessage = (e) => {
    if (firstMsg) {
      currentLogLines.value = [];
      firstMsg = false;
    }
    currentLogLines.value.push(e.data);
    nextTick(() => {
      if (terminalBodyRef.value) {
        terminalBodyRef.value.scrollTop = terminalBodyRef.value.scrollHeight;
      }
    });
  };

  activeSse.onerror = () => {
    currentLogLines.value.push('[SYSTEM] Поток логов завершён.');
    activeSse?.close();
    activeSse = null;
  };
};

const closeLogsModal = () => {
  if (activeSse) {
    activeSse.close();
    activeSse = null;
  }
  activeLogModalTaskId.value = null;
  currentLogLines.value = [];
};

const copyLogsToClipboard = () => {
  const text = currentLogLines.value.join('\n');
  navigator.clipboard.writeText(text);
  toastStore.info('Логи скопированы в буфер обмена');
};

// Подписка на глобальное обновление из AppTopbar
const registerRefresh = inject('registerRefresh');
let unregisterRefresh = null;
let polling = null;

onMounted(() => {
  fetchHistory();
  polling = usePolling(fetchHistory, 5000, false);
  if (registerRefresh) {
    unregisterRefresh = registerRefresh(fetchHistory);
  }
});

onUnmounted(() => {
  if (polling) polling.stop();
  if (unregisterRefresh) unregisterRefresh();
  if (activeSse) activeSse.close();
});
</script>

<style scoped>
.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.history-tools {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.search-input-wrap {
  position: relative;
  display: flex;
  align-items: center;
  width: 240px;
}
.search-icon {
  position: absolute;
  left: 0.75rem;
  color: var(--text-3);
}
.search-input {
  width: 100%;
  padding: 0.4rem 0.75rem 0.4rem 2.2rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.82rem;
  outline: none;
}
.search-input:focus {
  border-color: var(--primary);
}

.history-filter-tabs {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1.5rem;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}

.history-tab {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-2);
  padding: 0.35rem 0.75rem;
  border-radius: 20px;
  font-size: 0.78rem;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  transition: all 0.15s;
}
.history-tab:hover {
  border-color: var(--border-hover);
  color: var(--text);
}
.history-tab.active {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}
.tab-count {
  font-size: 0.7rem;
  padding: 0.05rem 0.4rem;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.15);
}

.job-id-pill {
  font-family: monospace;
  font-weight: 700;
  color: var(--primary);
}

.conn-type-badge {
  font-size: 0.72rem;
  background: var(--surface-2);
  padding: 0.2rem 0.45rem;
  border-radius: 4px;
  border: 1px solid var(--border);
}

.error-sub-text {
  font-size: 0.72rem;
  color: var(--red);
  margin-top: 0.25rem;
}

.hp-universal-banner {
  margin-top: 0.5rem;
  padding: 0.6rem 0.8rem;
  background: rgba(245, 158, 11, 0.06);
  border: 1px dashed rgba(245, 158, 11, 0.4);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.banner-title {
  font-size: 0.75rem;
  color: var(--yellow);
  font-weight: 500;
}
.banner-actions {
  display: flex;
  gap: 0.5rem;
}

.approval-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.btn-danger-outline {
  color: var(--red);
  border-color: rgba(244, 63, 94, 0.4);
}
.btn-danger-outline:hover {
  background: var(--red-bg);
}

.job-actions {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.text-red {
  color: var(--red);
}

/* Log Modal Terminal */
.log-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}
.log-modal-panel {
  width: 820px;
  max-width: 90vw;
  height: 600px;
  max-height: 85vh;
  background: #0d1117;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: var(--radius);
  box-shadow: 0 25px 50px rgba(0, 0, 0, 0.7);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.log-modal-header {
  padding: 0.75rem 1.25rem;
  background: #161b22;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.log-modal-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: #c9d1d9;
}
.terminal-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.terminal-dot.red { background: #ff5f56; }
.terminal-dot.yellow { background: #ffbd2e; }
.terminal-dot.green { background: #27c93f; }

.log-modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 1.25rem;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.82rem;
  line-height: 1.5;
  color: #c9d1d9;
  background: #090d13;
}
.terminal-line {
  white-space: pre-wrap;
  word-break: break-all;
}
.term-line-err { color: #f85149; }
.term-line-ok { color: #3fb950; }
.term-line-warn { color: #d29922; }
.term-line-sys { color: #58a6ff; font-weight: 600; }
.term-line-info { color: #8b949e; }
</style>
