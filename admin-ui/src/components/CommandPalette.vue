<template>
  <Teleport to="body">
    <div v-if="isOpen" class="palette-backdrop" @click="close">
      <div class="palette-modal" @click.stop>
        <div class="palette-input-wrap">
          <svg viewBox="0 0 24 24" width="18" height="18" class="palette-search-icon">
            <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" fill="none"/>
            <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          <input 
            ref="inputRef"
            v-model="query" 
            type="text" 
            class="palette-input" 
            placeholder="Номер заявки (#12345), имя ПК, раздел или действие..."
            @keydown.down.prevent="navigateDown"
            @keydown.up.prevent="navigateUp"
            @keydown.enter.prevent="executeActive"
            @keydown.esc.prevent="close"
          />
          <span class="esc-badge">ESC</span>
        </div>

        <div class="palette-results">
          <!-- Пустое состояние -->
          <div v-if="filteredItems.length === 0" class="palette-empty">
            Ничего не найдено по запросу «{{ query }}»
          </div>

          <!-- Список результатов -->
          <div 
            v-for="(item, idx) in filteredItems" 
            :key="item.id || idx"
            class="palette-item"
            :class="{ active: activeIndex === idx }"
            @mouseenter="activeIndex = idx"
            @click="executeItem(item)"
          >
            <div class="item-icon">
              <svg v-if="item.icon === 'queue' || item.icon === 'task'" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
              <svg v-else-if="item.icon === 'ai'" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2a8 8 0 0 0-8 8c0 3.3 2 6.2 5 7.4V20a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-2.6c3-1.2 5-4.1 5-7.4a8 8 0 0 0-8-8z"/><line x1="9.5" y1="9" x2="9.51" y2="9"/><line x1="14.5" y1="9" x2="14.51" y2="9"/></svg>
              <svg v-else-if="item.icon === 'settings'" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
              <svg v-else-if="item.icon === 'check-all'" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>
              <svg v-else-if="item.icon === 'reset'" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
              <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            </div>
            <div class="item-info">
              <div class="item-title">{{ item.title }}</div>
              <div v-if="item.subtitle" class="item-sub">{{ item.subtitle }}</div>
            </div>
            <span class="item-badge">{{ item.category }}</span>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { useQueueStore } from '../stores/queue';
import { useToastStore } from '../stores/toast';
import { apiFetch } from '../api';

interface PaletteActionItem {
  id: string;
  icon: string;
  title: string;
  subtitle?: string;
  category: 'Навигация' | 'Заявки' | 'Команды' | 'Действия';
  action: () => void | Promise<any>;
}

const router = useRouter();
const queueStore = useQueueStore();
const toastStore = useToastStore();

const isOpen = ref(false);
const query = ref('');
const activeIndex = ref(0);
const inputRef = ref<HTMLInputElement | null>(null);

const open = () => {
  isOpen.value = true;
  query.value = '';
  activeIndex.value = 0;
  nextTick(() => {
    inputRef.value?.focus();
  });
};

const close = () => {
  isOpen.value = false;
};

// Хоткей Ctrl+K / Cmd+K
const handleGlobalKeydown = (e: KeyboardEvent) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (isOpen.value) {
      close();
    } else {
      open();
    }
  }
};

onMounted(() => {
  window.addEventListener('keydown', handleGlobalKeydown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleGlobalKeydown);
});

// Базовые команды системы
const staticCommands: PaletteActionItem[] = [
  {
    id: 'nav-queue',
    icon: 'queue',
    title: 'Перейти в Очередь 1-й линии',
    subtitle: 'Список открытых инцидентов и пакетный триаж',
    category: 'Навигация',
    action: () => { router.push('/queue'); },
  },
  {
    id: 'nav-ai',
    icon: 'ai',
    title: 'Перейти в Базу знаний & AI',
    subtitle: 'Управление pgvector, RAG-базой и автоответами',
    category: 'Навигация',
    action: () => { router.push('/ai-worker'); },
  },
  {
    id: 'nav-settings',
    icon: 'settings',
    title: 'Перейти в Настройки & Инфраструктура',
    subtitle: 'Статус интеграций, сброс кэшей и мониторинг',
    category: 'Навигация',
    action: () => { router.push('/settings'); },
  },
  {
    id: 'cmd-select-confident',
    icon: 'check-all',
    title: 'Выбрать все типовые заявки (Оценка ≥ 9)',
    subtitle: 'Авто-выбор заявок высокой уверенности в текущем списке',
    category: 'Действия',
    action: () => {
      queueStore.selectAllConfident(queueStore.filteredTasks);
      toastStore.info(`Выбрано типовых заявок: ${queueStore.selectedTaskIds.size}`);
    },
  },
  {
    id: 'cmd-reset-filters',
    icon: 'reset',
    title: 'Сбросить все фильтры очереди',
    subtitle: 'Очистить поиск, фильтры уверенности и доступности ПК',
    category: 'Действия',
    action: () => {
      queueStore.resetFilters();
      toastStore.info('Фильтры очереди сброшены');
    },
  },
];

// Динамические результаты поиска
const filteredItems = computed<PaletteActionItem[]>(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) {
    return staticCommands;
  }

  const results: PaletteActionItem[] = [];

  // 1. Если введен номер заявки (например #12345 или 12345)
  const numMatch = q.match(/^#?(\d+)$/);
  if (numMatch) {
    const taskId = parseInt(numMatch[1], 10);
    results.push({
      id: `task-direct-${taskId}`,
      icon: 'search',
      title: `Открыть карточку заявки #${taskId}`,
      subtitle: 'Прямой просмотр деталей, истории и шаблонов',
      category: 'Заявки',
      action: () => {
        queueStore.openTaskDrawer(taskId);
      },
    });
  }

  // 2. Поиск по открытым заявкам в очереди
  queueStore.tasks.forEach(t => {
    if (
      t.id.toString().includes(q) ||
      (t.name && t.name.toLowerCase().includes(q)) ||
      (t.creator && t.creator.toLowerCase().includes(q)) ||
      (t.pc_name && t.pc_name.toLowerCase().includes(q))
    ) {
      results.push({
        id: `task-${t.id}`,
        icon: 'task',
        title: `#${t.id}: ${t.name}`,
        subtitle: `${t.creator} | ПК: ${t.pc_name || '—'} | ${t.service_name}`,
        category: 'Заявки',
        action: () => {
          queueStore.openTaskDrawer(t.id);
        },
      });
    }
  });

  // 3. Поиск по командам и навигации
  staticCommands.forEach(cmd => {
    if (
      cmd.title.toLowerCase().includes(q) ||
      (cmd.subtitle && cmd.subtitle.toLowerCase().includes(q))
    ) {
      results.push(cmd);
    }
  });

  return results.slice(0, 10);
});

const navigateDown = () => {
  if (filteredItems.value.length === 0) return;
  activeIndex.value = (activeIndex.value + 1) % filteredItems.value.length;
};

const navigateUp = () => {
  if (filteredItems.value.length === 0) return;
  activeIndex.value = (activeIndex.value - 1 + filteredItems.value.length) % filteredItems.value.length;
};

const executeActive = () => {
  const item = filteredItems.value[activeIndex.value];
  if (item) {
    executeItem(item);
  }
};

const executeItem = async (item: PaletteActionItem) => {
  close();
  await item.action();
};

defineExpose({ open, close });
</script>

<style scoped>
.palette-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 15, 15, 0.65);
  backdrop-filter: blur(6px);
  z-index: 1000;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 14vh;
}

.palette-modal {
  width: 600px;
  max-width: 92vw;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  box-shadow: var(--shadow-floating);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.palette-input-wrap {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.85rem 1rem;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-sidebar);
}

.palette-search-icon {
  color: var(--accent-primary);
  flex-shrink: 0;
}

.palette-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 0.9rem;
  color: var(--text-primary);
  font-family: inherit;
}

.palette-input::placeholder {
  color: var(--text-muted);
}

.esc-badge {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  background: var(--tag-default-bg);
  color: var(--text-muted);
  padding: 0.12rem 0.38rem;
  border-radius: 4px;
  border: 1px solid var(--border-subtle);
}

.palette-results {
  max-height: 360px;
  overflow-y: auto;
  padding: 0.4rem;
}

.palette-empty {
  padding: 2.5rem 1rem;
  text-align: center;
  font-size: 0.82rem;
  color: var(--text-muted);
}

.palette-item {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.5rem 0.75rem;
  border-radius: 5px;
  cursor: pointer;
  transition: all 0.12s ease;
}

.palette-item:hover {
  background: var(--bg-hover);
}

.palette-item.active {
  background: var(--bg-selected);
}

.item-icon {
  font-size: 1.05rem;
  flex-shrink: 0;
}

.item-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.item-title {
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.item-sub {
  font-size: 0.7rem;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.item-badge {
  font-size: 0.66rem;
  font-weight: 600;
  background: var(--tag-default-bg);
  color: var(--tag-default-text);
  padding: 0.12rem 0.4rem;
  border-radius: 4px;
}
</style>
