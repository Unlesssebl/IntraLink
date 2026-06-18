<template>
  <div class="stats-row">
    <div v-for="(card, idx) in items" :key="idx" class="stat-card">
      <div class="stat-icon" :class="card.color">
        <!-- check icon -->
        <svg v-if="card.icon === 'check'" viewBox="0 0 24 24">
          <polyline points="20 6 9 17 4 12" />
        </svg>
        <!-- inbox / active icon -->
        <svg v-else-if="card.icon === 'inbox'" viewBox="0 0 24 24">
          <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
          <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
        </svg>
        <!-- error / cross icon -->
        <svg v-else-if="card.icon === 'error'" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="10" />
          <line x1="15" y1="9" x2="9" y2="15" />
          <line x1="9" y1="9" x2="15" y2="15" />
        </svg>
        <!-- info icon -->
        <svg v-else-if="card.icon === 'info'" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="16" x2="12" y2="12" />
          <line x1="12" y1="8" x2="12.01" y2="8" />
        </svg>
        <!-- redirect / arrow-right icon -->
        <svg v-else-if="card.icon === 'redirect'" viewBox="0 0 24 24">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
          <polyline points="9 17 9 12 15 12 15 17"></polyline>
        </svg>
      </div>
      <div class="stat-body">
        <div class="stat-num">{{ card.value !== null && card.value !== undefined ? card.value : '—' }}</div>
        <div class="stat-desc">{{ card.label }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  items: {
    type: Array,
    required: true
  }
});
</script>
