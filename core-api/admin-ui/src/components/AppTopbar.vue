<template>
  <div class="topbar">
    <span class="topbar-title">{{ pageTitle }}</span>
    <div class="topbar-right">
      <button class="btn-ghost" @click="handleRefresh">Обновить</button>
      <button class="btn-ghost" @click="handleLogout">Выйти</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useAuthStore } from '../stores/auth';

const emit = defineEmits(['refresh']);

const route = useRoute();
const authStore = useAuthStore();

const pageTitle = computed(() => {
  return route.meta?.title || 'Панель администратора';
});

const handleRefresh = () => {
  emit('refresh');
};

const handleLogout = async () => {
  if (confirm('Вы действительно хотите выйти?')) {
    await authStore.logout();
  }
};
</script>
