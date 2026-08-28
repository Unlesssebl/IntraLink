<template>
  <Teleport to="body">
    <div v-if="isOpen" class="lightbox-overlay" @click="close" @keydown.esc="close">
      <div class="lightbox-dialog" @click.stop>
        <!-- Шапка Lightbox -->
        <div class="lightbox-header">
          <div class="lightbox-title-wrap">
            <span class="lightbox-filename" :title="title">{{ title || 'Просмотр изображения' }}</span>
            <span v-if="sizeText" class="lightbox-size">{{ sizeText }}</span>
          </div>

          <div class="lightbox-controls">
            <button class="ctrl-btn" title="Уменьшить" @click="zoomOut">
              <svg viewBox="0 0 24 24" width="16" height="16">
                <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" fill="none"/>
                <line x1="8" y1="11" x2="14" y2="11" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </button>
            <span class="zoom-level">{{ Math.round(zoom * 100) }}%</span>
            <button class="ctrl-btn" title="Увеличить" @click="zoomIn">
              <svg viewBox="0 0 24 24" width="16" height="16">
                <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" fill="none"/>
                <line x1="11" y1="8" x2="11" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="8" y1="11" x2="14" y2="11" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </button>
            <button class="ctrl-btn" title="Сбросить масштаб" @click="resetZoom">1:1</button>
            <button class="ctrl-btn" title="Повернуть на 90°" @click="rotate">
              <svg viewBox="0 0 24 24" width="16" height="16">
                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
            <a v-if="imageUrl" :href="imageUrl" target="_blank" download class="ctrl-btn" title="Скачать">
              <svg viewBox="0 0 24 24" width="16" height="16">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </a>
            <button class="ctrl-btn close-btn" title="Закрыть (Esc)" @click="close">
              <svg viewBox="0 0 24 24" width="16" height="16">
                <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- Контейнер изображения -->
        <div class="lightbox-stage" @wheel.prevent="handleWheel">
          <img 
            v-if="imageUrl" 
            :src="imageUrl" 
            :alt="title" 
            class="lightbox-img" 
            :style="{
              transform: `scale(${zoom}) rotate(${rotation}deg)`,
              transition: isDragging ? 'none' : 'transform 0.15s ease'
            }"
            @error="handleImgError"
          />
          <div v-if="imgError" class="img-error-fallback">
            <svg viewBox="0 0 24 24" width="48" height="48">
              <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="2" fill="none"/>
              <circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/>
              <polyline points="21 15 16 10 5 21" stroke="currentColor" stroke-width="2" fill="none"/>
            </svg>
            <p>Не удалось отобразить изображение напрямую</p>
            <a :href="imageUrl" target="_blank" class="btn btn-outline btn-sm">Открыть в новой вкладке ↗</a>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';

const isOpen = ref(false);
const imageUrl = ref('');
const title = ref('');
const sizeText = ref('');
const zoom = ref(1);
const rotation = ref(0);
const isDragging = ref(false);
const imgError = ref(false);

const open = (url: string, imgTitle: string = '', imgSize: string = '') => {
  imageUrl.value = url;
  title.value = imgTitle;
  sizeText.value = imgSize;
  zoom.value = 1;
  rotation.value = 0;
  imgError.value = false;
  isOpen.value = true;
};

const close = () => {
  isOpen.value = false;
  imageUrl.value = '';
};

const zoomIn = () => {
  zoom.value = Math.min(zoom.value + 0.25, 4);
};

const zoomOut = () => {
  zoom.value = Math.max(zoom.value - 0.25, 0.25);
};

const resetZoom = () => {
  zoom.value = 1;
  rotation.value = 0;
};

const rotate = () => {
  rotation.value = (rotation.value + 90) % 360;
};

const handleWheel = (e: WheelEvent) => {
  if (e.deltaY < 0) {
    zoomIn();
  } else {
    zoomOut();
  }
};

const handleImgError = () => {
  imgError.value = true;
};

const handleKeydown = (e: KeyboardEvent) => {
  if (!isOpen.value) return;
  if (e.key === 'Escape') {
    close();
  } else if (e.key === '+' || e.key === '=') {
    zoomIn();
  } else if (e.key === '-') {
    zoomOut();
  } else if (e.key === '0') {
    resetZoom();
  }
};

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown);
});

defineExpose({ open, close });
</script>

<style scoped>
.lightbox-overlay {
  position: fixed;
  inset: 0;
  background: rgba(4, 5, 10, 0.94);
  backdrop-filter: blur(12px);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
}

.lightbox-dialog {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  max-width: 1300px;
  max-height: 92vh;
  background: #0e0f16;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 30px 60px rgba(0, 0, 0, 0.8);
}

.lightbox-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.85rem 1.25rem;
  background: #13141d;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.lightbox-title-wrap {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
}

.lightbox-filename {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 500px;
}

.lightbox-size {
  font-size: 0.75rem;
  color: var(--text-2);
  background: rgba(255, 255, 255, 0.05);
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
}

.lightbox-controls {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.ctrl-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  color: var(--text-2);
  width: 32px;
  height: 32px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
  transition: all 0.15s;
  text-decoration: none;
}

.ctrl-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text);
  border-color: var(--border-hover);
}

.ctrl-btn.close-btn {
  background: rgba(244, 63, 94, 0.15);
  border-color: rgba(244, 63, 94, 0.3);
  color: #f43f5e;
  margin-left: 0.5rem;
}

.ctrl-btn.close-btn:hover {
  background: #f43f5e;
  color: white;
}

.zoom-level {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.78rem;
  color: var(--text-2);
  min-width: 45px;
  text-align: center;
}

.lightbox-stage {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: #06070a;
  position: relative;
  cursor: grab;
}

.lightbox-img {
  max-width: 95%;
  max-height: 90%;
  object-fit: contain;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
  user-select: none;
}

.img-error-fallback {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  color: var(--text-3);
  text-align: center;
}

.img-error-fallback svg {
  stroke: var(--text-3);
}

.img-error-fallback p {
  font-size: 0.9rem;
  color: var(--text-2);
}
</style>
