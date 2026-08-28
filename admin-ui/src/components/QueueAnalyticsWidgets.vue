<template>
  <div class="analytics-container">
    <div class="analytics-header">
      <div class="analytics-title">
        <svg viewBox="0 0 24 24" width="16" height="16" class="analytics-icon">
          <line x1="18" y1="20" x2="18" y2="10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="12" y1="20" x2="12" y2="4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="6" y1="20" x2="6" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <span>Аналитическая сводка очереди 1-й линии</span>
      </div>
      <button class="toggle-analytics-btn" @click="isCollapsed = !isCollapsed">
        <span>{{ isCollapsed ? 'Развернуть метрики' : 'Свернуть' }}</span>
        <svg viewBox="0 0 24 24" width="13" height="13" :style="{ transform: isCollapsed ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }">
          <polyline points="18 15 12 9 6 15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </button>
    </div>

    <div v-show="!isCollapsed" class="analytics-grid">
      <!-- 1. Распределение по сервисам (Интерактивная диаграмма) -->
      <div class="analytics-card">
        <div class="card-head">
          <span class="card-title">Распределение по разделам</span>
          <span class="card-badge">{{ totalTasks }} заявок</span>
        </div>

        <div class="donut-chart-wrap">
          <svg viewBox="0 0 100 100" class="donut-svg">
            <!-- Фоновый круг -->
            <circle cx="50" cy="50" r="38" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="12"/>
            <!-- Сегменты сервисов -->
            <circle
              v-for="seg in donutSegments"
              :key="seg.name"
              cx="50"
              cy="50"
              r="38"
              fill="none"
              :stroke="seg.color"
              stroke-width="12"
              :stroke-dasharray="`${seg.dashLength} ${seg.dashGap}`"
              :stroke-dashoffset="seg.dashOffset"
              class="donut-segment"
            >
              <title>{{ seg.name }}: {{ seg.count }} ({{ seg.percent }}%)</title>
            </circle>
          </svg>
          <div class="donut-center-text">
            <span class="donut-num">{{ totalTasks }}</span>
            <span class="donut-sub">активно</span>
          </div>
        </div>

        <!-- Легенда сервисов -->
        <div class="donut-legend">
          <div 
            v-for="seg in donutSegments.slice(0, 4)" 
            :key="seg.name" 
            class="legend-item"
            :title="`${seg.name}: ${seg.count}`"
          >
            <span class="legend-dot" :style="{ background: seg.color }"></span>
            <span class="legend-name">{{ seg.name }}</span>
            <span class="legend-pct">{{ seg.percent }}%</span>
          </div>
        </div>
      </div>

      <!-- 2. Доступность парка рабочих станций (Ping / SMB / WinRM) -->
      <div class="analytics-card">
        <div class="card-head">
          <span class="card-title">Доступность ПК заявителей</span>
          <span class="card-badge">{{ hostStats.totalKnown }} хостов</span>
        </div>

        <div class="host-health-bars">
          <!-- Онлайн -->
          <div class="health-row">
            <div class="health-label">
              <span class="dot-green"></span>
              <span>Онлайн в сети (Ping/SMB)</span>
              <strong>{{ hostStats.online }} ({{ hostStats.onlinePct }}%)</strong>
            </div>
            <div class="progress-track">
              <div class="progress-bar bar-green" :style="{ width: `${hostStats.onlinePct}%` }"></div>
            </div>
          </div>

          <!-- Офлайн -->
          <div class="health-row">
            <div class="health-label">
              <span class="dot-red"></span>
              <span>Офлайн / Недоступен</span>
              <strong>{{ hostStats.offline }} ({{ hostStats.offlinePct }}%)</strong>
            </div>
            <div class="progress-track">
              <div class="progress-bar bar-red" :style="{ width: `${hostStats.offlinePct}%` }"></div>
            </div>
          </div>

          <!-- Проверяется / Без имени -->
          <div class="health-row">
            <div class="health-label">
              <span class="dot-gray"></span>
              <span>Проверка / Без имени ПК</span>
              <strong>{{ hostStats.pending }} ({{ hostStats.pendingPct }}%)</strong>
            </div>
            <div class="progress-track">
              <div class="progress-bar bar-gray" :style="{ width: `${hostStats.pendingPct}%` }"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 3. Уверенность классификатора & Готовность к авто-триажу -->
      <div class="analytics-card">
        <div class="card-head">
          <span class="card-title">Уверенность классификатора</span>
          <span class="card-badge">Rule Engine</span>
        </div>

        <div class="confidence-bars">
          <!-- Высокая уверенность (9-10) -->
          <div class="conf-item high" @click="$emit('filter-confidence', 'high')">
            <div class="conf-num">{{ confStats.high }}</div>
            <div class="conf-info">
              <div class="conf-title">Высокая (Оценка 9–10)</div>
              <div class="conf-sub">Типовые заявки — готовы к авто-триажу</div>
            </div>
            <span class="conf-tag">{{ confStats.highPct }}%</span>
          </div>

          <!-- Средняя (6-8) -->
          <div class="conf-item medium" @click="$emit('filter-confidence', 'medium')">
            <div class="conf-num">{{ confStats.medium }}</div>
            <div class="conf-info">
              <div class="conf-title">Средняя (Оценка 6–8)</div>
              <div class="conf-sub">Требуется беглый взгляд инженера</div>
            </div>
            <span class="conf-tag">{{ confStats.mediumPct }}%</span>
          </div>

          <!-- Низкая (< 6) -->
          <div class="conf-item low" @click="$emit('filter-confidence', 'low')">
            <div class="conf-num">{{ confStats.low }}</div>
            <div class="conf-info">
              <div class="conf-title">Ручной разбор (Оценка &lt; 6)</div>
              <div class="conf-sub">Нестандартные или сложные инциденты</div>
            </div>
            <span class="conf-tag">{{ confStats.lowPct }}%</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import type { TaskItem, HostDiagnostics } from '../types/task';

const props = defineProps<{
  tasks: TaskItem[];
  hostStatusMap: Record<string, HostDiagnostics>;
}>();

defineEmits<{
  (e: 'filter-confidence', level: 'high' | 'medium' | 'low'): void;
}>();

const isCollapsed = ref(false);

const totalTasks = computed(() => props.tasks.length);

// Палитра для сегментов диаграммы
const PALETTE = ['#4f46e5', '#10b981', '#f59e0b', '#ec4899', '#6366f1', '#14b8a6', '#8b5cf6'];

// 1. Расчет сегментов Donut Chart (по 17 корневым сервисам IntraService)
const donutSegments = computed(() => {
  if (totalTasks.value === 0) return [];
  const counts: Record<string, number> = {};
  props.tasks.forEach(t => {
    const s = t.root_service_name || '11. Общие вопросы';
    counts[s] = (counts[s] || 0) + 1;
  });

  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const circumference = 2 * Math.PI * 38; // ~238.76

  let currentOffset = 0;
  return sorted.map(([name, count], idx) => {
    const fraction = count / totalTasks.value;
    const dashLength = fraction * circumference;
    const dashGap = circumference - dashLength;
    const segment = {
      name,
      count,
      percent: Math.round(fraction * 100),
      color: PALETTE[idx % PALETTE.length],
      dashLength,
      dashGap,
      dashOffset: -currentOffset,
    };
    currentOffset += dashLength;
    return segment;
  });
});

// 2. Статистика доступности хостов
const hostStats = computed(() => {
  let online = 0;
  let offline = 0;
  let pending = 0;

  props.tasks.forEach(t => {
    if (!t.pc_name || !t.pc_name.trim()) {
      pending++;
      return;
    }
    const diag = props.hostStatusMap[t.pc_name.trim()];
    if (!diag || diag.loading) {
      pending++;
    } else if (diag.is_online) {
      online++;
    } else {
      offline++;
    }
  });

  const total = totalTasks.value || 1;
  return {
    totalKnown: online + offline,
    online,
    onlinePct: Math.round((online / total) * 100),
    offline,
    offlinePct: Math.round((offline / total) * 100),
    pending,
    pendingPct: Math.round((pending / total) * 100),
  };
});

// 3. Статистика уверенности Rule Engine
const confStats = computed(() => {
  let high = 0;
  let medium = 0;
  let low = 0;

  props.tasks.forEach(t => {
    if (t.score >= 9) high++;
    else if (t.score >= 6) medium++;
    else low++;
  });

  const total = totalTasks.value || 1;
  return {
    high,
    highPct: Math.round((high / total) * 100),
    medium,
    mediumPct: Math.round((medium / total) * 100),
    low,
    lowPct: Math.round((low / total) * 100),
  };
});
</script>

<style scoped>
.analytics-container {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  margin-bottom: 1rem;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}

.analytics-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.65rem 1rem;
  background: var(--bg-sidebar);
  border-bottom: 1px solid var(--border-subtle);
}

.analytics-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-primary);
}

.analytics-icon {
  color: var(--accent-primary);
}

.toggle-analytics-btn {
  background: transparent;
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 500;
  cursor: pointer;
  padding: 0.2rem 0.55rem;
  border-radius: 4px;
  transition: all 0.15s ease;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.toggle-analytics-btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
  border-color: var(--border-hover);
}

.analytics-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.85rem;
  padding: 1rem;
}

@media (max-width: 1100px) {
  .analytics-grid {
    grid-template-columns: 1fr;
  }
}

.analytics-card {
  background: var(--bg-sidebar);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0.85rem 1rem;
  display: flex;
  flex-direction: column;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.card-title {
  font-size: 0.76rem;
  font-weight: 600;
  color: var(--text-primary);
}

.card-badge {
  font-size: 0.68rem;
  font-weight: 600;
  font-family: var(--font-mono);
  background: var(--tag-default-bg);
  color: var(--text-secondary);
  padding: 0.12rem 0.4rem;
  border-radius: 4px;
}

.donut-chart-wrap {
  position: relative;
  width: 100px;
  height: 100px;
  margin: 0 auto 0.65rem;
}

.donut-svg {
  transform: rotate(-90deg);
  width: 100%;
  height: 100%;
}

.donut-segment {
  transition: stroke-dasharray 0.3s ease;
}

.donut-center-text {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.donut-num {
  font-size: 1.15rem;
  font-weight: 700;
  line-height: 1;
  font-family: var(--font-mono);
  color: var(--text-primary);
}

.donut-sub {
  font-size: 0.62rem;
  color: var(--text-muted);
  margin-top: 0.1rem;
}

.donut-legend {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.72rem;
}

.legend-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-name {
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.legend-pct {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 0.68rem;
}

.host-health-bars {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  justify-content: center;
  flex: 1;
}

.health-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.health-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.72rem;
  color: var(--text-secondary);
}

.health-label strong {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--text-primary);
}

.dot-green {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  display: inline-block;
  margin-right: 0.35rem;
}

.dot-red {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ef4444;
  display: inline-block;
  margin-right: 0.35rem;
}

.dot-gray {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-muted);
  display: inline-block;
  margin-right: 0.35rem;
}

.progress-track {
  height: 5px;
  background: var(--bg-hover);
  border-radius: 3px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  border-radius: 3px;
  transition: width 0.4s ease;
}

.bar-green {
  background: #10b981;
}

.bar-red {
  background: #ef4444;
}

.bar-gray {
  background: var(--text-muted);
}

.confidence-bars {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  flex: 1;
  justify-content: center;
}

.conf-item {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.45rem 0.65rem;
  border-radius: 5px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-surface);
  cursor: pointer;
  transition: all 0.15s ease;
}

.conf-item:hover {
  background: var(--bg-hover);
  border-color: var(--border-hover);
}

.conf-item.high {
  border-left: 3px solid #10b981;
}

.conf-item.medium {
  border-left: 3px solid var(--accent-primary);
}

.conf-item.low {
  border-left: 3px solid #f59e0b;
}

.conf-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
  min-width: 26px;
}

.conf-info {
  flex: 1;
  min-width: 0;
}

.conf-title {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text);
}

.conf-sub {
  font-size: 0.68rem;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conf-tag {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: var(--text-2);
  background: rgba(255, 255, 255, 0.05);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
}
</style>
