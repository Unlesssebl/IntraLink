import { defineStore } from 'pinia';
import { apiFetch } from '../api';

export const useCacheStore = defineStore('cache', {
    state: () => ({
        kbData: null,
        servicesTree: null,
        servicesTreeLoading: false,
        kbLoading: false,
    }),
    actions: {
        async fetchKnowledgeBase(force = false) {
            if (this.kbData && !force) return this.kbData;
            
            // Пробуем восстановить из sessionStorage
            if (!force) {
                const cached = sessionStorage.getItem('intralink_kb_cache');
                if (cached) {
                    try {
                        this.kbData = JSON.parse(cached);
                        return this.kbData;
                    } catch {}
                }
            }

            this.kbLoading = true;
            try {
                const data = await apiFetch('/admin/api/knowledge-base');
                this.kbData = data;
                sessionStorage.setItem('intralink_kb_cache', JSON.stringify(data));
                return data;
            } catch (error) {
                console.error('Ошибка загрузки базы знаний:', error);
                throw error;
            } finally {
                this.kbLoading = false;
            }
        },

        async fetchServicesTree(force = false) {
            if (this.servicesTree && !force) return this.servicesTree;

            // Пробуем из sessionStorage
            if (!force) {
                const cached = sessionStorage.getItem('intralink_services_cache');
                if (cached) {
                    try {
                        this.servicesTree = JSON.parse(cached);
                        return this.servicesTree;
                    } catch {}
                }
            }

            this.servicesTreeLoading = true;
            try {
                const data = await apiFetch('/admin/api/services-tree');
                this.servicesTree = data;
                sessionStorage.setItem('intralink_services_cache', JSON.stringify(data));
                return data;
            } catch (error) {
                console.error('Ошибка загрузки дерева услуг:', error);
                throw error;
            } finally {
                this.servicesTreeLoading = false;
            }
        },

        invalidate() {
            this.kbData = null;
            this.servicesTree = null;
            sessionStorage.removeItem('intralink_kb_cache');
            sessionStorage.removeItem('intralink_services_cache');
        }
    }
});
