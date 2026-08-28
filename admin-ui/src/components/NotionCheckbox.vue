<template>
  <div 
    class="notion-checkbox-wrap" 
    :class="{ 
      'is-checked': modelValue, 
      'is-disabled': disabled,
      'is-sm': size === 'sm'
    }"
    @click.stop="toggle"
    tabindex="0"
    @keydown.space.prevent="toggle"
    @keydown.enter.prevent="toggle"
  >
    <div class="notion-checkbox-box">
      <svg 
        v-if="modelValue" 
        viewBox="0 0 24 24" 
        class="check-icon"
        fill="none" 
        stroke="currentColor" 
        stroke-width="3.2" 
        stroke-linecap="round" 
        stroke-linejoin="round"
      >
        <polyline points="20 6 9 17 4 12"></polyline>
      </svg>
    </div>
    <span v-if="$slots.default || label" class="notion-checkbox-label">
      <slot>{{ label }}</slot>
    </span>
  </div>
</template>

<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    label?: string;
    disabled?: boolean;
    size?: 'sm' | 'md';
  }>(),
  {
    modelValue: false,
    label: '',
    disabled: false,
    size: 'md',
  }
);

const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void;
  (e: 'change', val: boolean): void;
}>();

const toggle = () => {
  if (props.disabled) return;
  const newVal = !props.modelValue;
  emit('update:modelValue', newVal);
  emit('change', newVal);
};
</script>

<style scoped>
.notion-checkbox-wrap {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  cursor: pointer;
  user-select: none;
  font-size: 0.76rem;
  color: var(--text-secondary);
  transition: all 0.15s ease;
  outline: none;
  line-height: 1;
}

.notion-checkbox-wrap:hover {
  color: var(--text-primary);
}

.notion-checkbox-box {
  width: 17px;
  height: 17px;
  border-radius: 4px;
  background: var(--bg-surface);
  border: 1.5px solid var(--border-hover);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s cubic-bezier(0.16, 1, 0.3, 1);
  flex-shrink: 0;
  color: #ffffff;
}

.notion-checkbox-wrap.is-sm .notion-checkbox-box {
  width: 14px;
  height: 14px;
  border-radius: 3px;
  border-width: 1.2px;
}

.notion-checkbox-wrap:hover .notion-checkbox-box {
  border-color: var(--accent-primary);
  background: var(--bg-hover);
  box-shadow: 0 0 0 2px rgba(35, 131, 226, 0.15);
}

.notion-checkbox-wrap:focus-visible .notion-checkbox-box {
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 3px rgba(35, 131, 226, 0.25);
}

.notion-checkbox-wrap.is-checked .notion-checkbox-box {
  background: var(--accent-primary);
  border-color: var(--accent-primary);
  box-shadow: 0 1px 4px rgba(35, 131, 226, 0.35);
}

.check-icon {
  width: 11px;
  height: 11px;
  stroke: #ffffff;
  animation: checkPop 0.15s cubic-bezier(0.16, 1, 0.3, 1);
}

.notion-checkbox-wrap.is-sm .check-icon {
  width: 9px;
  height: 9px;
  stroke-width: 3.5;
}

.notion-checkbox-label {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.notion-checkbox-wrap.is-disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

@keyframes checkPop {
  0% {
    transform: scale(0.6);
    opacity: 0;
  }
  100% {
    transform: scale(1);
    opacity: 1;
  }
}
</style>
