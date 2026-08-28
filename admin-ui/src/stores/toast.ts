import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { ToastNotification, ToastType, ToastAction } from '../types/common';

export const useToastStore = defineStore('toast', () => {
  const toasts = ref<ToastNotification[]>([]);
  let nextId = 1;

  const addToast = (options: {
    message: string;
    type?: ToastType;
    title?: string | null;
    duration?: number;
    action?: ToastAction | null;
  }): number => {
    const id = nextId++;
    const toast: ToastNotification = {
      id,
      message: options.message,
      type: options.type || 'info',
      title: options.title || null,
      duration: options.duration !== undefined ? options.duration : 4000,
      action: options.action || null,
      createdAt: Date.now(),
    };

    toasts.value.push(toast);

    if (toast.duration > 0) {
      setTimeout(() => {
        removeToast(id);
      }, toast.duration);
    }

    return id;
  };

  const removeToast = (id: number) => {
    const idx = toasts.value.findIndex(t => t.id === id);
    if (idx !== -1) {
      toasts.value.splice(idx, 1);
    }
  };

  const success = (message: string, title: string = 'Успешно', duration: number = 3500) => {
    return addToast({ message, type: 'success', title, duration });
  };

  const error = (message: string, title: string = 'Ошибка', duration: number = 5000) => {
    return addToast({ message, type: 'error', title, duration });
  };

  const warning = (message: string, title: string = 'Внимание', duration: number = 4500) => {
    return addToast({ message, type: 'warning', title, duration });
  };

  const info = (message: string, title: string = 'Информация', duration: number = 3500) => {
    return addToast({ message, type: 'info', title, duration });
  };

  return {
    toasts,
    addToast,
    removeToast,
    success,
    error,
    warning,
    info,
  };
});
