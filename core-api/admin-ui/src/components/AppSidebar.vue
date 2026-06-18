<template>
  <nav class="sidebar">
    <div class="sidebar-header">
      <div class="logo">
        <div class="logo-icon">
          <svg viewBox="0 0 24 24">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </div>
        Intra<span>Link</span>
      </div>
    </div>

    <div class="sidebar-nav">
      <div class="nav-section-label">Мониторинг</div>
      <RouterLink to="/history" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path d="M12 8v4l3 3m6-3a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" />
        </svg>
        Журнал операций
      </RouterLink>

      <div class="nav-section-label">Управление</div>
      <RouterLink to="/install" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
          <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
        </svg>
        Установка принтера
      </RouterLink>
      <RouterLink to="/kb" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5V4.5z" />
        </svg>
        Поддерживаемые модели
      </RouterLink>
      <RouterLink to="/ai-worker" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
          <polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline>
          <line x1="12" y1="22.08" x2="12" y2="12"></line>
        </svg>
        AI Воркер
      </RouterLink>
      <RouterLink to="/settings" class="nav-item" active-class="router-link-active">
        <svg viewBox="0 0 24 24">
          <path stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path>
          <circle cx="12" cy="12" r="3"></circle>
        </svg>
        Настройки
      </RouterLink>
    </div>

    <!-- Воркер-статусы внизу сайдбара -->
    <div class="sidebar-statuses">
      <div class="worker-status">
        <div class="status-info">
          <span class="status-label">Printer Worker</span>
          <span class="status-value">{{ printerStatusText }}</span>
        </div>
        <div class="status-dot" :class="{ online: isPrinterOnline }"></div>
      </div>

      <div class="worker-status" style="border-top: none; padding-top: 0;">
        <div class="status-info">
          <span class="status-label">AI Worker</span>
          <span class="status-value">{{ aiStatusText }}</span>
        </div>
        <div class="status-dot" :class="{ online: isAiOnline }"></div>
      </div>
    </div>
  </nav>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { useAuthStore } from '../stores/auth';
import { usePolling } from '../composables/usePolling';

const authStore = computed(() => useAuthStore());

const isPrinterOnline = ref(false);
const printerStatusText = ref('Проверка...');

const isAiOnline = ref(false);
const aiStatusText = ref('Проверка...');

// Периодический опрос статусов
const fetchStatuses = async () => {
  if (!authStore.value.isLoggedIn) return;

  // 1. Printer Worker Status
  try {
    const res = await fetch('/admin/api/worker-status');
    if (res.ok) {
      const data = await res.json();
      isPrinterOnline.value = data.status === 'online';
      printerStatusText.value = isPrinterOnline.value ? 'Online' : 'Offline';
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
      aiStatusText.value = 'Online';
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
.sidebar-statuses {
  display: flex;
  flex-direction: column;
}
</style>
