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
              >
                #{{ queueStore.activeDrawerTaskId }} ↗
              </a>
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
                  <span class="meta-title">👤 Заявитель</span>
                  <strong class="meta-val">{{ details.creator }}</strong>
                  <span v-if="details.department" class="meta-sub">{{ details.department }}</span>
                </div>
                <div class="meta-card">
                  <span class="meta-title">📞 Контакты & Кабинет</span>
                  <strong class="meta-val">{{ details.phone ? details.phone : 'Телефон не указан' }}</strong>
                  <span v-if="details.room" class="meta-sub">Кабинет: {{ details.room }}</span>
                </div>
                <div class="meta-card host-card">
                  <span class="meta-title">💻 Рабочая станция</span>
                  <strong class="meta-val">{{ details.pc_name || 'Не указан' }}</strong>
                  <!-- Статус диагностики хоста -->
                  <div v-if="details.pc_name" class="host-diag-status">
                    <span v-if="hostDiag?.loading" class="diag-pill loading">Проверка сети...</span>
                    <span v-else-if="hostDiag?.is_online" class="diag-pill online">
                      🟢 {{ hostDiag.avg_rtt }} | SMB: {{ hostDiag.smb_ok ? 'OK' : '—' }} | WinRM: {{ hostDiag.winrm_ok ? 'OK' : '—' }}
                    </span>
                    <span v-else class="diag-pill offline">🔴 Офлайн / Недоступен</span>
                  </div>
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
                  @click="openImageLightbox(att)"
                >
                  <div class="att-icon">🖼️</div>
                  <div class="att-info">
                    <span class="att-name" :title="att.name">{{ att.name }}</span>
                    <span class="att-size">{{ formatFileSize(att.size) }}</span>
                  </div>
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
                <label>Корпоративный шаблон:</label>
                <select 
                  v-model="selectedTmplKey" 
                  class="select-template"
                  @change="handleTemplateChange"
                >
                  <option v-for="t in queueStore.templates" :key="t.key" :value="t.key">
                    {{ t.name }}
                  </option>
                </select>
              </div>

              <!-- Текст комментария -->
              <textarea 
                v-model="editComment" 
                rows="4" 
                class="drawer-textarea"
                placeholder="Текст ответа заявителю..."
              ></textarea>

              <div class="drawer-action-bar">
                <div class="status-summary">
                  Целевой статус: <strong>{{ targetStatusName }}</strong>
                </div>
                <button 
                  class="btn btn-primary" 
                  :disabled="queueStore.submittingIds.has(details.id)"
                  @click="applyFromDrawer"
                >
                  <span v-if="queueStore.submittingIds.has(details.id)">Применение...</span>
                  <span v-else>⚡ Применить и закрыть шторку</span>
                </button>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- Модальное окно просмотра скриншота (Lightbox) -->
    <div v-if="activeImage" class="lightbox-overlay" @click="activeImage = null">
      <div class="lightbox-modal" @click.stop>
        <div class="lightbox-header">
          <span>{{ activeImage.name }}</span>
          <button class="btn btn-outline btn-sm" @click="activeImage = null">✕ Закрыть</button>
        </div>
        <div class="lightbox-body">
          <img :src="activeImage.url" :alt="activeImage.name" class="lightbox-img" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useQueueStore } from '../stores/queue';

const queueStore = useQueueStore();
const details = computed(() => queueStore.drawerTaskDetails);

const selectedTmplKey = ref('general');
const editComment = ref('');
const targetStatusId = ref(27);
const targetStatusName = ref('В работе (27)');
const expensesMinutes = ref(10);
const activeImage = ref(null);

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
  };
  await queueStore.applySingleAction(taskObj);
  queueStore.closeTaskDrawer();
};

const openImageLightbox = (att) => {
  activeImage.value = att;
};

const formatFileSize = (bytes) => {
  if (!bytes) return '';
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
};

const formatDate = (dateStr) => {
  if (!dateStr) return '';
  return dateStr.replace('T', ' ').substring(0, 16);
};
</script>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(4px);
  z-index: 1000;
  display: flex;
  justify-content: flex-end;
  animation: fadeIn 0.2s ease-out;
}

.drawer-panel {
  width: 580px;
  max-width: 90vw;
  height: 100vh;
  background: var(--surface);
  border-left: 1px solid var(--border);
  box-shadow: -10px 0 30px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  animation: slideInRight 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

.drawer-header {
  padding: 1.25rem 1.5rem;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  background: var(--surface-2);
}

.drawer-title-wrap {
  flex: 1;
  min-width: 0;
}

.drawer-id-badge {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.35rem;
}

.external-task-link {
  font-family: monospace;
  font-weight: 700;
  color: var(--primary);
  font-size: 0.95rem;
  text-decoration: none;
}
.external-task-link:hover {
  text-decoration: underline;
}

.drawer-task-name {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text);
  line-height: 1.35;
}

.drawer-close-btn {
  background: none;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  padding: 0.3rem;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.15s, background 0.15s;
}
.drawer-close-btn:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.08);
}

.drawer-content {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.drawer-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 4rem 1rem;
  color: var(--text-2);
  gap: 1rem;
}

.drawer-section {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.section-label {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-3);
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 0.75rem;
}

.meta-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.75rem 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.meta-title {
  font-size: 0.72rem;
  color: var(--text-3);
}

.meta-val {
  font-size: 0.88rem;
  color: var(--text);
}

.meta-sub {
  font-size: 0.75rem;
  color: var(--text-2);
}

.host-diag-status {
  margin-top: 0.35rem;
}

.diag-pill {
  font-size: 0.72rem;
  padding: 0.2rem 0.45rem;
  border-radius: 4px;
  font-weight: 500;
}
.diag-pill.online {
  background: var(--green-bg);
  color: var(--green);
}
.diag-pill.offline {
  background: var(--red-bg);
  color: var(--red);
}
.diag-pill.loading {
  background: var(--yellow-bg);
  color: var(--yellow);
}

.description-box {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 1rem;
  font-size: 0.88rem;
  line-height: 1.5;
  color: var(--text);
  white-space: pre-wrap;
}

.attachments-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 0.75rem;
}

.attachment-item {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.6rem 0.8rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.attachment-item:hover {
  border-color: var(--primary);
  background: rgba(79, 70, 229, 0.06);
}

.att-icon {
  font-size: 1.25rem;
}
.att-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.att-name {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.att-size {
  font-size: 0.7rem;
  color: var(--text-3);
}

.comments-feed {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.comment-bubble {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.75rem 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.comment-bubble.is-private {
  border-left: 3px solid var(--yellow);
}

.comment-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.comment-author {
  font-size: 0.82rem;
  color: var(--text);
}
.comment-date {
  font-size: 0.72rem;
  color: var(--text-3);
}
.comment-text {
  font-size: 0.84rem;
  color: var(--text-2);
  line-height: 1.4;
  white-space: pre-wrap;
}
.empty-comments {
  font-size: 0.82rem;
  color: var(--text-3);
  padding: 0.5rem 0;
}

.action-section {
  background: rgba(79, 70, 229, 0.03);
  border: 1px solid rgba(79, 70, 229, 0.2);
  border-radius: var(--radius);
  padding: 1.2rem;
}

.template-selector-box {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.template-selector-box label {
  font-size: 0.8rem;
  color: var(--text-2);
  font-weight: 500;
}
.select-template {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-sm);
  font-size: 0.85rem;
  outline: none;
}
.select-template:focus {
  border-color: var(--primary);
}

.drawer-textarea {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 0.75rem;
  border-radius: var(--radius-sm);
  font-size: 0.85rem;
  line-height: 1.4;
  outline: none;
  font-family: inherit;
  resize: vertical;
}
.drawer-textarea:focus {
  border-color: var(--primary);
}

.drawer-action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 0.5rem;
}
.status-summary {
  font-size: 0.82rem;
  color: var(--text-2);
}

/* Lightbox Modal */
.lightbox-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.85);
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}
.lightbox-modal {
  max-width: 90vw;
  max-height: 90vh;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.lightbox-header {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.88rem;
  color: var(--text);
}
.lightbox-body {
  padding: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
}
.lightbox-img {
  max-width: 100%;
  max-height: 75vh;
  object-fit: contain;
  border-radius: 4px;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes slideInRight {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}
</style>
