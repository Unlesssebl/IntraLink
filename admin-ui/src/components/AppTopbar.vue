<template>
  <div class="topbar">
    <div class="topbar-left">
      <h1 class="topbar-title">{{ pageTitle }}</h1>
    </div>

    <div class="topbar-center">
      <button class="palette-trigger-btn" @click="$emit('open-palette')">
        <svg viewBox="0 0 24 24" width="16" height="16">
          <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" fill="none"/>
          <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <span>Поиск или команда...</span>
        <kbd class="kbd-badge">Ctrl K</kbd>
      </button>
    </div>

    <div class="topbar-right">
      <!-- Звуковой сигнал о новых заявках -->
      <button 
        class="icon-toggle-btn" 
        :class="{ active: soundEnabled }" 
        :title="soundEnabled ? 'Звук уведомлений включен' : 'Звук уведомлений выключен'"
        @click="toggleSound"
      >
        <span v-if="soundEnabled">🔔</span>
        <span v-else>🔕</span>
      </button>

      <!-- Кнопка обновления данных -->
      <button class="btn btn-outline btn-sm topbar-refresh-btn" :disabled="isRefreshing" @click="handleRefresh">
        <svg v-if="isRefreshing" class="spin" viewBox="0 0 24 24" width="14" height="14">
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

      <!-- Пользователь и Выход -->
      <div class="user-profile-menu">
        <div class="user-avatar">
          {{ userInitials }}
        </div>
        <span class="user-name">{{ userName }}</span>
        <button class="btn-ghost btn-sm" title="Выйти из сессии" @click="handleLogout">
          <svg viewBox="0 0 24 24" width="16" height="16">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" stroke="currentColor" stroke-width="2" fill="none"/>
            <polyline points="16 17 21 12 16 7" stroke="currentColor" stroke-width="2" fill="none"/>
            <line x1="21" y1="12" x2="9" y2="12" stroke="currentColor" stroke-width="2"/>
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useRoute } from 'vue-router';
import { useAuthStore } from '../stores/auth';
import { useToastStore } from '../stores/toast';

const emit = defineEmits(['refresh', 'open-palette']);

const route = useRoute();
const authStore = useAuthStore();
const toastStore = useToastStore();

const isRefreshing = ref(false);
const soundEnabled = ref(localStorage.getItem('intralink_sound_enabled') === 'true');

const pageTitle = computed(() => {
  return route.meta?.title || 'Панель администратора';
});

const userName = computed(() => {
  return authStore.user?.username || 'Оператор';
});

const userInitials = computed(() => {
  const name = userName.value;
  return name.substring(0, 2).toUpperCase();
});

const toggleSound = () => {
  soundEnabled.value = !soundEnabled.value;
  localStorage.setItem('intralink_sound_enabled', soundEnabled.value.toString());
  if (soundEnabled.value) {
    toastStore.info('Звуковые уведомления включены');
  } else {
    toastStore.info('Звуковые уведомления отключены');
  }
};

const handleRefresh = async () => {
  isRefreshing.value = true;
  emit('refresh');
  setTimeout(() => {
    isRefreshing.value = false;
  }, 600);
};

const handleLogout = async () => {
  if (confirm('Вы действительно хотите выйти из панели администратора?')) {
    await authStore.logout();
  }
};
</script>

<style scoped>
.topbar {
  height: 64px;
  padding: 0 1.75rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.5rem;
  position: sticky;
  top: 0;
  z-index: 100;
}

.topbar-left {
  display: flex;
  align-items: center;
}

.topbar-title {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.02em;
}

.topbar-center {
  flex: 1;
  max-width: 420px;
}

.palette-trigger-btn {
  width: 100%;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-3);
  padding: 0.45rem 0.85rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.15s ease;
}
.palette-trigger-btn:hover {
  border-color: var(--border-hover);
  color: var(--text-2);
  background: rgba(255, 255, 255, 0.03);
}
.palette-trigger-btn span {
  flex: 1;
  text-align: left;
}

.kbd-badge {
  font-size: 0.7rem;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.15rem 0.4rem;
  color: var(--text-3);
  font-family: inherit;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 0.85rem;
}

.icon-toggle-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.4rem 0.6rem;
  font-size: 0.95rem;
  cursor: pointer;
  transition: all 0.15s;
}
.icon-toggle-btn:hover {
  border-color: var(--border-hover);
  background: rgba(255, 255, 255, 0.04);
}
.icon-toggle-btn.active {
  background: rgba(79, 70, 229, 0.15);
  border-color: rgba(79, 70, 229, 0.4);
}

.user-profile-menu {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding-left: 0.75rem;
  border-left: 1px solid var(--border);
}

.user-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  font-weight: 700;
  font-size: 0.75rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.user-name {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--text);
}
</style>
