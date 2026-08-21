import { createRouter, createWebHashHistory } from 'vue-router';
import QueueDashboardView from './views/QueueDashboardView.vue';
import HistoryView from './views/HistoryView.vue';
import AIWorkerView from './views/AIWorkerView.vue';
import SettingsView from './views/SettingsView.vue';

const routes = [
    {
        path: '/',
        redirect: '/queue'
    },
    {
        path: '/queue',
        name: 'queue',
        component: QueueDashboardView,
        meta: { title: 'Очередь 1-й линии' }
    },
    {
        path: '/history',
        name: 'history',
        component: HistoryView,
        meta: { title: 'Журнал операций' }
    },
    {
        path: '/ai-worker',
        name: 'ai-worker',
        component: AIWorkerView,
        meta: { title: 'AI Воркер & База знаний' }
    },
    {
        path: '/settings',
        name: 'settings',
        component: SettingsView,
        meta: { title: 'Настройки' }
    },
    {
        path: '/:pathMatch(.*)*',
        redirect: '/queue'
    }
];

export const router = createRouter({
    history: createWebHashHistory(),
    routes
});

