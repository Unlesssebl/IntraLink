<template>
  <section class="screen active">
    <div class="card mb-0">
      <div class="card-header">
        <div>
          <div class="card-title">Поддерживаемые модели принтеров</div>
          <div class="card-subtitle">{{ countLabel }}</div>
        </div>
      </div>
      <div class="card-body">
        <div v-if="cacheStore.kbLoading" style="color: var(--text-3); font-size: 0.85rem;">
          Загрузка базы моделей...
        </div>
        <div v-else-if="printers.length === 0" style="color: var(--text-3); font-size: 0.85rem;">
          База моделей пуста.
        </div>
        <div v-else class="printer-grid">
          <div v-for="p in printers" :key="p.model_key" class="printer-card">
            <div class="printer-card-header">
              <span class="printer-name">{{ p.display_name }}</span>
              <span class="printer-vendor">{{ p.vendor }}</span>
            </div>
            <div class="printer-detail">
              <div class="printer-detail-row">
                <span class="printer-detail-key">Ключ</span>
                <span class="printer-detail-val">{{ p.model_key }}</span>
              </div>
              <div class="printer-detail-row">
                <span class="printer-detail-key">Тип</span>
                <span class="printer-detail-val">
                  <span class="badge" :class="`badge-${connBadge(p.connection_type)}`">
                    {{ p.connection_type || '—' }}
                  </span>
                </span>
              </div>
              <div class="printer-detail-row">
                <span class="printer-detail-key">Драйвер</span>
                <span class="printer-detail-val">{{ p.driver_name }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, inject, onUnmounted } from 'vue';
import { useCacheStore } from '../stores/cache';

const cacheStore = useCacheStore();

const printers = computed(() => {
  return cacheStore.kbData?.printers || [];
});

const plural = (n, one, few, many) => {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} ${one}`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${n} ${few}`;
  return `${n} ${many}`;
};

const countLabel = computed(() => {
  if (cacheStore.kbLoading) return 'Загрузка...';
  const len = printers.value.length;
  return plural(len, 'модель в базе', 'модели в базе', 'моделей в базе');
});

const connBadge = (type) => {
  if (!type) return 'progress';
  return type === 'usb' ? 'pending' : 'progress';
};

const fetchKB = () => cacheStore.fetchKnowledgeBase(true);

// Регистрация обновления в Topbar
const registerRefresh = inject('registerRefresh');
let unregisterRefresh = null;

onMounted(() => {
  cacheStore.fetchKnowledgeBase();
  
  if (registerRefresh) {
    unregisterRefresh = registerRefresh(fetchKB);
  }
});

onUnmounted(() => {
  if (unregisterRefresh) unregisterRefresh();
});
</script>
