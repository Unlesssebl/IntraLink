<template>
  <div class="services-tree">
    <div v-for="node in nodes" :key="node.id" class="tree-node">
      <div class="tree-node-content" style="gap: 0.35rem;">
        <input 
          type="checkbox" 
          :id="`${prefix}-chk-${node.id}`" 
          :value="node.id" 
          :checked="isChecked(node.id)"
          @change="toggleCheckbox(node.id)"
          @click.stop
        />
        <span 
          class="tree-node-title" 
          :class="{ selected: selectedId === node.id }"
          @click="selectNode(node)"
        >
          {{ node.name }}{{ getProgressText(node) }}
        </span>
      </div>
      
      <!-- Рекурсивный вызов для потомков -->
      <div v-if="node.children && node.children.length > 0" class="tree-node-children">
        <ServicesTree 
          :nodes="node.children"
          :model-value="modelValue"
          :selected-id="selectedId"
          :prefix="prefix"
          :show-progress="showProgress"
          :progress-data="progressData"
          :service-quotas="serviceQuotas"
          :global-quotas="globalQuotas"
          @update:model-value="$emit('update:modelValue', $event)"
          @select-node="$emit('select-node', $event)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>


const props = defineProps({
  nodes: {
    type: Array,
    required: true
  },
  modelValue: {
    type: Array,
    default: () => []
  },
  selectedId: {
    type: Number,
    default: null
  },
  prefix: {
    type: String,
    default: 'service'
  },
  showProgress: {
    type: Boolean,
    default: false
  },
  progressData: {
    type: Object,
    default: () => ({})
  },
  serviceQuotas: {
    type: Object,
    default: () => ({})
  },
  globalQuotas: {
    type: Object,
    default: () => ({})
  }
});

const emit = defineEmits(['update:modelValue', 'select-node']);

const isChecked = (id) => {
  return props.modelValue.includes(id);
};

const toggleCheckbox = (id) => {
  const newValue = [...props.modelValue];
  const index = newValue.indexOf(id);
  if (index === -1) {
    newValue.push(id);
  } else {
    newValue.splice(index, 1);
  }
  emit('update:modelValue', newValue);
};

const selectNode = (node) => {
  emit('select-node', node);
};

// Вычисление лимита
const getQuotaForServiceAndStatus = (serviceId, statusId) => {
  const sq = props.serviceQuotas[serviceId] || {};
  const q = sq[statusId];
  if (q !== undefined && q !== null) return parseInt(q);
  const g = props.globalQuotas[statusId];
  if (g !== undefined && g !== null) return parseInt(g);
  return statusId === 28 ? 10 : 5;
};

// Вычисление прогресса
const getServiceProgress = (node) => {
  let current = 0;
  let quota = 0;
  
  const traverse = (n) => {
    const isLeaf = !n.children || n.children.length === 0;
    if (isLeaf) {
      const q28 = getQuotaForServiceAndStatus(n.id, 28);
      const q30 = getQuotaForServiceAndStatus(n.id, 30);
      quota += (q28 + q30);
      
      const stat = props.progressData[n.id] || {};
      current += (stat["Закрыта"] || 0) + (stat["Отменена"] || 0);
    } else {
      n.children.forEach(traverse);
    }
  };
  
  traverse(node);
  return { current, quota };
};

const getProgressText = (node) => {
  if (!props.showProgress) return '';
  const progress = getServiceProgress(node);
  if (progress.quota === 0) return '';
  return ` [${progress.current}/${progress.quota}]`;
};
</script>

<style scoped>
.services-tree {
  width: 100%;
}
.tree-node-children {
  margin-left: 1.25rem;
}
</style>
