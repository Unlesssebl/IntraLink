import { defineStore } from 'pinia';
import { ref } from 'vue';

export type ThemeMode = 'light' | 'dark';

export const useThemeStore = defineStore('theme', () => {
  const saved = localStorage.getItem('intralink_theme') as ThemeMode | null;
  const currentTheme = ref<ThemeMode>(saved || 'light');

  const applyTheme = (theme: ThemeMode) => {
    currentTheme.value = theme;
    localStorage.setItem('intralink_theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
    }
  };

  const toggleTheme = () => {
    const next = currentTheme.value === 'light' ? 'dark' : 'light';
    applyTheme(next);
  };

  // Инициализация при старте
  applyTheme(currentTheme.value);

  return {
    currentTheme,
    applyTheme,
    toggleTheme,
  };
});
