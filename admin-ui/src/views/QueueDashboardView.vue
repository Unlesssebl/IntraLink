<template>
  <div class="queue-dashboard">
    <!-- ГЛАВНЫЕ ВКЛАДКИ ПО РЕАЛЬНЫМ СЕРВИСАМ HELPDESK -->
    <div class="services-tab-nav-container">
      <div class="services-tab-bar">
        <button 
          v-for="svc in queueStore.serviceTabs" 
          :key="svc.key" 
          class="service-tab-btn" 
          :class="{ active: queueStore.activeServiceTab === svc.key }"
          @click="queueStore.activeServiceTab = svc.key"
        >
          <span class="service-tab-icon">{{ svc.icon }}</span>
          <span class="service-tab-title">{{ svc.name }}</span>
          <span class="service-tab-count" :class="{ 'has-items': svc.count > 0 }">{{ svc.count }}</span>
        </button>
      </div>
    </div>

    <!-- Верхняя панель управления и действия выбранного сервиса -->
    <div class="dashboard-header">
      <div class="header-main">
        <div class="title-group">
          <h2>
            <span v-if="queueStore.activeServiceTab === 'all'">📋 Все сервисы очереди</span>
            <span v-else>📂 {{ queueStore.activeServiceTab }}</span>
          </h2>
          <span class="subtitle">
            {{ queueStore.filteredTasks.length }} {{ getTaskWord(queueStore.filteredTasks.length) }} в разделе (Фильтр #{{ queueStore.filterId }})
          </span>
        </div>

        <div class="header-tools">
          <!-- Поисковая строка -->
          <div class="search-input-wrap">
            <svg viewBox="0 0 24 24" width="15" height="15" class="search-icon">
              <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" fill="none"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
            <input 
              v-model="queueStore.searchQuery" 
              type="text" 
              class="search-input" 
              placeholder="Поиск по номеру, ПК, заявителю..."
            />
            <button v-if="queueStore.searchQuery" class="clear-search-btn" @click="queueStore.searchQuery = ''">✕</button>
          </div>

          <!-- Переключатель вида (Карточки / Таблица) -->
          <div class="view-mode-toggle">
            <button 
              class="mode-btn" 
              :class="{ active: queueStore.viewMode === 'cards' }" 
              title="Вид карточек"
              @click="queueStore.viewMode = 'cards'"
            >
              <svg viewBox="0 0 24 24" width="16" height="16">
                <rect x="3" y="3" width="7" height="7" stroke="currentColor" stroke-width="2" fill="none"/>
                <rect x="14" y="3" width="7" height="7" stroke="currentColor" stroke-width="2" fill="none"/>
                <rect x="3" y="14" width="7" height="7" stroke="currentColor" stroke-width="2" fill="none"/>
                <rect x="14" y="14" width="7" height="7" stroke="currentColor" stroke-width="2" fill="none"/>
              </svg>
            </button>
            <button 
              class="mode-btn" 
              :class="{ active: queueStore.viewMode === 'grid' }" 
              title="Компактная таблица"
              @click="queueStore.viewMode = 'grid'"
            >
              <svg viewBox="0 0 24 24" width="16" height="16">
                <line x1="3" y1="6" x2="21" y2="6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="3" y1="12" x2="21" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="3" y1="18" x2="21" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </button>
          </div>

          <!-- Кнопка обновления -->
          <button class="btn btn-primary" :disabled="queueStore.loading" @click="queueStore.fetchQueue">
            <svg v-if="queueStore.loading" class="spin" viewBox="0 0 24 24" width="15" height="15">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none" opacity="0.3"/>
              <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-width="4" fill="none"/>
            </svg>
            <svg v-else viewBox="0 0 24 24" width="15" height="15">
              <polyline points="23 4 23 10 17 10" stroke="currentColor" stroke-width="2" fill="none"/>
              <polyline points="1 20 1 14 7 14" stroke="currentColor" stroke-width="2" fill="none"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" stroke="currentColor" stroke-width="2" fill="none"/>
            </svg>
            <span>Обновить</span>
          </button>
        </div>
      </div>

      <!-- Панель пакетных действий (Bulk Actions Bar) -->
      <div class="bulk-actions-bar">
        <div class="bulk-selection-controls">
          <button class="btn btn-outline btn-xs" @click="queueStore.selectAllFiltered(queueStore.filteredTasks)">
            Выбрать все в «{{ queueStore.activeServiceTab === 'all' ? 'списке' : queueStore.activeServiceTab }}» ({{ queueStore.filteredTasks.length }})
          </button>
          <button class="btn btn-outline btn-xs" @click="queueStore.selectAllConfident(queueStore.filteredTasks)">
            Выбрать типовые (⭐ ≥ 9)
          </button>
          <button v-if="queueStore.selectedTaskIds.size > 0" class="btn btn-ghost btn-xs" @click="queueStore.deselectAll">
            Снять выбор
          </button>
        </div>

        <div v-if="queueStore.selectedTaskIds.size > 0" class="bulk-execute-wrap">
          <span class="selected-count-badge">Выбрано: <strong>{{ queueStore.selectedTaskIds.size }}</strong></span>
          <button 
            class="btn btn-success btn-sm" 
            :disabled="queueStore.submittingIds.size > 0"
            @click="queueStore.applyBulkSelected"
          >
            ⚡ Применить выбранные ({{ queueStore.selectedTaskIds.size }})
          </button>
        </div>
      </div>
    </div>

    <!-- Список задач: Загрузка и пустое состояние -->
    <div v-if="queueStore.loading && queueStore.tasks.length === 0" class="loading-state">
      <div class="spinner"></div>
      <p>Загрузка очереди заявок из IntraService...</p>
    </div>

    <div v-else-if="queueStore.filteredTasks.length === 0" class="empty-state">
      <div class="empty-icon">🎉</div>
      <h3>Сервис чист!</h3>
      <p>
        В разделе <strong>«{{ queueStore.activeServiceTab === 'all' ? 'Все сервисы' : queueStore.activeServiceTab }}»</strong> нет открытых заявок.
      </p>
    </div>

    <!-- Режим 1: Сетка карточек (Cards View) -->
    <div v-else-if="queueStore.viewMode === 'cards'" class="tasks-grid">
      <div 
        v-for="t in queueStore.filteredTasks" 
        :key="t.id" 
        class="task-card"
        :class="{ 
          'is-selected': queueStore.selectedTaskIds.has(t.id),
          'is-submitting': queueStore.submittingIds.has(t.id), 
          'is-done': queueStore.doneIds.has(t.id) 
        }"
      >
        <!-- Карточка: Верхняя часть -->
        <div class="card-header">
          <div class="header-left">
            <label class="custom-checkbox" @click.stop>
              <input 
                type="checkbox" 
                :checked="queueStore.selectedTaskIds.has(t.id)"
                @change="queueStore.toggleSelect(t.id)"
              />
              <span class="checkmark"></span>
            </label>

            <a 
              :href="`https://servicedesk.corporate.loc/Task/View/${t.id}`" 
              target="_blank" 
              class="task-id-link"
            >
              #{{ t.id }} ↗
            </a>
            <span class="category-badge" :class="t.badge_color">
              {{ t.category_label }}
            </span>
          </div>

          <div class="header-right">
            <div class="score-badge" :class="getScoreClass(t.score)">
              ⭐ {{ t.score }}/10
            </div>
            <button class="btn btn-outline btn-xs drawer-btn" @click="queueStore.openTaskDrawer(t.id)">
              <span v-if="t.has_attachments">📎</span>
              <span>Детали</span>
            </button>
          </div>
        </div>

        <!-- Сервисный блок (Исходный и Целевой) -->
        <div class="service-meta-box">
          <div class="service-meta-row">
            <span class="service-origin-label">📂 Сервис:</span>
            <span class="service-origin-val">{{ t.service_name }}</span>
          </div>
          <div v-if="t.is_redirect" class="service-redirect-row">
            <span class="service-redirect-label">⚠️ Неверный раздел ➔ Целевой сервис:</span>
            <span class="service-redirect-val">{{ t.target_service_name }}</span>
          </div>
        </div>

        <!-- Информация о заявителе и ПК -->
        <div class="applicant-row">
          <div class="applicant-info">
            <span class="applicant-name">👤 {{ t.creator }}</span>
            <span v-if="t.department" class="applicant-dept">({{ t.department }})</span>
          </div>

          <div class="applicant-meta">
            <span v-if="t.phone" class="meta-tag">📞 {{ t.phone }}</span>
            <span v-if="t.room" class="meta-tag">📍 каб. {{ t.room }}</span>
            <span v-if="t.pc_name" class="meta-tag host-tag" :class="getHostDiagClass(t.pc_name)">
              💻 {{ t.pc_name }}
              <span class="ping-sub">{{ getHostDiagText(t.pc_name) }}</span>
            </span>
          </div>
        </div>

        <!-- Описание проблемы -->
        <div class="problem-box">
          <div class="task-title">{{ t.name }}</div>
          <div v-if="t.description" class="task-description">{{ t.description }}</div>
        </div>

        <!-- Редактируемый блок ответа и выбор шаблона -->
        <div class="response-section">
          <div class="response-toolbar">
            <label class="response-label">💬 Корпоративный ответ:</label>
            <select 
              :value="t.template_key" 
              class="select-template-inline"
              @change="(e) => queueStore.selectTemplateForTask(t, e.target.value)"
            >
              <option v-for="tmpl in queueStore.templates" :key="tmpl.key" :value="tmpl.key">
                {{ tmpl.name }}
              </option>
            </select>
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
          <div class="status-selector-wrap">
            <select v-model="t.target_status_id" class="select-status">
              <option :value="29">29 (Выполнена)</option>
              <option :value="30">30 (Отменена - Редирект)</option>
              <option :value="48">48 (Ожидание устройства - Ремонт 112)</option>
              <option :value="35">35 (Требует уточнения)</option>
              <option :value="27">27 (В работе)</option>
            </select>
            <span class="expenses-hint">⏱️ {{ t.expenses || 10 }} мин</span>
          </div>

          <div class="action-buttons">
            <button 
              class="btn btn-success btn-sm" 
              :disabled="queueStore.submittingIds.has(t.id) || queueStore.doneIds.has(t.id)"
              @click="queueStore.applySingleAction(t)"
            >
              <span v-if="queueStore.submittingIds.has(t.id)">Применение...</span>
              <span v-else-if="queueStore.doneIds.has(t.id)">✓ Выполнено</span>
              <span v-else>⚡ Применить ({{ t.expenses || 10 }} мин)</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Режим 2: Компактная таблица (Table Grid View) -->
    <div v-else class="table-card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th width="40">
                <input 
                  type="checkbox" 
                  @change="(e) => e.target.checked ? queueStore.selectAllFiltered(queueStore.filteredTasks) : queueStore.deselectAll()"
                />
              </th>
              <th>№ заявки</th>
              <th>Заявитель</th>
              <th>ПК / Статус сети</th>
              <th>Тема & Сервис</th>
              <th>Шаблон & Статус</th>
              <th width="140">Действие</th>
            </tr>
          </thead>
          <tbody>
            <tr 
              v-for="t in queueStore.filteredTasks" 
              :key="t.id"
              :class="{ 
                'is-selected': queueStore.selectedTaskIds.has(t.id),
                'is-submitting': queueStore.submittingIds.has(t.id), 
                'is-done': queueStore.doneIds.has(t.id) 
              }"
            >
              <td>
                <input 
                  type="checkbox" 
                  :checked="queueStore.selectedTaskIds.has(t.id)"
                  @change="queueStore.toggleSelect(t.id)"
                />
              </td>
              <td class="td-mono">
                <a :href="`https://servicedesk.corporate.loc/Task/View/${t.id}`" target="_blank" class="task-id-link">
                  #{{ t.id }}
                </a>
              </td>
              <td>
                <strong>{{ t.creator }}</strong>
                <div v-if="t.room || t.phone" class="table-sub-text">
                  {{ t.room ? 'каб. ' + t.room : '' }} {{ t.phone ? 'тел. ' + t.phone : '' }}
                </div>
              </td>
              <td>
                <span v-if="t.pc_name" class="table-host-pill" :class="getHostDiagClass(t.pc_name)">
                  {{ t.pc_name }} ({{ getHostDiagText(t.pc_name) }})
                </span>
                <span v-else class="muted">—</span>
              </td>
              <td>
                <div class="table-task-name" :title="t.name">{{ t.name }}</div>
                <div class="table-svc-row">
                  <span class="table-service-name">{{ t.service_name }}</span>
                  <span class="category-badge table-badge" :class="t.badge_color">{{ t.category_label }}</span>
                  <span v-if="t.is_redirect" class="table-redirect-pill">➔ {{ t.target_service_name }}</span>
                </div>
              </td>
              <td>
                <select 
                  :value="t.template_key" 
                  class="select-template-table"
                  @change="(e) => queueStore.selectTemplateForTask(t, e.target.value)"
                >
                  <option v-for="tmpl in queueStore.templates" :key="tmpl.key" :value="tmpl.key">
                    {{ tmpl.name }}
                  </option>
                </select>
                <div class="table-sub-status">➔ {{ t.target_status_name }}</div>
              </td>
              <td>
                <div class="table-actions">
                  <button 
                    class="btn btn-success btn-xs" 
                    :disabled="queueStore.submittingIds.has(t.id) || queueStore.doneIds.has(t.id)"
                    @click="queueStore.applySingleAction(t)"
                  >
                    ⚡ Применить
                  </button>
                  <button class="btn btn-outline btn-xs" @click="queueStore.openTaskDrawer(t.id)">
                    👁️
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import { useQueueStore } from '../stores/queue';

const queueStore = useQueueStore();

onMounted(async () => {
  await queueStore.fetchQueue();
});

const getTaskWord = (count) => {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod100 >= 11 && mod100 <= 19) return 'заявок';
  if (mod10 === 1) return 'заявка';
  if (mod10 >= 2 && mod10 <= 4) return 'заявки';
  return 'заявок';
};

const getScoreClass = (score) => {
  if (score >= 9) return 'score-high';
  if (score >= 7) return 'score-med';
  return 'score-low';
};

const getHostDiagClass = (pcName) => {
  if (!pcName) return '';
  const status = queueStore.hostStatusMap[pcName.trim()];
  if (!status || status.loading) return 'diag-loading';
  return status.is_online ? 'diag-online' : 'diag-offline';
};

const getHostDiagText = (pcName) => {
  if (!pcName) return '';
  const status = queueStore.hostStatusMap[pcName.trim()];
  if (!status || status.loading) return '⚪ проверка...';
  return status.is_online ? `🟢 ${status.avg_rtt || '2ms'}` : '🔴 офлайн';
};
</script>

<style scoped>
.queue-dashboard {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

/* Service Primary Navigation Tabs */
.services-tab-nav-container {
  display: flex;
  overflow-x: auto;
  padding-bottom: 0.25rem;
}
.services-tab-bar {
  display: flex;
  gap: 0.5rem;
  padding: 0.35rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.service-tab-btn {
  background: none;
  border: 1px solid transparent;
  color: var(--text-2);
  padding: 0.6rem 1rem;
  border-radius: var(--radius-sm);
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.55rem;
  white-space: nowrap;
  transition: all 0.15s cubic-bezier(0.16, 1, 0.3, 1);
}
.service-tab-btn:hover {
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
}
.service-tab-btn.active {
  background: var(--primary);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 4px 12px var(--primary-glow);
}
.service-tab-icon {
  font-size: 1rem;
}
.service-tab-title {
  letter-spacing: -0.01em;
}
.service-tab-count {
  font-size: 0.72rem;
  padding: 0.15rem 0.45rem;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.12);
  color: var(--text-2);
}
.service-tab-btn.active .service-tab-count {
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
}
.service-tab-count.has-items {
  font-weight: 700;
}

/* Dashboard Header */
.dashboard-header {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem 1.5rem;
}

.header-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.title-group h2 {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.02em;
}
.subtitle {
  font-size: 0.82rem;
  color: var(--text-2);
}

.header-tools {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.search-input-wrap {
  position: relative;
  display: flex;
  align-items: center;
  width: 250px;
}
.search-icon {
  position: absolute;
  left: 0.75rem;
  color: var(--text-3);
  pointer-events: none;
}
.search-input {
  width: 100%;
  padding: 0.45rem 1.8rem 0.45rem 2.2rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.85rem;
  outline: none;
}
.search-input:focus {
  border-color: var(--primary);
}
.clear-search-btn {
  position: absolute;
  right: 0.6rem;
  background: none;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  font-size: 0.75rem;
}

.view-mode-toggle {
  display: flex;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 2px;
}
.mode-btn {
  background: none;
  border: none;
  color: var(--text-3);
  padding: 0.35rem 0.5rem;
  border-radius: 4px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.mode-btn.active {
  background: var(--primary);
  color: #fff;
}

.bulk-actions-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
  gap: 1rem;
  flex-wrap: wrap;
}
.bulk-selection-controls {
  display: flex;
  gap: 0.5rem;
}
.bulk-execute-wrap {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.selected-count-badge {
  font-size: 0.85rem;
  color: var(--text);
}

/* Tasks Grid (Cards) */
.tasks-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(460px, 1fr));
  gap: 1.25rem;
}

.task-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  position: relative;
}
.task-card:hover {
  border-color: var(--border-hover);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.3);
}
.task-card.is-selected {
  border-color: var(--primary);
  background: rgba(79, 70, 229, 0.04);
}
.task-card.is-submitting {
  opacity: 0.6;
  pointer-events: none;
}
.task-card.is-done {
  border-color: var(--green);
  background: rgba(16, 185, 129, 0.04);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.task-id-link {
  font-family: monospace;
  font-weight: 700;
  color: var(--primary);
  font-size: 0.95rem;
  text-decoration: none;
}
.task-id-link:hover {
  text-decoration: underline;
}

.score-badge {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
}
.score-high {
  background: rgba(16, 185, 129, 0.15);
  color: var(--green);
}
.score-med {
  background: rgba(245, 158, 11, 0.15);
  color: var(--yellow);
}
.score-low {
  background: rgba(244, 63, 94, 0.15);
  color: var(--red);
}

/* Service Meta Box */
.service-meta-box {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.75rem;
}
.service-meta-row, .service-redirect-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
}
.service-origin-label {
  color: var(--text-3);
}
.service-origin-val {
  font-weight: 600;
  color: var(--text-2);
}
.service-redirect-label {
  color: var(--yellow);
  font-weight: 500;
}
.service-redirect-val {
  font-weight: 700;
  color: var(--yellow);
}

.applicant-row {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.65rem 0.85rem;
}
.applicant-info {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.applicant-name {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--text);
}
.applicant-dept {
  font-size: 0.78rem;
  color: var(--text-3);
}

.applicant-meta {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.meta-tag {
  font-size: 0.75rem;
  color: var(--text-2);
}
.host-tag {
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.04);
}
.host-tag.diag-online {
  color: var(--green);
}
.host-tag.diag-offline {
  color: var(--red);
}
.ping-sub {
  font-size: 0.7rem;
  opacity: 0.85;
}

.problem-box {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.task-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--text);
  line-height: 1.35;
}
.task-description {
  font-size: 0.82rem;
  color: var(--text-2);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.response-section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.response-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.response-label {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-2);
}
.select-template-inline {
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--text);
  font-size: 0.78rem;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  outline: none;
  max-width: 220px;
}

.comment-textarea {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.82rem;
  line-height: 1.4;
  padding: 0.6rem 0.75rem;
  outline: none;
  resize: vertical;
  font-family: inherit;
}
.comment-textarea:focus {
  border-color: var(--primary);
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--border);
}
.status-selector-wrap {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.select-status {
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 0.35rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
  outline: none;
}
.expenses-hint {
  font-size: 0.75rem;
  color: var(--text-3);
}

/* Custom Checkbox */
.custom-checkbox {
  position: relative;
  display: inline-block;
  width: 18px;
  height: 18px;
  cursor: pointer;
}
.custom-checkbox input {
  opacity: 0;
  width: 0;
  height: 0;
}
.checkmark {
  position: absolute;
  inset: 0;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 4px;
  transition: all 0.15s;
}
.custom-checkbox:hover .checkmark {
  border-color: var(--border-hover);
}
.custom-checkbox input:checked ~ .checkmark {
  background: var(--primary);
  border-color: var(--primary);
}
.custom-checkbox input:checked ~ .checkmark:after {
  content: "";
  position: absolute;
  display: block;
  left: 6px;
  top: 2px;
  width: 4px;
  height: 9px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

/* Table Card View */
.table-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.table-sub-text {
  font-size: 0.72rem;
  color: var(--text-3);
}
.table-task-name {
  font-size: 0.85rem;
  font-weight: 500;
  max-width: 320px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.table-svc-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.25rem;
}
.table-service-name {
  font-size: 0.72rem;
  color: var(--text-3);
}
.table-badge {
  display: inline-block;
}
.table-redirect-pill {
  font-size: 0.72rem;
  color: var(--yellow);
  font-weight: 600;
}
.table-host-pill {
  font-size: 0.75rem;
  padding: 0.2rem 0.45rem;
  border-radius: 4px;
  background: var(--surface-2);
}
.select-template-table {
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--text);
  font-size: 0.75rem;
  padding: 0.2rem 0.4rem;
  border-radius: 4px;
  max-width: 180px;
}
.table-sub-status {
  font-size: 0.72rem;
  color: var(--text-2);
  margin-top: 0.2rem;
}
.table-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
</style>
