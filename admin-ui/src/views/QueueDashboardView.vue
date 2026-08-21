<template>
  <div class="queue-dashboard">
    <!-- Верхняя панель со статистикой и фильтрами -->
    <div class="dashboard-header">
      <div class="header-title">
        <h2>⚡ Оперативная очередь 1-й линии</h2>
        <span class="subtitle">Интерактивный триаж заявок IntraService (Фильтр #{{ filterId }})</span>
      </div>

      <div class="header-actions">
        <div class="stats-badge">
          <span>В очереди:</span>
          <strong>{{ tasks.length }}</strong>
        </div>
        <button class="btn btn-primary" :disabled="loading" @click="fetchQueue">
          <svg v-if="loading" class="spin" viewBox="0 0 24 24" width="16" height="16">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none" opacity="0.3"/>
            <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-width="4" fill="none"/>
          </svg>
          <svg v-else viewBox="0 0 24 24" width="16" height="16">
            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" stroke="currentColor" stroke-width="2" fill="none"/>
          </svg>
          <span>Обновить</span>
        </button>
      </div>
    </div>

    <!-- Фильтры по категориям -->
    <div class="filter-tabs">
      <button 
        v-for="cat in categories" 
        :key="cat.key" 
        class="filter-tab" 
        :class="{ active: currentCategory === cat.key }"
        @click="currentCategory = cat.key"
      >
        {{ cat.label }}
        <span class="count-pill">{{ getCategoryCount(cat.key) }}</span>
      </button>
    </div>

    <!-- Список задач -->
    <div v-if="loading && tasks.length === 0" class="loading-state">
      <div class="spinner"></div>
      <p>Загрузка очереди заявок из IntraService...</p>
    </div>

    <div v-else-if="filteredTasks.length === 0" class="empty-state">
      <div class="empty-icon">🎉</div>
      <h3>Очередь чиста!</h3>
      <p>Нет открытых заявок, требующих обработки по выбранному фильтру.</p>
    </div>

    <div v-else class="tasks-grid">
      <div 
        v-for="t in filteredTasks" 
        :key="t.id" 
        class="task-card"
        :class="{ 'is-submitting': submittingIds.has(t.id), 'is-done': doneIds.has(t.id) }"
      >
        <!-- Карточка: Верхняя часть -->
        <div class="card-header">
          <div class="task-id-badge">
            <a :href="`https://servicedesk.corporate.loc/Task/View/${t.id}`" target="_blank" class="task-link">
              #{{ t.id }} ↗
            </a>
            <span class="category-badge" :class="t.badge_color">
              {{ t.category_label }}
            </span>
          </div>

          <div class="score-badge" :class="getScoreClass(t.score)">
            ⭐ {{ t.score }}/10
          </div>
        </div>

        <!-- Информация о заявителе -->
        <div class="applicant-row">
          <div class="applicant-name">
            <strong>{{ t.creator }}</strong>
            <span v-if="t.department" class="muted">({{ t.department }})</span>
          </div>
          <div class="applicant-meta">
            <span v-if="t.phone" class="meta-item">📞 {{ t.phone }}</span>
            <span v-if="t.room" class="meta-item">📍 каб. {{ t.room }}</span>
            <span v-if="t.pc_name" class="meta-item pc-badge">💻 {{ t.pc_name }}</span>
          </div>
        </div>

        <!-- Описание проблемы -->
        <div class="problem-box">
          <div class="task-title">{{ t.name }}</div>
          <div v-if="t.description" class="task-description">{{ t.description }}</div>
        </div>

        <!-- Редактируемый блок ответа -->
        <div class="response-section">
          <div class="response-header">
            <label>💬 Ответ заявителю:</label>
            <span class="target-status-tag" :class="t.badge_color">
              ➔ {{ t.target_status_name }}
            </span>
          </div>
          <textarea 
            v-model="t.suggested_comment" 
            class="comment-textarea" 
            rows="3" 
            placeholder="Введите комментарий для заявителя..."
          ></textarea>
        </div>

        <!-- Кнопки действий -->
        <div class="card-footer">
          <div class="status-selector">
            <select v-model="t.target_status_id" class="select-status">
              <option :value="29">29 (Выполнена)</option>
              <option :value="30">30 (Отменена - Редирект)</option>
              <option :value="48">48 (Ожидание устройства)</option>
              <option :value="27">27 (В работе)</option>
            </select>
          </div>

          <div class="action-buttons">
            <button 
              class="btn btn-success" 
              :disabled="submittingIds.has(t.id) || doneIds.has(t.id)"
              @click="applyAction(t)"
            >
              <span v-if="submittingIds.has(t.id)">Применение...</span>
              <span v-else-if="doneIds.has(t.id)">✓ Выполнено</span>
              <span v-else>Применить действие</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';

const filterId = ref(984);
const loading = ref(false);
const tasks = ref([]);
const currentCategory = ref('all');
const submittingIds = ref(new Set());
const doneIds = ref(new Set());

const categories = [
  { key: 'all', label: 'Все заявки' },
  { key: 'wlan_access', label: 'Wi-Fi (WLAN)' },
  { key: 'redirect_1c', label: '1С:Предприятие' },
  { key: 'redirect_directum', label: 'Directum' },
  { key: 'hardware_repair', label: 'Ремонт ПК (48)' },
  { key: 'redirect_security', label: 'ИБ / Пароли' },
  { key: 'redirect_printers', label: 'Оргтехника' },
  { key: 'general', label: 'Прочие' },
];

const fetchQueue = async () => {
  loading.value = true;
  try {
    const res = await fetch(`/admin/api/queue?filter_id=${filterId.value}&limit=30`);
    if (res.ok) {
      const data = await res.json();
      tasks.value = data.tasks || [];
    }
  } catch (e) {
    console.error('Ошибка загрузки очереди:', e);
  } finally {
    loading.value = false;
  }
};

const getCategoryCount = (key) => {
  if (key === 'all') return tasks.value.length;
  return tasks.value.filter(t => t.rule_type === key).length;
};

const filteredTasks = computed(() => {
  if (currentCategory.value === 'all') return tasks.value;
  return tasks.value.filter(t => t.rule_type === currentCategory.value);
});

const getScoreClass = (score) => {
  if (score >= 9) return 'score-high';
  if (score >= 7) return 'score-mid';
  return 'score-low';
};

const applyAction = async (task) => {
  submittingIds.value.add(task.id);
  try {
    const res = await fetch(`/admin/api/tasks/${task.id}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        status_id: task.target_status_id,
        comment: task.suggested_comment,
        minutes: 10,
        executor_ids: '8664,10502',
        is_private: false,
      }),
    });

    if (res.ok) {
      doneIds.value.add(task.id);
      setTimeout(() => {
        tasks.value = tasks.value.filter(t => t.id !== task.id);
        doneIds.value.delete(task.id);
      }, 1200);
    } else {
      const err = await res.json();
      alert(`Ошибка: ${err.detail || 'Не удалось применить действие'}`);
    }
  } catch (e) {
    alert(`Сетевая ошибка: ${e}`);
  } finally {
    submittingIds.value.delete(task.id);
  }
};

onMounted(() => {
  fetchQueue();
});
</script>

<style scoped>
.queue-dashboard {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 1.5rem;
  max-width: 1400px;
  margin: 0 auto;
}

.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border-color, #20242e);
  padding-bottom: 1rem;
}

.header-title h2 {
  font-size: 1.4rem;
  font-weight: 700;
  color: #fff;
  margin: 0 0 0.25rem 0;
}

.subtitle {
  font-size: 0.85rem;
  color: #8b949e;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.stats-badge {
  background: rgba(56, 139, 253, 0.1);
  border: 1px solid rgba(56, 139, 253, 0.3);
  padding: 0.4rem 0.8rem;
  border-radius: 6px;
  font-size: 0.85rem;
  color: #58a6ff;
  display: flex;
  gap: 0.4rem;
}

.filter-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.filter-tab {
  background: #161b22;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 0.4rem 0.8rem;
  border-radius: 6px;
  font-size: 0.82rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  transition: all 0.15s ease;
}

.filter-tab:hover {
  background: #21262d;
  border-color: #8b949e;
}

.filter-tab.active {
  background: #1f6feb;
  color: #fff;
  border-color: #388bfd;
}

.count-pill {
  background: rgba(0, 0, 0, 0.3);
  padding: 0.1rem 0.4rem;
  border-radius: 10px;
  font-size: 0.75rem;
}

.tasks-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
  gap: 1.25rem;
}

.task-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  transition: all 0.2s ease;
}

.task-card:hover {
  border-color: #58a6ff;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}

.task-card.is-done {
  border-color: #238636;
  background: rgba(35, 134, 54, 0.1);
  transform: scale(0.98);
  opacity: 0.7;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.task-id-badge {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.task-link {
  font-size: 1.05rem;
  font-weight: 700;
  color: #58a6ff;
  text-decoration: none;
}

.task-link:hover {
  text-decoration: underline;
}

.category-badge {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
}

.category-badge.success { background: rgba(46, 160, 67, 0.2); color: #3fb950; border: 1px solid #238636; }
.category-badge.warning { background: rgba(210, 153, 34, 0.2); color: #d29922; border: 1px solid #9e6a03; }
.category-badge.primary { background: rgba(88, 166, 255, 0.2); color: #58a6ff; border: 1px solid #1f6feb; }
.category-badge.info { background: rgba(163, 113, 247, 0.2); color: #a371f7; border: 1px solid #8957e5; }
.category-badge.secondary { background: rgba(139, 148, 158, 0.2); color: #8b949e; border: 1px solid #484f58; }

.score-badge {
  font-size: 0.8rem;
  font-weight: 600;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
}

.score-high { color: #3fb950; background: rgba(46, 160, 67, 0.15); }
.score-mid { color: #d29922; background: rgba(210, 153, 34, 0.15); }
.score-low { color: #8b949e; background: rgba(139, 148, 158, 0.15); }

.applicant-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.85rem;
  color: #c9d1d9;
  border-bottom: 1px dashed #21262d;
  padding-bottom: 0.5rem;
}

.applicant-meta {
  display: flex;
  gap: 0.6rem;
  font-size: 0.8rem;
}

.pc-badge {
  background: #21262d;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  color: #79c0ff;
  font-family: monospace;
}

.problem-box {
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 6px;
  padding: 0.75rem;
}

.task-title {
  font-weight: 600;
  color: #f0f6fc;
  margin-bottom: 0.25rem;
  font-size: 0.9rem;
}

.task-description {
  font-size: 0.82rem;
  color: #8b949e;
  line-height: 1.4;
  white-space: pre-wrap;
  max-height: 80px;
  overflow-y: auto;
}

.response-section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.response-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.8rem;
  font-weight: 600;
  color: #8b949e;
}

.target-status-tag {
  font-size: 0.75rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

.comment-textarea {
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 6px;
  color: #e6edf3;
  padding: 0.6rem;
  font-size: 0.82rem;
  line-height: 1.4;
  resize: vertical;
  font-family: inherit;
}

.comment-textarea:focus {
  outline: none;
  border-color: #58a6ff;
}

.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.25rem;
}

.select-status {
  background: #21262d;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  font-size: 0.82rem;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.9rem;
  border-radius: 6px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: all 0.15s ease;
}

.btn-primary { background: #1f6feb; color: #fff; }
.btn-primary:hover { background: #388bfd; }
.btn-success { background: #238636; color: #fff; }
.btn-success:hover { background: #2ea043; }

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.loading-state, .empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: #8b949e;
}

.empty-icon {
  font-size: 2.5rem;
  margin-bottom: 0.5rem;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  100% { transform: rotate(360deg); }
}
</style>
