<template>
  <Teleport to="body">
    <div v-if="queueStore.activeDrawerTaskId" class="drawer-backdrop" @click="queueStore.closeTaskDrawer">
      <div class="drawer-panel" @click.stop>
        <!-- Шапка шторки -->
        <div class="drawer-header">
          <div class="drawer-title-wrap">
            <div class="drawer-id-badge">
              <a 
                :href="`https://servicedesk.corporate.loc/Task/View/${queueStore.activeDrawerTaskId}`" 
                target="_blank" 
                class="external-task-link"
                title="Открыть в IntraService"
              >
                #{{ queueStore.activeDrawerTaskId }}
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" style="margin-left: 2px;"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg>
              </a>
              <button 
                class="copy-mini-btn" 
                title="Скопировать номер заявки" 
                @click="clipboard.copyText(String(queueStore.activeDrawerTaskId), 'Номер заявки')"
              >
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </button>
              <span v-if="details" class="category-badge" :class="details.cls_info?.badge_color">
                {{ details.cls_info?.category_label }}
              </span>
            </div>
            <h3 class="drawer-task-name">{{ details?.name || 'Загрузка данных заявки...' }}</h3>
          </div>
          <button class="drawer-close-btn" @click="queueStore.closeTaskDrawer" aria-label="Закрыть">
            <svg viewBox="0 0 24 24" width="20" height="20">
              <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
        </div>

        <!-- Тело шторки -->
        <div class="drawer-content">
          <!-- Спиннер загрузки -->
          <div v-if="queueStore.drawerLoading" class="drawer-loading">
            <div class="spinner"></div>
            <span>Получение данных из IntraService...</span>
          </div>

          <template v-else-if="details">
            <!-- Блок заявителя и ПК -->
            <div class="drawer-section">
              <div class="section-label">Заявитель и рабочее место</div>
              <div class="meta-grid">
                <div class="meta-card">
                  <div class="meta-card-head">
                    <span class="meta-title">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" class="section-icon"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                      <span>Заявитель</span>
                    </span>
                    <button 
                      v-if="details.creator" 
                      class="copy-pill-btn" 
                      title="Скопировать имя" 
                      @click="clipboard.copyText(details.creator, 'Заявитель')"
                    >
                      Копировать
                    </button>
                  </div>
                  <strong class="meta-val">{{ details.creator }}</strong>
                  <span v-if="details.department" class="meta-sub">{{ details.department }}</span>
                </div>

                <div class="meta-card">
                  <div class="meta-card-head">
                    <span class="meta-title">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" class="section-icon"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
                      <span>Контакты & Кабинет</span>
                    </span>
                    <button 
                      v-if="details.phone" 
                      class="copy-pill-btn" 
                      title="Скопировать телефон" 
                      @click="clipboard.copyText(details.phone, 'Телефон')"
                    >
                      Копировать
                    </button>
                  </div>
                  <strong class="meta-val">{{ details.phone ? details.phone : 'Телефон не указан' }}</strong>
                  <span v-if="details.room" class="meta-sub">Кабинет: {{ details.room }}</span>
                </div>

                <div class="meta-card host-card">
                  <div class="meta-card-head">
                    <span class="meta-title">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" class="section-icon"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                      <span>Рабочая станция</span>
                    </span>
                    <button 
                      v-if="details.pc_name" 
                      class="copy-pill-btn" 
                      title="Скопировать имя ПК" 
                      @click="clipboard.copyText(details.pc_name, 'Имя ПК')"
                    >
                      Копировать
                    </button>
                  </div>
                  <strong class="meta-val">{{ details.pc_name || 'Не указан' }}</strong>
                  <!-- Статус диагностики хоста -->
                  <div v-if="details.pc_name" class="host-diag-status">
                    <span v-if="hostDiag?.loading" class="diag-pill loading">Проверка сети...</span>
                    <span v-else-if="hostDiag?.is_online" class="diag-pill online">
                      <span class="status-dot online"></span> {{ hostDiag.avg_rtt }} &middot; SMB: {{ hostDiag.smb_ok ? 'OK' : '—' }} &middot; WinRM: {{ hostDiag.winrm_ok ? 'OK' : '—' }}
                    </span>
                    <span v-else class="diag-pill offline">
                      <span class="status-dot offline"></span> Офлайн / Недоступен
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Блок раздела каталога IntraService -->
            <div class="drawer-section">
              <div class="section-label">Раздел каталога IntraService</div>
              <div class="service-catalog-card">
                <div class="service-catalog-head">
                  <span class="service-root-pill">{{ details.root_service_name || '11. Общие вопросы' }}</span>
                  <span v-if="details.service_name && details.service_name !== details.root_service_name" class="service-sub-pill">{{ details.service_name }}</span>
                </div>
                <div v-if="details.service_path" class="service-catalog-path">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink: 0;"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                  <span>{{ details.service_path }}</span>
                </div>
              </div>
            </div>

            <!-- Описание заявки -->
            <div class="drawer-section">
              <div class="section-label">Описание инцидента</div>
              <div class="description-box">
                {{ details.description || 'Описание отсутствует' }}
              </div>
            </div>

            <!-- Вложения и скриншоты -->
            <div v-if="details.attachments && details.attachments.length > 0" class="drawer-section">
              <div class="section-label">Вложения и скриншоты ({{ details.attachments.length }})</div>
              <div class="attachments-grid">
                <div 
                  v-for="att in details.attachments" 
                  :key="att.id" 
                  class="attachment-item"
                  @click="openLightbox(att)"
                >
                  <div class="att-icon">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                  </div>
                  <div class="att-info">
                    <span class="att-name" :title="att.name">{{ att.name }}</span>
                    <span class="att-size">{{ formatFileSize(att.size) }}</span>
                  </div>
                  <button class="att-zoom-btn" title="Просмотр">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9M9 21 3 21 3 15M21 3l-7 7M3 21l7-7"/></svg>
                  </button>
                </div>
              </div>
            </div>

            <!-- История комментариев -->
            <div class="drawer-section">
              <div class="section-label">История переписки ({{ details.comments?.length || 0 }})</div>
              <div v-if="!details.comments || details.comments.length === 0" class="empty-comments">
                Комментариев пока нет
              </div>
              <div v-else class="comments-feed">
                <div 
                  v-for="c in details.comments" 
                  :key="c.id" 
                  class="comment-bubble" 
                  :class="{ 'is-private': c.is_private }"
                >
                  <div class="comment-head">
                    <strong class="comment-author">{{ c.author }}</strong>
                    <span class="comment-date">{{ formatDate(c.created) }}</span>
                  </div>
                  <div class="comment-text">{{ c.text }}</div>
                </div>
              </div>
            </div>

            <!-- Быстрые действия и шаблон -->
            <div class="drawer-section action-section">
              <div class="section-label">Действие и ответ заявителю</div>
              
              <!-- Селектор шаблонов -->
              <div class="template-selector-box">
                <label class="form-label">Корпоративный шаблон:</label>
                <select 
                  v-model="selectedTmplKey" 
                  class="form-control select-template"
                  @change="handleTemplateChange"
                >
                  <option v-for="t in queueStore.templates" :key="t.key" :value="t.key">
                    {{ t.name }} (статус {{ t.status_id }})
                  </option>
                </select>
              </div>

              <!-- Текст комментария -->
              <div class="form-group mt-2">
                <label class="form-label">Комментарий заявителю:</label>
                <textarea 
                  v-model="editComment" 
                  rows="4" 
                  class="form-control drawer-textarea"
                  placeholder="Текст ответа заявителю..."
                ></textarea>
              </div>

              <div class="drawer-action-bar">
                <div class="status-summary">
                  Целевой статус: <strong>{{ targetStatusName }}</strong> | Трудозатраты: <strong>{{ expensesMinutes }} мин.</strong>
                  <div class="drawer-kbd-hint">
                    <kbd>Ctrl</kbd> + <kbd>Enter</kbd> применить &middot; <kbd>Esc</kbd> закрыть
                  </div>
                </div>
                <button 
                  class="btn btn-primary" 
                  :disabled="queueStore.submittingIds.has(details.id)"
                  @click="applyFromDrawer"
                >
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  <span v-if="queueStore.submittingIds.has(details.id)">Применение...</span>
                  <span v-else>Применить и закрыть</span>
                </button>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- Модальное окно просмотра изображений (Lightbox) -->
    <ImageLightbox ref="lightboxRef" />
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useQueueStore } from '../stores/queue';
import { useClipboard } from '../composables/useClipboard';
import ImageLightbox from './ImageLightbox.vue';
import type { TaskAttachment, TaskItem } from '../types/task';

const queueStore = useQueueStore();
const clipboard = useClipboard();
const lightboxRef = ref<InstanceType<typeof ImageLightbox> | null>(null);

const details = computed(() => queueStore.drawerTaskDetails);

const selectedTmplKey = ref('general');
const editComment = ref('');
const targetStatusId = ref(27);
const targetStatusName = ref('В работе (27)');
const expensesMinutes = ref(10);

// Синхронизация при загрузке данных
watch(details, (val) => {
  if (val && val.cls_info) {
    selectedTmplKey.value = val.cls_info.template_key || 'general';
    editComment.value = val.cls_info.suggested_comment || '';
    targetStatusId.value = val.cls_info.target_status_id || 27;
    targetStatusName.value = val.cls_info.target_status_name || 'В работе (27)';
    expensesMinutes.value = val.cls_info.expenses || 10;
  }
});

const hostDiag = computed(() => {
  if (details.value && details.value.pc_name) {
    return queueStore.hostStatusMap[details.value.pc_name.trim()];
  }
  return null;
});

const handleTemplateChange = () => {
  const tmpl = queueStore.templatesMap[selectedTmplKey.value];
  if (tmpl) {
    editComment.value = tmpl.template;
    targetStatusId.value = tmpl.status_id;
    targetStatusName.value = tmpl.status_name;
    expensesMinutes.value = tmpl.expenses || 10;
  }
};

const applyFromDrawer = async () => {
  if (!details.value) return;
  const taskObj = {
    id: details.value.id,
    target_status_id: targetStatusId.value,
    suggested_comment: editComment.value,
    expenses: expensesMinutes.value,
  } as TaskItem;

  await queueStore.applySingleAction(taskObj);
  queueStore.closeTaskDrawer();
};

const handleDrawerKeydown = (e: KeyboardEvent) => {
  if (!queueStore.activeDrawerTaskId) return;

  if (e.key === 'Escape') {
    e.preventDefault();
    queueStore.closeTaskDrawer();
  } else if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    applyFromDrawer();
  }
};

onMounted(() => {
  window.addEventListener('keydown', handleDrawerKeydown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleDrawerKeydown);
});

const openLightbox = (att: TaskAttachment) => {
  const url = att.url || `/admin/api/attachments/${att.id}`;
  lightboxRef.value?.open(url, att.name, formatFileSize(att.size));
};

const formatFileSize = (bytes?: number) => {
  if (!bytes) return '';
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
};

const formatDate = (dateStr?: string) => {
  if (!dateStr) return '';
  return dateStr.replace('T', ' ').substring(0, 16);
};
</script>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 15, 15, 0.65);
  backdrop-filter: blur(4px);
  z-index: 500;
  display: flex;
  justify-content: flex-end;
}

.drawer-panel {
  width: 580px;
  max-width: 95vw;
  height: 100vh;
  background: var(--bg-surface);
  border-left: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-floating);
  animation: slide-left 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slide-left {
  from {
    transform: translateX(100%);
  }
  to {
    transform: translateX(0);
  }
}

.drawer-header {
  padding: 1.15rem 1.35rem;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  background: var(--bg-sidebar);
  flex-shrink: 0;
}

.drawer-title-wrap {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.drawer-id-badge {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.external-task-link {
  font-family: var(--font-mono);
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--accent-primary);
  text-decoration: none;
}

.external-task-link:hover {
  text-decoration: underline;
}

.copy-mini-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.85rem;
  opacity: 0.7;
  transition: opacity 0.15s;
}

.copy-mini-btn:hover {
  opacity: 1;
}

.category-badge {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  background: var(--tag-default-bg);
  color: var(--tag-default-text);
}

.category-badge.success {
  background: var(--tag-green-bg);
  color: var(--tag-green-text);
}

.category-badge.warning {
  background: var(--tag-yellow-bg);
  color: var(--tag-yellow-text);
}

.category-badge.primary {
  background: var(--tag-blue-bg);
  color: var(--tag-blue-text);
}

.drawer-task-name {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.35;
}

.drawer-close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 0.35rem;
  border-radius: 6px;
  transition: all 0.15s;
}

.drawer-close-btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.drawer-content {
  flex: 1;
  overflow-y: auto;
  padding: 1.25rem 1.35rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.drawer-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 4rem 1rem;
  gap: 1rem;
  color: var(--text-muted);
}

.drawer-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.section-label {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.65rem;
}

@media (max-width: 500px) {
  .meta-grid {
    grid-template-columns: 1fr;
  }
}

.meta-card {
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0.65rem 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.meta-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.meta-title {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.section-icon {
  color: var(--text-muted);
}

.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-muted);
  margin-right: 4px;
}

.status-dot.online {
  background: #10b981;
}

.status-dot.offline {
  background: #ef4444;
}

.copy-pill-btn {
  background: var(--bg-hover);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
  font-size: 0.65rem;
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}

.copy-pill-btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover-strong);
}

.meta-val {
  font-size: 0.8rem;
  color: var(--text-primary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.meta-sub {
  font-size: 0.68rem;
  color: var(--text-muted);
}

.host-diag-status {
  margin-top: 0.35rem;
}

.diag-pill {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  display: inline-block;
  font-weight: 600;
}

.diag-pill.online {
  background: var(--tag-green-bg);
  color: var(--tag-green-text);
}

.diag-pill.offline {
  background: var(--tag-red-bg);
  color: var(--tag-red-text);
}

.diag-pill.loading {
  background: var(--tag-default-bg);
  color: var(--text-secondary);
}

.service-catalog-card {
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0.65rem 0.85rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.service-catalog-head {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  flex-wrap: wrap;
}

.service-root-pill {
  font-size: 0.76rem;
  font-weight: 600;
  color: var(--tag-blue-text);
  background: var(--tag-blue-bg);
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
}

.service-sub-pill {
  font-size: 0.74rem;
  color: var(--text-secondary);
  background: var(--bg-hover);
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
}

.service-catalog-path {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.72rem;
  color: var(--text-muted);
  margin-top: 0.1rem;
}

.description-box {
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0.85rem;
  font-size: 0.82rem;
  line-height: 1.55;
  color: var(--text-primary);
  white-space: pre-wrap;
  max-height: 250px;
  overflow-y: auto;
}

.attachments-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 0.5rem;
}

.attachment-item {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0.5rem 0.65rem;
  cursor: pointer;
  transition: all 0.15s;
}

.attachment-item:hover {
  background: var(--bg-hover);
  border-color: var(--border-hover);
}

.att-icon {
  font-size: 1.05rem;
}

.att-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.att-name {
  font-size: 0.76rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.att-size {
  font-size: 0.66rem;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.att-zoom-btn {
  background: none;
  border: none;
  font-size: 0.8rem;
  opacity: 0.6;
  cursor: pointer;
}

.comments-feed {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.comment-bubble {
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0.75rem 0.85rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.comment-bubble.is-private {
  border-left: 3px solid var(--tag-yellow-text);
  background: var(--tag-yellow-bg);
}

.comment-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.comment-author {
  font-size: 0.76rem;
  font-weight: 500;
  color: var(--text-primary);
}

.comment-date {
  font-size: 0.68rem;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.comment-text {
  font-size: 0.8rem;
  color: var(--text-secondary);
  white-space: pre-wrap;
  line-height: 1.45;
}

.empty-comments {
  font-size: 0.78rem;
  color: var(--text-muted);
  font-style: italic;
}

.action-section {
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 1rem;
}

.template-selector-box {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.drawer-textarea {
  resize: vertical;
  min-height: 85px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  color: var(--text-primary);
  border-radius: 6px;
  padding: 0.55rem;
}

.drawer-textarea:focus {
  outline: none;
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 2px rgba(35, 131, 226, 0.2);
}

.drawer-action-bar {
  margin-top: 0.85rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.85rem;
  flex-wrap: wrap;
}

.status-summary {
  font-size: 0.76rem;
  color: var(--text-secondary);
}

.status-summary strong {
  color: var(--text-primary);
}

.drawer-kbd-hint {
  font-size: 0.68rem;
  color: var(--text-muted);
  margin-top: 0.25rem;
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.drawer-kbd-hint kbd {
  font-family: var(--font-mono);
  font-size: 0.65rem;
  background: var(--tag-default-bg);
  border: 1px solid var(--border-subtle);
  border-radius: 3px;
  padding: 0.05rem 0.35rem;
  color: var(--text-secondary);
}
</style>
