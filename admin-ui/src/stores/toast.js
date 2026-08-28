import { defineStore } from 'pinia';
import { ref } from 'vue';

export const useToastStore = defineStore('toast', () => {
  const toasts = ref([]);
  let nextId = 1;

  const addToast = ({ message, type = 'info', title = null, duration = 4000, action = null }) => {
    const id = nextId++;
    const toast = {
      id,
      message,
      type, // 'success' | 'error' | 'warning' | 'info'
      title,
      duration,
      action, // { label: string, onClick: Function }
      createdAt: Date.now(),
    };

    toasts.value.push(toast);

    if (duration > 0) {
      setTimeout(() => {
        removeToast(id);
      }, duration);
    }

    return id;
  };

  const removeToast = (id) => {
    const index = toasts.value.findIndex(t => t.id === id);
    if (index !== -1) {
      toasts.value.splice(index, 1);
    }
  };

  const success = (message, title = 'Успешно', duration = 3500) => {
    return addToast({ message, type: 'success', title, duration });
  };

  const error = (message, title = 'Ошибка', duration = 5000) => {
    return addToast({ message, type: 'error', title, duration });
  };

  const warning = (message, title = 'Внимание', duration = 4500) => {
    return addToast({ message, type: 'warning', title, duration });
  };

  const info = (message, title = 'Информация', duration = 3500) => {
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
