<template>
  <div class="app-layout">
    <!-- Сайдбар -->
    <AppSidebar />

    <div class="main">
      <!-- Топбар -->
      <AppTopbar 
        @refresh="triggerRefresh" 
        @open-palette="openCommandPalette" 
      />

      <!-- Рабочая область -->
      <main class="content">
        <slot></slot>
      </main>
    </div>

    <!-- Глобальные модальные элементы -->
    <ToastContainer />
    <CommandPalette ref="paletteRef" />
    <TaskDrawer />
  </div>
</template>

<script setup>
import { ref, provide } from 'vue';
import AppSidebar from './AppSidebar.vue';
import AppTopbar from './AppTopbar.vue';
import ToastContainer from './ToastContainer.vue';
import CommandPalette from './CommandPalette.vue';
import TaskDrawer from './TaskDrawer.vue';

const paletteRef = ref(null);
const refreshCallbacks = new Set();

const registerRefreshCallback = (cb) => {
  refreshCallbacks.add(cb);
  return () => refreshCallbacks.delete(cb);
};

provide('registerRefresh', registerRefreshCallback);

const triggerRefresh = () => {
  refreshCallbacks.forEach(cb => {
    try {
      cb();
    } catch (err) {
      console.error('Ошибка при вызове колбэка обновления:', err);
    }
  });
};

const openCommandPalette = () => {
  paletteRef.value?.open();
};
</script>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
  width: 100%;
  background: var(--bg);
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.content {
  flex: 1;
  padding: 1.5rem 1.75rem;
  max-width: 1600px;
  width: 100%;
  margin: 0 auto;
}
</style>
