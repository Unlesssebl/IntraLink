import { createRouter, createWebHashHistory } from 'vue-router';
import HistoryView from './views/HistoryView.vue';
import InstallView from './views/InstallView.vue';
import KnowledgeBaseView from './views/KnowledgeBaseView.vue';
import AIWorkerView from './views/AIWorkerView.vue';
import SettingsView from './views/SettingsView.vue';

const routes = [
    {
        path: '/',
        redirect: '/history'
    },
    {
        path: '/history',
        name: 'history',
        component: HistoryView,
        meta: { title: 'Журнал операций' }
    },
    {
        path: '/install',
        name: 'install',
        component: InstallView,
        meta: { title: 'Установка принтера' }
    },
    {
        path: '/kb',
        name: 'kb',
        component: KnowledgeBaseView,
        meta: { title: 'Поддерживаемые модели' }
    },
    {
        path: '/ai-worker',
        name: 'ai-worker',
        component: AIWorkerView,
        meta: { title: 'AI Воркер' }
    },
    {
        path: '/settings',
        name: 'settings',
        component: SettingsView,
        meta: { title: 'Настройки' }
    },
    {
        path: '/:pathMatch(.*)*',
        redirect: '/history'
    }
];

export const router = createRouter({
    history: createWebHashHistory(),
    routes
});
