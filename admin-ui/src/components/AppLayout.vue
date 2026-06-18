<template>
  <div class="app-layout">
    <AppSidebar />
    <div class="main">
      <AppTopbar @refresh="triggerRefresh" />
      <div class="content">
        <slot></slot>
      </div>
    </div>
  </div>
</template>

<script setup>
import { provide } from 'vue';
import AppSidebar from './AppSidebar.vue';
import AppTopbar from './AppTopbar.vue';

const refreshCallbacks = new Set();

const registerRefreshCallback = (cb) => {
  refreshCallbacks.add(cb);
  return () => refreshCallbacks.delete(cb); // возвращаем функцию отписки
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
</script>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
  width: 100%;
}
</style>
