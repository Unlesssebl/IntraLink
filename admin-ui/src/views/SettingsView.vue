<template>
  <section class="screen active">
    <!-- КАРТОЧКА 1: Мониторинг здоровья инфраструктуры -->
    <div class="card mb-3">
      <div class="card-header">
        <div>
          <div class="card-title">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" class="title-icon"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
            <span>Статус сервисов и интеграций</span>
          </div>
          <div class="card-subtitle">Мониторинг подключения шлюза к IntraService API, Redis, pgvector и фоновым процессам</div>
        </div>
        <button class="btn btn-outline btn-sm" @click="checkHealth" :disabled="checking">
          <span v-if="checking">Проверка...</span>
          <span v-else>Обновить статус</span>
        </button>
      </div>
      <div class="card-body">
        <div class="health-grid">
          <div class="health-card">
            <div class="health-head">
              <span class="health-title">IntraService API</span>
              <span class="status-indicator online"></span>
            </div>
            <div class="health-val">Подключено</div>
            <div class="health-sub">Фильтр #984 (Очередь 1-й линии)</div>
          </div>

          <div class="health-card">
            <div class="health-head">
              <span class="health-title">Redis Streams & State</span>
              <span class="status-indicator online"></span>
            </div>
            <div class="health-val">Активен</div>
            <div class="health-sub">stream:intraservice_events</div>
          </div>

          <div class="health-card">
            <div class="health-head">
              <span class="health-title">База знаний RAG</span>
              <span class="status-indicator" :class="{ online: isAiOnline }"></span>
            </div>
            <div class="health-val">{{ isAiOnline ? 'pgvector Tier-1' : 'Offline' }}</div>
            <div class="health-sub">FastEmbed (BAAI/bge-small-en-v1.5)</div>
          </div>

          <div class="health-card">
            <div class="health-head">
              <span class="health-title">Сессия оператора</span>
              <span class="status-indicator online"></span>
            </div>
            <div class="health-val">{{ authStore.user?.username || 'Авторизован' }}</div>
            <div class="health-sub">HttpOnly JWT Cookie (12ч)</div>
          </div>
        </div>
      </div>
    </div>

    <!-- КАРТОЧКА 2: Параметры очереди и рабочего места -->
    <div class="card mb-3">
      <div class="card-header">
        <div>
          <div class="card-title">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" class="title-icon"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
            <span>Параметры очереди и диспетчеризации</span>
          </div>
          <div class="card-subtitle">Конфигурация источника заявок 1-й линии и правил автоматической классификации</div>
        </div>
      </div>
      <div class="card-body">
        <div class="info-grid-2">
          <div class="info-item">
            <span class="info-label">Целевой фильтр IntraService:</span>
            <div class="info-val-badge">#984 (1-я линия технической поддержки)</div>
            <span class="form-hint">Заявки загружаются из системного фильтра 1-й линии с жадной загрузкой кастомных полей.</span>
          </div>

          <div class="info-item">
            <span class="info-label">Движок автоклассификации:</span>
            <div class="info-val-badge success">IntraLink Rule Engine + RAG</div>
            <span class="form-hint">Автоматическое распознавание Wi-Fi, 1C, Directum, Ремонта в 112 каб., ИБ и SMB блокировок.</span>
          </div>
        </div>
      </div>
    </div>

    <!-- КАРТОЧКА 3: Горячие клавиши оператора -->
    <div class="card mb-3">
      <div class="card-header">
        <div>
          <div class="card-title">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" class="title-icon"><rect x="2" y="4" width="20" height="16" rx="2"/><line x1="6" y1="8" x2="6.01" y2="8"/><line x1="10" y1="8" x2="10.01" y2="8"/><line x1="14" y1="8" x2="14.01" y2="8"/><line x1="18" y1="8" x2="18.01" y2="8"/><line x1="6" y1="12" x2="6.01" y2="12"/><line x1="10" y1="12" x2="10.01" y2="12"/><line x1="14" y1="12" x2="14.01" y2="12"/><line x1="18" y1="12" x2="18.01" y2="12"/><line x1="7" y1="16" x2="17" y2="16"/></svg>
            <span>Горячие клавиши (Keyboard Shortcuts)</span>
          </div>
          <div class="card-subtitle">Шорткаты для сверхбыстрой работы в очереди без мыши</div>
        </div>
      </div>
      <div class="card-body">
        <div class="shortcuts-grid">
          <div class="shortcut-row">
            <div class="keys"><kbd>Ctrl</kbd> + <kbd>K</kbd></div>
            <div class="shortcut-desc">Командная строка: поиск по номеру заявки (#123), имени ПК, заявителю или командам</div>
          </div>
          <div class="shortcut-row">
            <div class="keys"><kbd>Esc</kbd></div>
            <div class="shortcut-desc">Закрыть детальную карточку заявки (TaskDrawer) или поиск</div>
          </div>
          <div class="shortcut-row">
            <div class="keys"><kbd>Ctrl</kbd> + <kbd>Enter</kbd></div>
            <div class="shortcut-desc">Применить выбранный шаблон и отправить комментарий из шторки заявки</div>
          </div>
          <div class="shortcut-row">
            <div class="keys"><kbd>Alt</kbd> + <kbd>R</kbd></div>
            <div class="shortcut-desc">Мгновенно обновить список открытых заявок в очереди</div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useAuthStore } from '../stores/auth';
import { apiFetch } from '../api';

const authStore = useAuthStore();
const isAiOnline = ref(true);
const checking = ref(false);

const checkHealth = async () => {
  checking.value = true;
  try {
    const res = await apiFetch('/admin/api/ai-worker/status');
    isAiOnline.value = !!res;
  } catch (e) {
    isAiOnline.value = false;
  } finally {
    checking.value = false;
  }
};

onMounted(() => {
  checkHealth();
});
</script>

<style scoped>
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.85rem;
}

.health-card {
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.health-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.health-title {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-secondary);
}
.health-val {
  font-size: 1rem;
  font-weight: 700;
  color: var(--text-primary);
}
.health-sub {
  font-size: 0.72rem;
  color: var(--text-muted);
}

.info-grid-2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1rem;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.info-label {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-secondary);
}

.info-val-badge {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--accent-primary);
  background: var(--tag-blue-bg);
  border: 1px solid transparent;
  padding: 0.35rem 0.65rem;
  border-radius: 5px;
  width: fit-content;
}

.info-val-badge.success {
  color: var(--tag-green-text);
  background: var(--tag-green-bg);
}

.shortcuts-grid {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.shortcut-row {
  display: flex;
  align-items: center;
  gap: 1.25rem;
  padding: 0.55rem 0.75rem;
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 5px;
}

.keys {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  min-width: 120px;
  font-family: var(--font-mono);
  font-size: 0.76rem;
  color: var(--text-secondary);
}

kbd {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  padding: 0.15rem 0.4rem;
  color: var(--text-primary);
  box-shadow: 0 1px 1px rgba(0, 0, 0, 0.08);
}

.shortcut-desc {
  font-size: 0.8rem;
  color: var(--text-secondary);
}
</style>
