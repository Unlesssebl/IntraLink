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
            placeholder="Поиск заявки, компьютера, раздела или действие..."
            @keydown.down.prevent="navigateDown"
            @keydown.up.prevent="navigateUp"
            @keydown.enter.prevent="executeActive"
            @keydown.esc.prevent="close"
          />
          <span class="esc-badge">ESC</span>
        </div>

        <div class="palette-results">
          <!-- Группы результатов -->
          <div v-if="filteredItems.length === 0" class="palette-empty">
            Ничего не найдено по запросу «{{ query }}»
          </div>

          <div 
            v-for="(item, idx) in filteredItems" 
            :key="item.id || idx"
            class="palette-item"
            :class="{ active: activeIndex === idx }"
            @mouseenter="activeIndex = idx"
            @click="executeItem(item)"
          >
            <div class="item-icon">{{ item.icon }}</div>
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

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { useQueueStore } from '../stores/queue';
import { useToastStore } from '../stores/toast';
import { apiFetch } from '../api';

const router = useRouter();
const queueStore = useQueueStore();
const toastStore = useToastStore();

const isOpen = ref(false);
const query = ref('');
const activeIndex = ref(0);
const inputRef = ref(null);

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
const handleGlobalKeydown = (e) => {
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

defineExpose({ open, close });

const staticActions = [
  {
    id: 'nav-queue',
    icon: '⚡',
    title: 'Очередь 1-й линии Helpdesk',
    subtitle: 'Перейти к интерактивному триажу заявок',
    category: 'Навигация',
    action: () => router.push('/queue'),
  },
  {
    id: 'nav-history',
    icon: '📜',
    title: 'Журнал операций принтеров',
    subtitle: 'Просмотр очереди фоновых задач и логов',
    category: 'Навигация',
    action: () => router.push('/history'),
  },
  {
    id: 'nav-ai',
    icon: '🤖',
    title: 'AI & База знаний RAG',
    subtitle: 'Управление каталогом услуг и автоответами',
    category: 'Навигация',
    action: () => router.push('/ai-worker'),
  },
  {
    id: 'nav-settings',
    icon: '⚙️',
    title: 'Настройки и статус инфраструктуры',
    subtitle: 'Конфигурация WinRM, SMB и синхронизация',
    category: 'Навигация',
    action: () => router.push('/settings'),
  },
  {
    id: 'act-refresh',
    icon: '🔄',
    title: 'Обновить очередь заявок',
    subtitle: 'Синхронизировать задачи с IntraService',
    category: 'Действие',
    action: () => queueStore.fetchQueue(),
  },
  {
    id: 'act-fast-reindex',
    icon: '⚡',
    title: 'Быстрая переиндексация драйверов принтеров',
    subtitle: 'Обновить кэш драйверов за несколько секунд',
    category: 'Инфраструктура',
    action: async () => {
      try {
        await apiFetch('/admin/api/printers/fast-reindex', { method: 'POST' });
        toastStore.success('Быстрая переиндексация драйверов запущена');
      } catch (e) {
        toastStore.error(e.message);
      }
    },
  },
];

const filteredItems = computed(() => {
  const q = query.value.toLowerCase().trim();
  
  // Добавляем текущие заявки из очереди
  const taskItems = queueStore.tasks.map(t => ({
    id: `task-${t.id}`,
    icon: '🎫',
    title: `#${t.id}: ${t.name}`,
    subtitle: `${t.creator || 'Пользователь'} • ${t.pc_name ? 'ПК: ' + t.pc_name : ''} • ${t.category_label || ''}`,
    category: 'Заявка',
    action: () => {
      router.push('/queue');
      queueStore.openTaskDrawer(t.id);
    }
  }));

  const all = [...staticActions, ...taskItems];
  if (!q) return all.slice(0, 10);

  return all.filter(item => {
    return (
      item.title.toLowerCase().includes(q) ||
      (item.subtitle && item.subtitle.toLowerCase().includes(q)) ||
      item.category.toLowerCase().includes(q)
    );
  }).slice(0, 12);
});

const navigateDown = () => {
  if (activeIndex.value < filteredItems.value.length - 1) {
    activeIndex.value++;
  }
};

const navigateUp = () => {
  if (activeIndex.value > 0) {
    activeIndex.value--;
  }
};

const executeActive = () => {
  const item = filteredItems.value[activeIndex.value];
  if (item) {
    executeItem(item);
  }
};

const executeItem = (item) => {
  close();
  if (item.action) {
    item.action();
  }
};
</script>

<style scoped>
.palette-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(5px);
  z-index: 2500;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 10vh;
  animation: fadeIn 0.15s ease-out;
}

.palette-modal {
  width: 620px;
  max-width: 90vw;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.palette-input-wrap {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}

.palette-search-icon {
  color: var(--text-3);
}

.palette-input {
  flex: 1;
  background: none;
  border: none;
  color: var(--text);
  font-size: 1rem;
  font-family: inherit;
  outline: none;
}
.palette-input::placeholder {
  color: var(--text-3);
}

.esc-badge {
  font-size: 0.7rem;
  color: var(--text-3);
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
}

.palette-results {
  max-height: 400px;
  overflow-y: auto;
  padding: 0.5rem;
}

.palette-empty {
  padding: 2rem;
  text-align: center;
  color: var(--text-3);
  font-size: 0.88rem;
}

.palette-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 0.9rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.1s;
}
.palette-item:hover, .palette-item.active {
  background: rgba(79, 70, 229, 0.15);
}

.item-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
}

.item-info {
  flex: 1;
  min-width: 0;
}

.item-title {
  font-size: 0.88rem;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.item-sub {
  font-size: 0.75rem;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.item-badge {
  font-size: 0.7rem;
  background: var(--surface-2);
  color: var(--text-3);
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  border: 1px solid var(--border);
}
</style>
