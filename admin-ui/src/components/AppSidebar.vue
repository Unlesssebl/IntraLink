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
      <div class="workspace-badge">Helpdesk v2.0</div>
    </div>

    <!-- Основная навигация -->
    <div class="sidebar-nav">
      <div class="nav-section-label">Операции</div>

      <RouterLink to="/queue" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path d="M4 6h16M4 12h16M4 18h7" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/>
        </svg>
        <span class="nav-label">Очередь 1-й линии</span>
        <span v-if="queueCount > 0" class="nav-badge">{{ queueCount }}</span>
      </RouterLink>

      <RouterLink to="/history" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" fill="none"/>
          <polyline points="12 7 12 12 15 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/>
        </svg>
        <span class="nav-label">Журнал операций</span>
      </RouterLink>

      <div class="nav-section-label">Интеллект & Данные</div>

      <RouterLink to="/ai-worker" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" stroke="currentColor" stroke-width="2" fill="none"></path>
          <polyline points="3.27 6.96 12 12.01 20.73 6.96" stroke="currentColor" stroke-width="2" fill="none"></polyline>
          <line x1="12" y1="22.08" x2="12" y2="12" stroke="currentColor" stroke-width="2"></line>
        </svg>
        <span class="nav-label">AI & База знаний</span>
      </RouterLink>

      <div class="nav-section-label">Система</div>

      <RouterLink to="/settings" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none" d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path>
          <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2" fill="none"></circle>
        </svg>
        <span class="nav-label">Настройки & Инфра</span>
      </RouterLink>
    </div>

    <!-- Статусы воркеров внизу сайдбара -->
    <div class="sidebar-statuses">
      <div class="worker-status-card">
        <div class="status-indicator" :class="{ online: isPrinterOnline }"></div>
        <div class="status-info">
          <span class="status-title">Printer Orchestrator</span>
          <span class="status-sub">{{ printerStatusText }}</span>
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

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { useAuthStore } from '../stores/auth';
import { useQueueStore } from '../stores/queue';
import { usePolling } from '../composables/usePolling';

const authStore = computed(() => useAuthStore());
const queueStore = useQueueStore();

const queueCount = computed(() => queueStore.tasks.length);

const isPrinterOnline = ref(false);
const printerStatusText = ref('Проверка...');

const isAiOnline = ref(false);
const aiStatusText = ref('Проверка...');

const fetchStatuses = async () => {
  if (!authStore.value.isLoggedIn) return;

  // 1. Printer Worker Status
  try {
    const res = await fetch('/admin/api/worker-status');
    if (res.ok) {
      const data = await res.json();
      isPrinterOnline.value = data.status === 'online';
      printerStatusText.value = isPrinterOnline.value ? 'Online (WinRM/SMB)' : 'Offline';
    } else {
      isPrinterOnline.value = false;
      printerStatusText.value = 'Offline';
    }
  } catch (e) {
    isPrinterOnline.value = false;
    printerStatusText.value = 'Offline';
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

let polling = null;

onMounted(() => {
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
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}

.sidebar-header {
  padding: 1.5rem 1.25rem 1rem;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.logo {
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: -0.03em;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.logo-icon {
  width: 30px;
  height: 30px;
  background: var(--primary);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.logo-icon svg {
  width: 16px;
  height: 16px;
  stroke: white;
}

.logo span {
  color: var(--primary);
}

.workspace-badge {
  font-size: 0.65rem;
  font-weight: 600;
  color: var(--text-3);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
}

.sidebar-nav {
  padding: 1rem 0.75rem;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.nav-section-label {
  font-size: 0.65rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-3);
  padding: 0.6rem 0.75rem 0.25rem;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  color: var(--text-2);
  padding: 0.6rem 0.75rem;
  border-radius: var(--radius-sm);
  font-size: 0.88rem;
  font-weight: 500;
  text-decoration: none;
  transition: all 0.15s ease;
}

.nav-item svg {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.nav-item:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.04);
}

.nav-item.router-link-active {
  color: #fff;
  background: var(--primary);
  font-weight: 600;
}

.nav-label {
  flex: 1;
}

.nav-badge {
  font-size: 0.72rem;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
  padding: 0.15rem 0.5rem;
  border-radius: 10px;
}
.nav-item:not(.router-link-active) .nav-badge {
  background: rgba(79, 70, 229, 0.2);
  color: var(--primary);
}

.sidebar-statuses {
  padding: 1rem;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  background: var(--surface-2);
}

.worker-status-card {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--radius-sm);
}

.status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--red);
  box-shadow: 0 0 8px rgba(244, 63, 94, 0.4);
}
.status-indicator.online {
  background: var(--green);
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.4);
}

.status-info {
  display: flex;
  flex-direction: column;
}
.status-title {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text);
}
.status-sub {
  font-size: 0.68rem;
  color: var(--text-3);
}
</style>
