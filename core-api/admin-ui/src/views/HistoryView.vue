<template>
  <section class="screen active">
    <!-- Строка метрик -->
    <StatsRow :items="statsItems" />

    <!-- Таблица журнала -->
    <div class="card mb-0">
      <div class="card-header">
        <div>
          <div class="card-title">Журнал операций</div>
          <div class="card-subtitle">Последние 50 задач из Redis — обновляется каждые 5 секунд</div>
        </div>
      </div>
      
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>№ задачи</th>
              <th>Компьютер</th>
              <th>Модель принтера</th>
              <th>Тип</th>
              <th>Статус</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading && jobs.length === 0" class="empty-row">
              <td colspan="6">Загрузка журнала...</td>
            </tr>
            <tr v-else-if="jobs.length === 0" class="empty-row">
              <td colspan="6">История пуста</td>
            </tr>
            
            <template v-for="job in jobs" :key="job.task_id">
              <!-- Основная строка задачи -->
              <tr :id="`job-row-${job.task_id}`">
                <td class="td-mono">#{{ job.task_id }}</td>
                <td>{{ job.target_pc || '—' }}</td>
                <td>{{ job.model_key || '—' }}</td>
                <td>{{ (job.connection_type || '—').toUpperCase() }}</td>
                <td>
                  <span class="badge" :class="`badge-${stateBadge(job.state)}`">{{ job.state }}</span>
                  <div v-if="job.error_message" style="font-size: 0.72rem; color: var(--red); margin-top: 0.25rem;">
                    {{ job.error_message }}
                  </div>
                </td>
                <td style="white-space: nowrap;">
                  <!-- Действия для задач, ожидающих подтверждения -->
                  <template v-if="job.state === 'waiting_approval'">
                    <button 
                      class="btn btn-outline btn-sm" 
                      style="color: var(--green); border-color: var(--green-bg); margin-right: 4px"
                      @click="sendJobAction(job.task_id, 'approve')" 
                      title="Запустить установку с этими параметрами"
                    >
                      Подтвердить
                    </button>
                    <button 
                      class="btn btn-outline btn-sm" 
                      style="color: var(--red); border-color: var(--red-bg);"
                      @click="sendJobAction(job.task_id, 'reject')" 
                      title="Отменить автоматическую установку"
                    >
                      Отклонить
                    </button>
                  </template>
                  
                  <!-- Обычные действия -->
                  <template v-else>
                    <button 
                      class="btn btn-outline btn-sm" 
                      style="margin-right: 4px"
                      @click="toggleLogs(job.task_id)"
                    >
                      {{ openLogs[job.task_id] ? 'Закрыть' : 'Логи' }}
                    </button>
                    <button 
                      class="btn btn-outline btn-sm" 
                      style="color: var(--red); border-color: var(--red-bg);"
                      @click="deleteJob(job.task_id)" 
                      title="Удалить задачу из истории"
                    >
                      Удалить
                    </button>
                  </template>
                </td>
              </tr>
              
              <!-- Строка с SSE логами -->
              <tr :id="`log-row-${job.task_id}`" class="log-row" :class="{ open: openLogs[job.task_id] }">
                <td colspan="6">
                  <div class="log-inner">
                    <div :id="`log-pre-${job.task_id}`" class="logs-pre">
                      <span 
                        v-for="(line, idx) in logLines[job.task_id] || []" 
                        :key="idx" 
                        :class="getLineClass(line)"
                      >{{ line }}<br></span>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, inject, nextTick } from 'vue';
import { apiFetch } from '../api';
import StatsRow from '../components/StatsRow.vue';
import { usePolling } from '../composables/usePolling';

const jobs = ref([]);
const loading = ref(true);

const openLogs = ref({});
const logLines = ref({});
const logSources = {}; // Хранилище EventSource объектов

const counts = ref({ done: 0, active: 0, failed: 0 });

const statsItems = computed(() => [
  { value: counts.value.done, label: 'Завершено успешно', color: 'green', icon: 'check' },
  { value: counts.value.active, label: 'В процессе установки', color: 'blue', icon: 'inbox' },
  { value: counts.value.failed, label: 'Ошибок установки', color: 'red', icon: 'error' }
]);

const stateBadge = (state) => {
  const m = {
    done: 'done', failed: 'failed',
    pending: 'pending', waiting_approval: 'pending', waiting: 'pending',
    routing: 'progress', parsing: 'progress', probing: 'progress',
    copying: 'progress', installing: 'progress', verifying: 'progress'
  };
  return m[state] || 'progress';
};

const getLineClass = (line) => {
  if (line.includes('[ERROR]') || line.includes('FAIL')) return 'term-line-err';
  if (line.includes('[OK]') || line.includes('done')) return 'term-line-ok';
  if (line.includes('[WARN]')) return 'term-line-warn';
  if (line.includes('[SYSTEM]')) return 'term-line-sys';
  return 'term-line-info';
};

// Загрузка данных журнала
const fetchHistory = async () => {
  try {
    const data = await apiFetch('/admin/api/print-jobs');
    jobs.value = data || [];
    
    // Обновляем статистику
    let done = 0, active = 0, failed = 0;
    const inProgress = ['probing', 'copying', 'installing', 'verifying', 'routing', 'parsing', 'waiting', 'pending'];

    jobs.value.forEach(j => {
      if (j.state === 'done') done++;
      else if (j.state === 'failed') failed++;
      else if (inProgress.includes(j.state)) active++;
    });

    counts.value = { done, active, failed };
  } catch (err) {
    console.error('Ошибка загрузки истории:', err);
  } finally {
    loading.value = false;
  }
};

// Подтверждение/отклонение задачи
const sendJobAction = async (taskId, action) => {
  const actionNames = { approve: 'установить', reject: 'отменить' };
  if (!confirm(`Вы действительно хотите ${actionNames[action] || action} принтер для заявки #${taskId}?`)) return;
  
  try {
    await apiFetch(`/admin/api/print-jobs/${taskId}/action`, {
      method: 'POST',
      body: JSON.stringify({ action })
    });
    await fetchHistory();
  } catch (err) {
    alert('Ошибка: ' + err.message);
  }
};

// Удаление задачи
const deleteJob = async (taskId) => {
  if (!confirm(`Вы действительно хотите удалить задачу #${taskId} и всю её историю из Redis?`)) return;
  
  try {
    // Если открыты логи для этой задачи, закроем SSE
    if (logSources[taskId]) {
      logSources[taskId].close();
      delete logSources[taskId];
    }
    
    await apiFetch(`/admin/api/print-jobs/${taskId}`, {
      method: 'DELETE'
    });
    await fetchHistory();
  } catch (err) {
    alert('Ошибка при удалении: ' + err.message);
  }
};

// Управление отображением логов по SSE
const toggleLogs = (jobId) => {
  const isOpen = !!openLogs.value[jobId];
  
  if (isOpen) {
    openLogs.value[jobId] = false;
    if (logSources[jobId]) {
      logSources[jobId].close();
      delete logSources[jobId];
    }
  } else {
    openLogs.value[jobId] = true;
    logLines.value[jobId] = ['[SYSTEM] Подключение к потоку логов...'];
    
    const sse = new EventSource(`/admin/api/print-jobs/${jobId}/logs`);
    let firstMsg = true;
    
    sse.onmessage = (e) => {
      if (firstMsg) {
        logLines.value[jobId] = [];
        firstMsg = false;
      }
      logLines.value[jobId].push(e.data);
      
      // Скроллим блок вниз
      nextTick(() => {
        const el = document.getElementById(`log-pre-${jobId}`);
        if (el) el.scrollTop = el.scrollHeight;
      });
    };
    
    sse.onerror = () => {
      logLines.value[jobId].push('[SYSTEM] Поток логов завершён.');
      sse.close();
      delete logSources[jobId];
    };
    
    logSources[jobId] = sse;
  }
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
  
  // Закрываем все открытые SSE при уничтожении
  Object.keys(logSources).forEach(jobId => {
    logSources[jobId].close();
  });
});
</script>

<style scoped>
.log-row {
  display: none;
}
.log-row.open {
  display: table-row;
}
</style>
