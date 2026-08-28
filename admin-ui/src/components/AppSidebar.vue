<template>
  <nav class="sidebar">
    <!-- Логотип и брендинг -->
    <div class="sidebar-header">
      <div class="logo">
        <div class="logo-icon">
          <svg viewBox="0 0 24 24">
            <path d="M5 12h14M12 5l7 7-7 7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="logo-text">Intra<span>Link</span></div>
      </div>
      <div class="workspace-badge">Mission Control v2.5</div>
    </div>

    <!-- Основная навигация -->
    <div class="sidebar-nav">
      <div class="nav-section-label">Операции</div>

      <!-- 1. Очередь заявок -->
      <RouterLink to="/queue" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path d="M4 6h16M4 12h16M4 18h7" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/>
        </svg>
        <span class="nav-label">Очередь 1-й линии</span>
        <span v-if="queueCount > 0" class="nav-badge">{{ queueCount }}</span>
      </RouterLink>

      <div class="nav-section-label">Интеллект & RAG</div>

      <!-- 2. AI & База знаний -->
      <RouterLink to="/ai-worker" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" stroke="currentColor" stroke-width="2" fill="none"></path>
          <polyline points="3.27 6.96 12 12.01 20.73 6.96" stroke="currentColor" stroke-width="2" fill="none"></polyline>
          <line x1="12" y1="22.08" x2="12" y2="12" stroke="currentColor" stroke-width="2"></line>
        </svg>
        <span class="nav-label">База знаний & AI</span>
      </RouterLink>

      <div class="nav-section-label">Система</div>

      <!-- 3. Настройки & Инфра -->
      <RouterLink to="/settings" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none" d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path>
          <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2" fill="none"></circle>
        </svg>
        <span class="nav-label">Настройки & Инфра</span>
      </RouterLink>
    </div>

    <!-- Статусы сервисов внизу сайдбара -->
    <div class="sidebar-statuses">
      <div class="worker-status-card">
        <div class="status-indicator" :class="{ online: isApiOnline }"></div>
        <div class="status-info">
          <span class="status-title">Core Gateway</span>
          <span class="status-sub">{{ apiStatusText }}</span>
        </div>
      </div>

      <div class="worker-status-card">
        <div class="status-indicator" :class="{ online: isAiOnline }"></div>
        <div class="status-info">
          <span class="status-title">AI & RAG Engine</span>
          <span class="status-sub">{{ aiStatusText }}</span>
        </div>
      </div>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { useAuthStore } from '../stores/auth';
import { useQueueStore } from '../stores/queue';
import { usePolling } from '../composables/usePolling';

const authStore = computed(() => useAuthStore());
const queueStore = useQueueStore();

const queueCount = computed(() => queueStore.tasks.length);

const isApiOnline = ref(true);
const apiStatusText = ref('Online (Redis/IS)');

const isAiOnline = ref(false);
const aiStatusText = ref('Проверка...');

const fetchStatuses = async () => {
  if (!authStore.value.isLoggedIn) return;

  // 1. Core API & Redis Status
  try {
    const res = await fetch('/health');
    if (res.ok) {
      isApiOnline.value = true;
      apiStatusText.value = 'Online (Redis/IS)';
    } else {
      isApiOnline.value = false;
      apiStatusText.value = 'Degraded';
    }
  } catch (e) {
    isApiOnline.value = false;
    apiStatusText.value = 'Offline';
  }

  // 2. AI Worker Status
  try {
    const res = await fetch('/admin/api/ai-worker/status');
    if (res.ok) {
      isAiOnline.value = true;
      aiStatusText.value = 'Online (pgvector)';
    } else {
      isAiOnline.value = false;
      aiStatusText.value = 'Offline';
    }
  } catch (e) {
    isAiOnline.value = false;
    aiStatusText.value = 'Offline';
  }
};

let polling: any = null;

onMounted(() => {
  fetchStatuses();
  polling = usePolling(fetchStatuses, 5000);
});

onUnmounted(() => {
  if (polling) {
    polling.stop();
  }
});
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-w);
  flex-shrink: 0;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  padding: 0;
  user-select: none;
}

.sidebar-header {
  padding: 1.25rem 1.25rem 1rem;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.logo {
  font-size: 1.1rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.logo-icon {
  width: 26px;
  height: 26px;
  background: var(--accent-primary);
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: var(--shadow-sm);
}

.logo-icon svg {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: #ffffff;
  stroke-width: 2.2;
}

.logo-text {
  display: flex;
  align-items: center;
}

.logo-text span {
  color: var(--text-secondary);
  font-weight: 400;
}

.workspace-badge {
  font-size: 0.65rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  background: var(--tag-default-bg);
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  width: fit-content;
}

.sidebar-nav {
  padding: 0.85rem 0.65rem;
  flex: 1;
}

.nav-section-label {
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  padding: 0 0.65rem;
  margin: 1.15rem 0 0.35rem;
}

.nav-section-label:first-child {
  margin-top: 0;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  width: 100%;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  padding: 0.48rem 0.65rem;
  border-radius: 4px;
  font-size: 0.84rem;
  font-weight: 500;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: all 0.15s ease;
  position: relative;
  text-decoration: none;
}

.nav-item svg {
  width: 16px;
  height: 16px;
  stroke: currentColor;
  fill: none;
  stroke-width: 1.8;
  flex-shrink: 0;
  opacity: 0.85;
}

.nav-item:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.nav-item.router-link-active {
  color: var(--accent-primary);
  background: var(--bg-selected);
  font-weight: 600;
}

.nav-item.router-link-active svg {
  opacity: 1;
  color: var(--accent-primary);
}

.nav-label {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav-badge {
  font-size: 0.68rem;
  font-weight: 700;
  font-family: var(--font-mono);
  background: var(--tag-blue-bg);
  color: var(--tag-blue-text);
  padding: 0.1rem 0.45rem;
  border-radius: 10px;
  min-width: 18px;
  text-align: center;
}

.sidebar-statuses {
  padding: 0.75rem 0.85rem;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  background: var(--bg-sidebar);
}

.worker-status-card {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.4rem 0.6rem;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 5px;
  box-shadow: var(--shadow-sm);
}

.status-indicator {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #ef4444;
  flex-shrink: 0;
}

.status-indicator.online {
  background: #10b981;
}

.status-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.status-title {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.2;
}

.status-sub {
  font-size: 0.65rem;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.2;
}
</style>
