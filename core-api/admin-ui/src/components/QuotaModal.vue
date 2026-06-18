<template>
  <div v-if="isOpen" class="overlay open">
    <div class="modal">
      <div class="modal-logo">
        <span class="modal-title">Настройка квот услуги</span>
      </div>
      <p class="modal-desc">
        Укажите целевые лимиты сбора примеров для услуги:<br>
        <strong style="color: var(--primary);">"{{ serviceName }}"</strong>
      </p>
      
      <div class="form-group">
        <label class="form-label" for="f-quota-closed">Квота для "Выполнена" (Закрыта)</label>
        <input 
          v-model.number="closed" 
          type="number" 
          id="f-quota-closed" 
          class="form-control" 
          placeholder="Например: 10" 
          required 
        />
      </div>
      
      <div class="form-group">
        <label class="form-label" for="f-quota-cancelled">Квота для "Отменена"</label>
        <input 
          v-model.number="cancelled" 
          type="number" 
          id="f-quota-cancelled" 
          class="form-control" 
          placeholder="Например: 5" 
          required 
        />
      </div>
      
      <div style="display: flex; gap: 0.5rem; margin-top: 1.5rem;">
        <button 
          class="btn btn-primary" 
          style="flex: 1; justify-content: center;" 
          @click="handleSave"
        >
          Сохранить
        </button>
        <button 
          class="btn btn-outline" 
          style="flex: 1; justify-content: center;" 
          @click="handleClose"
        >
          Отмена
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    required: true
  },
  serviceId: {
    type: Number,
    required: true
  },
  serviceName: {
    type: String,
    required: true
  },
  initialClosed: {
    type: Number,
    default: 10
  },
  initialCancelled: {
    type: Number,
    default: 5
  }
});

const emit = defineEmits(['close', 'save']);

const closed = ref(props.initialClosed);
const cancelled = ref(props.initialCancelled);

// Следим за изменениями пропсов при открытии
watch(() => props.isOpen, (newVal) => {
  if (newVal) {
    closed.value = props.initialClosed;
    cancelled.value = props.initialCancelled;
  }
});

const handleSave = () => {
  emit('save', {
    serviceId: props.serviceId,
    closed: closed.value,
    cancelled: cancelled.value
  });
};

const handleClose = () => {
  emit('close');
};
</script>
