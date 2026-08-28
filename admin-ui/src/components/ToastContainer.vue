<template>
  <div class="toast-container" aria-live="polite">
    <TransitionGroup name="toast-anim">
      <div 
        v-for="t in toastStore.toasts" 
        :key="t.id" 
        class="toast-item" 
        :class="`toast-${t.type}`"
      >
        <div class="toast-icon">
          <!-- Success -->
          <svg v-if="t.type === 'success'" viewBox="0 0 24 24" width="18" height="18">
            <polyline points="20 6 9 17 4 12" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <!-- Error -->
          <svg v-else-if="t.type === 'error'" viewBox="0 0 24 24" width="18" height="18">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none"/>
            <line x1="15" y1="9" x2="9" y2="15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="9" y1="9" x2="15" y2="15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          <!-- Warning -->
          <svg v-else-if="t.type === 'warning'" viewBox="0 0 24 24" width="18" height="18">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" stroke="currentColor" stroke-width="2" fill="none"/>
            <line x1="12" y1="9" x2="12" y2="13" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="12" y1="17" x2="12.01" y2="17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          <!-- Info -->
          <svg v-else viewBox="0 0 24 24" width="18" height="18">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none"/>
            <line x1="12" y1="16" x2="12" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="12" y1="8" x2="12.01" y2="8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </div>

        <div class="toast-body">
          <div v-if="t.title" class="toast-title">{{ t.title }}</div>
          <div class="toast-message">{{ t.message }}</div>
        </div>

        <button class="toast-close" @click="toastStore.removeToast(t.id)" aria-label="Закрыть">
          <svg viewBox="0 0 24 24" width="14" height="14">
            <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup>
import { useToastStore } from '../stores/toast';

const toastStore = useToastStore();
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: 1.5rem;
  right: 1.5rem;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-width: 420px;
  pointer-events: none;
}

.toast-item {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.9rem 1.1rem;
  border-radius: var(--radius);
  background: var(--surface-2);
  border: 1px solid var(--border);
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.4), 0 2px 6px rgba(0, 0, 0, 0.2);
  color: var(--text);
  font-size: 0.875rem;
  backdrop-filter: blur(12px);
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.toast-icon {
  flex-shrink: 0;
  margin-top: 0.1rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.toast-body {
  flex: 1;
  min-width: 0;
}

.toast-title {
  font-weight: 600;
  font-size: 0.85rem;
  margin-bottom: 0.2rem;
  letter-spacing: -0.01em;
}

.toast-message {
  color: var(--text-2);
  line-height: 1.4;
  word-break: break-word;
}

.toast-close {
  background: none;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  padding: 0.2rem;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.15s, background 0.15s;
}

.toast-close:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.08);
}

/* Color types */
.toast-success {
  border-color: rgba(16, 185, 129, 0.3);
}
.toast-success .toast-icon {
  color: var(--green);
}
.toast-success .toast-title {
  color: var(--green);
}

.toast-error {
  border-color: rgba(244, 63, 94, 0.3);
}
.toast-error .toast-icon {
  color: var(--red);
}
.toast-error .toast-title {
  color: var(--red);
}

.toast-warning {
  border-color: rgba(245, 158, 11, 0.3);
}
.toast-warning .toast-icon {
  color: var(--yellow);
}
.toast-warning .toast-title {
  color: var(--yellow);
}

.toast-info {
  border-color: rgba(96, 165, 250, 0.3);
}
.toast-info .toast-icon {
  color: var(--blue);
}
.toast-info .toast-title {
  color: var(--blue);
}

/* Animations */
.toast-anim-enter-from {
  opacity: 0;
  transform: translateX(40px) scale(0.95);
}
.toast-anim-enter-to {
  opacity: 1;
  transform: translateX(0) scale(1);
}
.toast-anim-leave-from {
  opacity: 1;
  transform: translateX(0) scale(1);
}
.toast-anim-leave-to {
  opacity: 0;
  transform: translateX(40px) scale(0.9);
}
</style>
