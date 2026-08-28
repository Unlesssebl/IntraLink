import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router';
import QueueDashboardView from './views/QueueDashboardView.vue';
import AIWorkerView from './views/AIWorkerView.vue';
import SettingsView from './views/SettingsView.vue';

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/queue',
  },
  {
    path: '/queue',
    name: 'queue',
    component: QueueDashboardView,
    meta: { title: 'Очередь 1-й линии' },
  },
  {
    path: '/ai-worker',
    name: 'ai-worker',
    component: AIWorkerView,
    meta: { title: 'База знаний & AI' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: SettingsView,
    meta: { title: 'Настройки & Инфра' },
  },
  {
    path: '/printers',
    redirect: '/queue',
  },
  {
    path: '/history',
    redirect: '/queue',
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/queue',
  },
];

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
});
