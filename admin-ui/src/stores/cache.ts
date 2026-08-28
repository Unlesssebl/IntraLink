import { defineStore } from 'pinia';
import { apiFetch } from '../api';
import type { PrinterKnowledgeBase } from '../types/printer';
import type { ServiceTreeNode } from '../types/ai';

export const useCacheStore = defineStore('cache', {
  state: () => ({
    kbData: null as PrinterKnowledgeBase | null,
    servicesTree: null as ServiceTreeNode[] | null,
    servicesTreeLoading: false as boolean,
    kbLoading: false as boolean,
  }),
  actions: {
    async fetchKnowledgeBase(force: boolean = false): Promise<PrinterKnowledgeBase | null> {
      if (this.kbData && !force) return this.kbData;

      if (!force) {
        const cached = sessionStorage.getItem('intralink_kb_cache');
        if (cached) {
          try {
            this.kbData = JSON.parse(cached);
            return this.kbData;
          } catch {
            // ignore
          }
        }
      }

      this.kbLoading = true;
      try {
        const data = await apiFetch<PrinterKnowledgeBase>('/admin/api/knowledge-base');
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

    async fetchServicesTree(force: boolean = false): Promise<ServiceTreeNode[] | null> {
      if (this.servicesTree && !force) return this.servicesTree;

      if (!force) {
        const cached = sessionStorage.getItem('intralink_services_cache');
        if (cached) {
          try {
            this.servicesTree = JSON.parse(cached);
            return this.servicesTree;
          } catch {
            // ignore
          }
        }
      }

      this.servicesTreeLoading = true;
      try {
        const data = await apiFetch<ServiceTreeNode[]>('/admin/api/services-tree');
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

    invalidate(): void {
      this.kbData = null;
      this.servicesTree = null;
      sessionStorage.removeItem('intralink_kb_cache');
      sessionStorage.removeItem('intralink_services_cache');
    },
  },
});
