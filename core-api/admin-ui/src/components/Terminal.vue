<template>
  <div class="terminal">
    <div class="terminal-bar">
      <div class="term-dots">
        <div class="term-dot"></div>
        <div class="term-dot"></div>
        <div class="term-dot"></div>
      </div>
      <span class="term-title">{{ title }}</span>
      <button v-if="logs.length > 0" class="btn-ghost btn-sm" @click="handleClear" style="padding: 2px 8px; font-size: 0.7rem;">
        Очистить
      </button>
      <span v-else></span>
    </div>
    <div ref="bodyRef" class="term-body">
      <div v-if="logs.length === 0" class="term-line-sys">Ожидание вывода логов...</div>
      <span 
        v-for="(line, index) in logs" 
        :key="index" 
        :class="getLineClass(line)"
      >{{ line }}<br></span>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue';

const props = defineProps({
  title: {
    type: String,
    default: 'Терминал'
  },
  logs: {
    type: Array,
    default: () => []
  }
});

const emit = defineEmits(['clear']);

const bodyRef = ref(null);

const getLineClass = (line) => {
  if (line.includes('[ERROR]') || line.includes('FAIL')) return 'term-line-err';
  if (line.includes('[OK]') || line.includes('done') || line.includes('успешно') || line.includes('ЗАВЕРШЕНО')) return 'term-line-ok';
  if (line.includes('[WARN]')) return 'term-line-warn';
  if (line.includes('[SYSTEM]')) return 'term-line-sys';
  return 'term-line-info';
};

const handleClear = () => {
  emit('clear');
};

watch(() => props.logs, () => {
  nextTick(() => {
    if (bodyRef.value) {
      bodyRef.value.scrollTop = bodyRef.value.scrollHeight;
    }
  });
}, { deep: true });
</script>

<style scoped>
.terminal {
  display: block;
}
.terminal-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
