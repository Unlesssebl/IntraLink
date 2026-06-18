<template>
  <div v-if="authStore.checkingSession" class="initial-loading">
    <div class="spinner" style="width: 30px; height: 30px; border-width: 3px;"></div>
    <div style="margin-top: 1rem; color: var(--text-2); font-size: 0.9rem;">Инициализация панели...</div>
  </div>
  <template v-else>
    <!-- Если пользователь не авторизован, LoginModal перекроет весь экран -->
    <LoginModal />
    
    <AppLayout v-if="authStore.isLoggedIn">
      <RouterView />
    </AppLayout>
  </template>
</template>

<script setup>
import { onMounted } from 'vue';
import { useAuthStore } from './stores/auth';
import LoginModal from './components/LoginModal.vue';
import AppLayout from './components/AppLayout.vue';

const authStore = useAuthStore();

onMounted(async () => {
  await authStore.checkSession();
});
</script>

<style>
.initial-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  width: 100vw;
  background: #07080c;
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
}
</style>
