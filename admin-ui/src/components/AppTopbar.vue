<template>
  <div class="topbar">
    <!-- Левая часть: Заголовок текущего экрана -->
    <div class="topbar-left">
      <h1 class="topbar-title">{{ pageTitle }}</h1>
    </div>

    <!-- Центр: Быстрый поиск и вызов Command Palette -->
    <div class="topbar-center">
      <button class="palette-trigger-btn" @click="$emit('open-palette')">
        <svg viewBox="0 0 24 24" width="16" height="16">
          <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" fill="none"/>
          <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <span>Поиск (#ID, ПК, сервис) или команда...</span>
        <kbd class="kbd-badge">Ctrl K</kbd>
      </button>
    </div>

    <!-- Правая часть: Тема, звук, обновление, профиль -->
    <div class="topbar-right">
      <!-- Переключатель темы: Светлая (Notion) / Темная -->
      <button 
        class="icon-toggle-btn" 
        :title="themeStore.currentTheme === 'light' ? 'Переключить на темную тему' : 'Переключить на светлую тему'"
        @click="themeStore.toggleTheme"
      >
        <svg v-if="themeStore.currentTheme === 'light'" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="5"/>
          <line x1="12" y1="1" x2="12" y2="3"/>
          <line x1="12" y1="21" x2="12" y2="23"/>
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
          <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
          <line x1="1" y1="12" x2="3" y2="12"/>
          <line x1="21" y1="12" x2="23" y2="12"/>
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
          <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
        </svg>
        <svg v-else viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>
      </button>

      <!-- Переключатель звука -->
      <button 
        class="icon-toggle-btn" 
        :class="{ active: soundEnabled }" 
        :title="soundEnabled ? 'Звук уведомлений включен (нажмите для выключения)' : 'Звук уведомлений выключен (нажмите для включения)'"
        @click="toggleSound"
      >
        <svg v-if="soundEnabled" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
          <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
        </svg>
        <svg v-else viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
          <path d="M18.63 13A17.89 17.89 0 0 1 18 8"/>
          <path d="M6.26 6.26A5.86 5.86 0 0 0 6 8c0 7-3 9-3 9h14"/>
          <path d="M18 8a6 6 0 0 0-9.33-5"/>
          <line x1="1" y1="1" x2="23" y2="23"/>
        </svg>
      </button>

      <!-- Кнопка обновления с анимацией -->
      <button 
        class="btn btn-outline btn-sm topbar-refresh-btn" 
        :disabled="isRefreshing" 
        @click="triggerRefresh"
      >
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

      <!-- Меню профиля / выход -->
      <div class="user-profile-menu">
        <div class="user-avatar">{{ userInitials }}</div>
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

<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRoute } from 'vue-router';
import { useAuthStore } from '../stores/auth';
import { useToastStore } from '../stores/toast';
import { useThemeStore } from '../stores/theme';
import { useSound } from '../composables/useSound';

const emit = defineEmits<{
  (e: 'refresh'): void;
  (e: 'open-palette'): void;
}>();

const route = useRoute();
const authStore = useAuthStore();
const toastStore = useToastStore();
const themeStore = useThemeStore();
const sound = useSound();

const isRefreshing = ref(false);
const soundEnabled = ref(localStorage.getItem('intralink_sound_enabled') === 'true');

const pageTitle = computed(() => {
  return (route.meta?.title as string) || 'Панель администратора';
});

const userName = computed(() => {
  return authStore.user?.username || 'Инженер Helpdesk';
});

const userInitials = computed(() => {
  return userName.value.substring(0, 2).toUpperCase();
});

const toggleSound = () => {
  soundEnabled.value = !soundEnabled.value;
  localStorage.setItem('intralink_sound_enabled', soundEnabled.value.toString());
  if (soundEnabled.value) {
    sound.playSuccessSound();
    toastStore.info('Звуковые оповещения включены', 'Звук');
  } else {
    toastStore.info('Звуковые оповещения отключены', 'Звук');
  }
};

const triggerRefresh = async () => {
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
  padding: 1rem 2rem;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.5rem;
  background: var(--surface);
  position: sticky;
  top: 0;
  z-index: 100;
}

.topbar-left {
  min-width: 0;
}

.topbar-title {
  font-size: 1.1rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text);
  white-space: nowrap;
}

.topbar-center {
  flex: 1;
  max-width: 450px;
}

.palette-trigger-btn {
  width: 100%;
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-3);
  padding: 0.5rem 0.85rem;
  font-size: 0.82rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  cursor: pointer;
  transition: all 0.15s;
}

.palette-trigger-btn:hover {
  border-color: var(--border-hover);
  color: var(--text-2);
  background: rgba(255, 255, 255, 0.03);
}

.palette-trigger-btn span {
  flex: 1;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kbd-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.1rem 0.4rem;
  color: var(--text-2);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.icon-toggle-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 0.95rem;
  transition: all 0.15s;
  opacity: 0.7;
}

.icon-toggle-btn.active {
  opacity: 1;
  border-color: rgba(79, 70, 229, 0.4);
  background: rgba(79, 70, 229, 0.1);
}

.icon-toggle-btn:hover {
  opacity: 1;
  border-color: var(--border-hover);
}

.user-profile-menu {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding-left: 0.5rem;
  border-left: 1px solid var(--border);
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--primary);
  color: white;
  font-size: 0.72rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  letter-spacing: -0.02em;
}

.user-name {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text);
}
</style>
