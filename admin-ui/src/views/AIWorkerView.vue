<template>
  <section class="screen active">
    <!-- Метрики AI -->
    <StatsRow :items="statsItems" />

    <div class="ai-layout">
      <!-- Левая колонка: Режим автоответа и Дерево услуг -->
      <div class="ai-sidebar" :style="{ width: `${sidebarWidth}px` }">
        <!-- Режим автоответа -->
        <div class="sidebar-section">
          <label class="form-label" for="f-ai-mode">Режим автоответа</label>
          <select v-model="aiMode" id="f-ai-mode" class="form-control" @change="saveAIMode" :disabled="savingConfig">
            <option value="comment_only">Только комментарий (Безопасный)</option>
            <option value="comment_and_wait">Комментарий + статус "Требует уточнения"</option>
            <option value="comment_and_resolve">Комментарий + статус "Выполнена" (при высоком confidence)</option>
          </select>

          <!-- Оповещение об успешности сохранения настроек -->
          <div v-if="configAlertMsg" class="alert-mini" :class="configAlertType">
            {{ configAlertMsg }}
          </div>
        </div>

        <!-- Дерево разделов (навигационное) -->
        <div class="sidebar-section tree-section">
          <label class="form-label">Каталог услуг</label>
          <div v-if="cacheStore.servicesTreeLoading" class="services-tree-loading">
            <div class="spinner"></div>
            <span>Загрузка дерева услуг...</span>
          </div>
          <div v-else-if="!servicesTree || servicesTree.length === 0" class="services-tree-empty">
            Каталог услуг пуст
          </div>
          <div v-else class="services-tree-scroll">
            <ServicesTree 
              :nodes="servicesTree"
              :model-value="aiServiceIds"
              :selected-id="selectedServiceId"
              prefix="ai"
              :show-progress="true"
              :progress-data="ragStats"
              :service-quotas="ragServiceQuotas"
              :global-quotas="ragGlobalQuotas"
              :show-checkboxes="true"
              @update:model-value="updateAiServiceIdsFromTree"
              @select-node="handleSelectCategory"
            />
          </div>
        </div>
      </div>

      <!-- Разделитель с возможностью изменения ширины -->
      <div class="sidebar-resizer" @mousedown="startResize"></div>

      <!-- Правая колонка: Детали, Навыки, Лог, Библиотека примеров, Тестирование -->
      <div class="ai-main-content">
        
        <!-- Заглушка, если ничего не выбрано -->
        <div v-if="!selectedServiceId" class="ai-empty-state-card">
          <div class="empty-state-icon">
            <svg viewBox="0 0 24 24" width="48" height="48">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none"/>
              <line x1="12" y1="8" x2="12" y2="12" stroke="currentColor" stroke-width="2"/>
              <line x1="12" y1="16" x2="12.01" y2="16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </div>
          <h3 class="empty-state-title">Выберите раздел в каталоге</h3>
          <p class="empty-state-desc">
            Нажмите на любую услугу или категорию в левой панели, чтобы открыть детальные настройки автоответов, лимиты RAG-примеров и библиотеку знаний.
          </p>
        </div>

        <template v-else>
          <!-- Детали выбранного раздела -->
          <div class="card category-detail-card">
            <div class="card-body">
              <div class="category-header-wrap">
                <div class="category-info">
                  <h2 class="category-name">{{ selectedServiceName }}</h2>
                  <span class="category-path">Управление автоответами и базой RAG</span>
                </div>
                <div class="category-actions">
                  <!-- Тоггл включения автоответа -->
                  <div class="toggle-control">
                    <span class="toggle-label">Автоответ ИИ:</span>
                    <label class="switch">
                      <input type="checkbox" v-model="isAIEnabledForSelected" />
                      <span class="slider"></span>
                    </label>
                    <span class="toggle-status" :class="{ enabled: isAIEnabledForSelected }">
                      {{ isAIEnabledForSelected ? 'Активен' : 'Выключен' }}
                    </span>
                  </div>
                  
                  <!-- Кнопка сборщика для раздела -->
                  <button class="btn btn-outline btn-sm" @click="triggerRAGBuild([selectedServiceId])" :disabled="ragRunning">
                    <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    Обучить раздел
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- Карточки навыков для выбранного раздела -->
          <div class="card">
            <div class="card-header">
              <div>
                <div class="card-title">Карточки навыков (конечные услуги)</div>
                <div class="card-subtitle">Индивидуальные лимиты сбора и точечный запуск обучения</div>
              </div>
            </div>
            <div class="card-body">
              <div v-if="skillLeaves.length === 0" class="skill-empty-state">
                В этом разделе нет конечных услуг (листьев).
              </div>
              <div v-else class="skill-grid">
                <div v-for="leaf in skillLeaves" :key="leaf.id" class="skill-card">
                  <div class="skill-card-header">
                    <span v-if="skillLeaves.length > 1" class="skill-name">{{ leaf.name }}</span>
                    <span v-else class="skill-name" style="color: var(--text-2); font-size: 0.85rem; font-weight: 500;">Настройки квот для этой услуги</span>
                    <!-- Кнопка точечного обучения -->
                    <button class="btn-icon-only" title="Обучить только эту услугу" @click="triggerRAGBuild([leaf.id])" :disabled="ragRunning">
                      <svg viewBox="0 0 24 24"><polygon points="8 5 19 12 8 19 8 5"/></svg>
                    </button>
                  </div>
                  
                  <!-- Прогресс сбора примеров -->
                  <div class="skill-progress-wrap">
                    <div class="skill-progress-bar-bg">
                      <div class="skill-progress-bar-fg" :style="{ width: `${getSkillPercent(leaf)}%` }"></div>
                    </div>
                    <div class="skill-progress-text">
                      <span>Собрано: {{ getSkillCurrent(leaf) }} из {{ getSkillQuota(leaf) }}</span>
                      <span>{{ Math.round(getSkillPercent(leaf)) }}%</span>
                    </div>
                  </div>
                  
                  <!-- Статистика по статусам -->
                  <div class="skill-stats-breakdown">
                    <span>Выполнено: {{ getLeafStatusCount(leaf.id, 'Закрыта') }}</span>
                    <span>Отменено: {{ getLeafStatusCount(leaf.id, 'Отменена') }}</span>
                  </div>

                  <!-- Инлайн Редактор Квот -->
                  <div class="quota-inline-edit-section">
                    <div class="quota-row">
                      <span class="quota-status-label">Квота "Выполнена":</span>
                      <input 
                        type="number" 
                        class="quota-input-mini"
                        :value="getLeafQuota(leaf.id, 28)"
                        @change="updateInlineQuota(leaf.id, 28, $event.target.value)"
                        @keydown.enter="$event.target.blur()"
                      />
                    </div>
                    <div class="quota-row">
                      <span class="quota-status-label">Квота "Отменена":</span>
                      <input 
                        type="number" 
                        class="quota-input-mini"
                        :value="getLeafQuota(leaf.id, 30)"
                        @change="updateInlineQuota(leaf.id, 30, $event.target.value)"
                        @keydown.enter="$event.target.blur()"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Синхронизация базы знаний RAG (Сворачиваемый лог) -->
          <div class="card">
            <div class="card-header" style="display: flex; justify-content: space-between; align-items: center;">
              <div>
                <div class="card-title">Синхронизация базы знаний RAG (pgvector)</div>
                <div class="card-subtitle">Загрузка примеров по квотам всех активных разделов</div>
              </div>
              <div style="display: flex; gap: 1rem; align-items: center;">
                <div v-if="ragRunning" style="display: flex; align-items: center; gap: 0.5rem; color: var(--blue); font-size: 0.85rem; font-weight: 500;">
                  <div class="spinner"></div> Выполняется сбор...
                </div>
                <button v-else class="btn btn-outline btn-sm" id="btn-rag-build" @click="triggerRAGBuild(null)">
                  <svg viewBox="0 0 24 24" width="14" height="14" style="fill:currentColor; margin-right: 4px; display: inline-block; vertical-align: middle;"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                  Запустить полный сбор
                </button>
                
                <a href="#" style="font-size: 0.8rem; color: var(--text-3); text-decoration: underline;" @click.prevent="showTerminal = !showTerminal">
                  {{ showTerminal ? 'Скрыть лог сбора' : 'Показать лог сбора' }}
                </a>
              </div>
            </div>
            <!-- Сворачиваемый терминал -->
            <div v-if="showTerminal" class="card-body" style="padding: 0 1.5rem 1.5rem; border-top: 1px solid var(--border);">
              <Terminal 
                title="Лог перестроения RAG" 
                :logs="ragLogs" 
                @clear="clearRagLogs"
              />
            </div>
          </div>

          <!-- Библиотека собранных примеров (Примеры RAG) -->
          <div class="card">
            <div class="card-header">
              <div>
                <div class="card-title">Библиотека эталонных примеров (Knowledge Library)</div>
                <div class="card-subtitle" id="rag-library-subtitle">{{ librarySubtitle }}</div>
              </div>
            </div>
            
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style="width: 80px;">Задача</th>
                    <th>Раздел</th>
                    <th>Проблема</th>
                    <th>Решение</th>
                    <th style="width: 100px;">Действие</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="loadingExamples" class="empty-row">
                    <td colspan="5">Загрузка библиотеки примеров...</td>
                  </tr>
                  <tr v-else-if="examples.length === 0" class="empty-row">
                    <td colspan="5">Примеры отсутствуют. Обучите раздел для наполнения базы знаний.</td>
                  </tr>
                  <tr v-else v-for="e in examples" :key="e.task_id">
                    <td class="td-mono">#{{ e.task_id }}</td>
                    <td style="font-size:0.78rem;color:var(--text-2);">{{ e.service_name }}</td>
                    <td style="max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" :title="e.problem">
                      {{ e.problem }}
                    </td>
                    <td style="max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" :title="e.solution">
                      {{ e.solution }}
                    </td>
                    <td>
                      <button class="btn btn-outline btn-sm" style="color:var(--red);border-color:var(--red-bg);" @click="deleteExample(e.task_id)">
                        Удалить
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            
            <!-- Пагинация -->
            <div class="pagination" v-if="totalPages > 0">
              <button class="btn btn-outline btn-sm" :disabled="examplesPage <= 1" @click="prevPage">Назад</button>
              <span style="font-size: 0.85rem; color: var(--text-2);">
                Страница {{ examplesPage }} из {{ totalPages }}
              </span>
              <button class="btn btn-outline btn-sm" :disabled="examplesPage >= totalPages" @click="nextPage">Вперед</button>
            </div>
          </div>

          <!-- Тестирование автоответа -->
          <div class="card">
            <div class="card-header">
              <div>
                <div class="card-title">Тестирование AI-ответа</div>
                <div class="card-subtitle">Сгенерировать пробный автоответ для существующей задачи</div>
              </div>
            </div>
            <div class="card-body">
              <div class="form-group" style="margin-bottom: 1rem;">
                <label class="form-label" for="f-test-task-id">ID задачи</label>
                <input 
                  v-model.number="testTaskId" 
                  type="number" 
                  id="f-test-task-id" 
                  class="form-control" 
                  placeholder="Введите ID задачи из IntraService..." 
                  required 
                  :disabled="testingReply"
                />
              </div>
              
              <button class="btn btn-outline" :disabled="testingReply || !testTaskId" @click="runTestReply">
                <template v-if="testingReply">
                  <div class="spinner"></div> Генерация...
                </template>
                <template v-else>
                  Протестировать генерацию
                </template>
              </button>

              <!-- Результаты тестирования -->
              <div v-if="testResult" style="margin-top: 1.5rem; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 1rem;">
                <div style="font-size: 0.8rem; color: var(--text-2); margin-bottom: 0.5rem;">
                  <strong>Раздел:</strong> <span>{{ testResult.service_name }} (ID: {{ testResult.service_id }})</span><br>
                  <strong>Уверенность:</strong> <span>{{ testResult.confidence?.toFixed(2) }}</span> | 
                  <strong>Решение?</strong> <span>{{ testResult.can_resolve ? 'Да' : 'Нет' }}</span> | 
                  <strong>Уточнение?</strong> <span>{{ testResult.needs_clarification ? 'Да' : 'Нет' }}</span>
                </div>
                <div style="font-size: 0.72rem; color: var(--text-3); font-style: italic; margin-bottom: 0.5rem;">
                  Причина: {{ testResult.reason }}
                </div>
                <div style="font-family: inherit; font-size: 0.85rem; white-space: pre-wrap; padding: 0.5rem; background: rgba(0,0,0,0.25); border-radius: 4px; border: 1px solid var(--border);">
                  {{ testResult.generated_reply }}
                </div>
              </div>
            </div>
          </div>
        </template>

      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, inject, watch } from 'vue';
import { apiFetch } from '../api';
import { useCacheStore } from '../stores/cache';
import StatsRow from '../components/StatsRow.vue';
import ServicesTree from '../components/ServicesTree.vue';
import Terminal from '../components/Terminal.vue';
import { usePolling } from '../composables/usePolling';

const cacheStore = useCacheStore();

// Ресайз левой панели
const sidebarWidth = ref(parseInt(localStorage.getItem('ai-sidebar-width')) || 340);

const startResize = (e) => {
  e.preventDefault();
  const startX = e.clientX;
  const startWidth = sidebarWidth.value;

  const doResize = (moveEvent) => {
    const delta = moveEvent.clientX - startX;
    const newWidth = startWidth + delta;
    if (newWidth >= 280 && newWidth <= 600) {
      sidebarWidth.value = newWidth;
    }
  };

  const stopResize = () => {
    window.removeEventListener('mousemove', doResize);
    window.removeEventListener('mouseup', stopResize);
    localStorage.setItem('ai-sidebar-width', sidebarWidth.value);
  };

  window.addEventListener('mousemove', doResize);
  window.addEventListener('mouseup', stopResize);
};

// Метрики
const aiMetrics = ref({ classifications: 0, replied: 0, redirected: 0 });

const statsItems = computed(() => [
  { value: aiMetrics.value.classifications, label: 'Классификаций (с запуска)', color: 'blue', icon: 'info' },
  { value: aiMetrics.value.replied, label: 'Автоответов отправлено (с запуска)', color: 'green', icon: 'check' },
  { value: aiMetrics.value.redirected, label: 'Перенаправлено в другой раздел (с запуска)', color: 'red', icon: 'redirect' }
]);

// Конфигурация AI
const aiMode = ref('comment_only');
const aiServiceIds = ref([]);
const ragFilterId = ref(0);

const savingConfig = ref(false);
const configAlertMsg = ref('');
const configAlertType = ref('success');

// Дерево и RAG данные
const ragGlobalQuotas = ref({ "28": 10, "30": 5 });
const ragServiceQuotas = ref({});
const ragStats = ref({});
const ragRunning = ref(false);

// Выбранная категория и листья
const selectedServiceId = ref(null);
const selectedServiceName = ref('');
const selectedServiceNode = ref(null);

// SSE Лог RAG и Терминал
const showTerminal = ref(false);
const ragLogs = ref([]);
let ragSse = null;
let ragSseFirstMsg = true;

// Библиотека примеров RAG
const examples = ref([]);
const examplesPage = ref(1);
const totalExamples = ref(0);
const loadingExamples = ref(false);

const totalPages = computed(() => {
  return Math.max(1, Math.ceil(totalExamples.value / 10));
});

const librarySubtitle = computed(() => {
  if (selectedServiceId.value) {
    return `Показаны собранные примеры для раздела "${selectedServiceName.value}"`;
  }
  return 'Показаны последние собранные примеры для обучения ИИ';
});

// Тестирование
const testTaskId = ref(null);
const testingReply = ref(false);
const testResult = ref(null);

const servicesTree = computed(() => cacheStore.servicesTree);

// Листья выбранного узла (навыки)
const skillLeaves = computed(() => {
  if (!selectedServiceNode.value) return [];
  const leaves = [];
  const collectLeaves = (n) => {
    if (!n.children || n.children.length === 0) {
      leaves.push(n);
    } else {
      n.children.forEach(collectLeaves);
    }
  };
  collectLeaves(selectedServiceNode.value);
  return leaves;
});

// Автоответ активен для выбранного раздела
const isAIEnabledForSelected = computed({
  get() {
    return selectedServiceId.value ? aiServiceIds.value.includes(selectedServiceId.value) : false;
  },
  set(val) {
    if (selectedServiceId.value) {
      toggleAIForService(selectedServiceId.value, val);
    }
  }
});

// Хелперы для квот карточек навыков
const getLeafQuota = (serviceId, statusId) => {
  const sq = ragServiceQuotas.value[serviceId] || {};
  const q = sq[statusId];
  if (q !== undefined && q !== null) return parseInt(q);
  const g = ragGlobalQuotas.value[statusId];
  if (g !== undefined && g !== null) return parseInt(g);
  return statusId === 28 ? 10 : 5;
};

const getLeafStatusCount = (serviceId, statusName) => {
  const s = ragStats.value[serviceId] || {};
  return s[statusName] || 0;
};

const getSkillCurrent = (leaf) => {
  return getLeafStatusCount(leaf.id, 'Закрыта') + getLeafStatusCount(leaf.id, 'Отменена');
};

const getSkillQuota = (leaf) => {
  return getLeafQuota(leaf.id, 28) + getLeafQuota(leaf.id, 30);
};

const getSkillPercent = (leaf) => {
  const current = getSkillCurrent(leaf);
  const quota = getSkillQuota(leaf);
  return Math.min(100, (current / (quota || 1)) * 100);
};

// Загрузка статуса AI воркера и квот
const fetchAIStatus = async () => {
  try {
    const data = await apiFetch('/admin/api/ai-worker/status');
    aiMetrics.value = data.metrics || { classifications: 0, replied: 0, redirected: 0 };
    aiMode.value = data.config.auto_reply_mode || 'comment_only';
    aiServiceIds.value = data.config.auto_reply_service_ids || [];
    ragRunning.value = !!data.rag_running;
    
    // Если RAG сборщик запущен, а у нас нет SSE, открываем его
    if (ragRunning.value && !ragSse) {
      openRAGTerminal();
    }
  } catch (err) {
    console.error('Ошибка загрузки статуса AI:', err);
  }
};

const fetchRAGQuotasAndStats = async () => {
  try {
    // 1. Квоты
    try {
      const qData = await apiFetch('/admin/api/ai-worker/rag/quotas');
      ragFilterId.value = qData.filter_id || 0;
      ragGlobalQuotas.value = qData.global_quotas || { "28": 10, "30": 5 };
      ragServiceQuotas.value = qData.service_quotas || {};
    } catch (e) {
      console.error('Ошибка загрузки RAG квот:', e);
    }
    
    // 2. Статистика
    try {
      ragStats.value = await apiFetch('/admin/api/ai-worker/rag/stats');
    } catch (e) {
      console.error('Ошибка загрузки RAG статистики:', e);
    }
  } catch (e) {}
};

// Вспомогательная функция для сохранения RAG-квот на бэкенде
const saveRAGQuotasApi = async (filterId, globalQuotas, serviceQuotas) => {
  await apiFetch('/admin/api/ai-worker/rag/quotas', {
    method: 'POST',
    body: JSON.stringify({
      filter_id: filterId,
      global_quotas: globalQuotas,
      service_quotas: serviceQuotas
    })
  });
};

// Авто-сохранение режима автоответа при изменении
const saveAIMode = async () => {
  savingConfig.value = true;
  configAlertMsg.value = '';
  try {
    await apiFetch('/admin/api/ai-worker/config', {
      method: 'POST',
      body: JSON.stringify({
        auto_reply_service_ids: aiServiceIds.value,
        auto_reply_mode: aiMode.value
      })
    });
    configAlertType.value = 'success';
    configAlertMsg.value = 'Режим сохранен';
    setTimeout(() => {
      configAlertMsg.value = '';
    }, 3000);
  } catch (err) {
    configAlertType.value = 'error';
    configAlertMsg.value = 'Ошибка сохранения';
  } finally {
    savingConfig.value = false;
  }
};

// Переключение автоответа для раздела (из тоггла)
const toggleAIForService = async (serviceId, checked) => {
  const idx = aiServiceIds.value.indexOf(serviceId);
  if (checked) {
    if (idx === -1) aiServiceIds.value.push(serviceId);
  } else {
    if (idx !== -1) aiServiceIds.value.splice(idx, 1);
  }
  
  try {
    await apiFetch('/admin/api/ai-worker/config', {
      method: 'POST',
      body: JSON.stringify({
        auto_reply_service_ids: aiServiceIds.value,
        auto_reply_mode: aiMode.value
      })
    });
  } catch (err) {
    alert('Ошибка при изменении активности автоответа: ' + err.message);
    // Откатываем локальное состояние
    const revertIdx = aiServiceIds.value.indexOf(serviceId);
    if (checked) {
      if (revertIdx !== -1) aiServiceIds.value.splice(revertIdx, 1);
    } else {
      if (revertIdx === -1) aiServiceIds.value.push(serviceId);
    }
  }
};

// Изменение автоответа из дерева (чекбоксы)
const updateAiServiceIdsFromTree = async (newIds) => {
  aiServiceIds.value = newIds;
  try {
    await apiFetch('/admin/api/ai-worker/config', {
      method: 'POST',
      body: JSON.stringify({
        auto_reply_service_ids: aiServiceIds.value,
        auto_reply_mode: aiMode.value
      })
    });
  } catch (err) {
    alert('Ошибка при изменении активности автоответа: ' + err.message);
    await fetchAIStatus(); // Восстанавливаем с сервера при ошибке
  }
};

// Сохранение квот инлайн при изменении (blur / enter)
const updateInlineQuota = async (serviceId, statusId, value) => {
  const numVal = parseInt(value);
  if (isNaN(numVal) || numVal < 0) return;
  
  if (!ragServiceQuotas.value[serviceId]) {
    ragServiceQuotas.value[serviceId] = {};
  }
  ragServiceQuotas.value[serviceId][statusId.toString()] = numVal;
  
  try {
    await saveRAGQuotasApi(
      ragFilterId.value,
      ragGlobalQuotas.value,
      ragServiceQuotas.value
    );
    await fetchRAGQuotasAndStats();
  } catch (err) {
    alert('Ошибка при сохранении лимитов: ' + err.message);
  }
};

// Выбор категории в дереве услуг
const handleSelectCategory = (node) => {
  selectedServiceId.value = node.id;
  selectedServiceName.value = node.name;
  selectedServiceNode.value = node;
  
  examplesPage.value = 1;
  fetchExamples();
};

// Загрузка примеров RAG
const fetchExamples = async () => {
  loadingExamples.value = true;
  try {
    let url = `/admin/api/ai-worker/rag/examples?page=${examplesPage.value}&limit=10`;
    if (selectedServiceId.value) {
      url += `&service_id=${selectedServiceId.value}`;
    }
    
    const data = await apiFetch(url);
    examples.value = data.examples || [];
    totalExamples.value = data.total || 0;
  } catch (err) {
    console.error('Ошибка загрузки библиотеки RAG:', err);
  } finally {
    loadingExamples.value = false;
  }
};

const nextPage = () => {
  if (examplesPage.value < totalPages.value) {
    examplesPage.value++;
    fetchExamples();
  }
};

const prevPage = () => {
  if (examplesPage.value > 1) {
    examplesPage.value--;
    fetchExamples();
  }
};

// Удаление примера
const deleteExample = async (taskId) => {
  if (!confirm(`Вы действительно хотите удалить пример по задаче #${taskId}? Она будет безвозвратно удалена и занесена в черный список (больше никогда не будет скачиваться).`)) return;
  
  try {
    await apiFetch(`/admin/api/ai-worker/rag/examples/${taskId}`, {
      method: 'DELETE'
    });
    await fetchExamples();
    await fetchRAGQuotasAndStats();
  } catch (err) {
    alert('Ошибка при удалении: ' + err.message);
  }
};

// Запуск сборщика RAG (для конкретных услуг или глобально)
const triggerRAGBuild = async (serviceIds = null) => {
  let url = '/admin/api/ai-worker/rag/build';
  const opts = { method: 'POST' };
  if (serviceIds) {
    opts.body = JSON.stringify(serviceIds);
  }
  
  try {
    await apiFetch(url, opts);
    // Оптимистично выставляем флаг — не ждём следующего тика поллинга
    ragRunning.value = true;
    openRAGTerminal();
  } catch (err) {
    alert('Ошибка при запуске сборщика RAG: ' + err.message);
  }
};

const clearRagLogs = () => {
  ragLogs.value = [];
};

// Подключение к SSE логам RAG
const openRAGTerminal = () => {
  showTerminal.value = true;
  ragLogs.value = ['[SYSTEM] Подключение к логам RAG...'];
  
  // Закрываем старое соединение, если оно есть
  if (ragSse) {
    ragSse.close();
    ragSse = null;
  }
  
  ragSseFirstMsg = true;
  ragSse = new EventSource('/admin/api/ai-worker/rag/logs');
  
  // Обычные сообщения с логами
  ragSse.onmessage = (e) => {
    // Первое сообщение после подключения или реконнекта означает,
    // что бэкенд начинает слать историю заново.
    // Очищаем старые логи, чтобы избежать дубликации.
    if (ragSseFirstMsg) {
      ragLogs.value = [];
      ragSseFirstMsg = false;
    }
    ragLogs.value.push(e.data);
  };
  
  // Финальное событие завершения сборки с сервера — закрываем SSE штатно
  ragSse.addEventListener('done', () => {
    ragRunning.value = false;
    if (ragSse) {
      ragSse.close();
      ragSse = null;
    }
    // Обновляем статистику и прогресс базы знаний
    fetchRAGQuotasAndStats();
  });
  
  // Ошибка соединения — не закрываем, даём браузеру автоматически переподключиться.
  // Сбрасываем флаг, чтобы при переподключении история заменила дубликаты.
  ragSse.onerror = () => {
    ragSseFirstMsg = true;
  };
};

// Тестирование автоответа
const runTestReply = async () => {
  if (!testTaskId.value) return;
  
  testingReply.value = true;
  testResult.value = null;
  
  try {
    const data = await apiFetch(`/admin/api/ai-worker/test-reply/${testTaskId.value}`, {
      method: 'POST'
    });
    testResult.value = data;
  } catch (err) {
    alert('Ошибка генерации теста: ' + err.message);
  } finally {
    testingReply.value = false;
  }
};

// Регистрация обновления в Topbar
const registerRefresh = inject('registerRefresh');
let unregisterRefresh = null;

const refreshAll = () => {
  fetchAIStatus();
  fetchRAGQuotasAndStats();
  if (selectedServiceId.value) {
    fetchExamples();
  }
};

let polling = null;

// Автоматически открывать лог терминала, если процесс сборщика стал активен
watch(ragRunning, (newVal) => {
  if (newVal) {
    showTerminal.value = true;
  }
});

onMounted(async () => {
  // Загружаем дерево услуг, если оно еще не загружено
  try {
    await cacheStore.fetchServicesTree();
  } catch (e) {
    console.error(e);
  }
  
  refreshAll();
  
  // Опрашиваем статус AI-воркера и RAG-процесса каждые 5 сек
  polling = usePolling(fetchAIStatus, 5000, false);
  
  if (registerRefresh) {
    unregisterRefresh = registerRefresh(refreshAll);
  }
});

onUnmounted(() => {
  if (polling) polling.stop();
  if (unregisterRefresh) unregisterRefresh();
  
  if (ragSse) {
    ragSse.close();
  }
});
</script>

<style scoped>
.ai-layout {
  display: flex;
  margin-top: 1.5rem;
  align-items: flex-start;
  gap: 0;
}

.ai-sidebar {
  flex-shrink: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  position: sticky;
  top: 85px;
  max-height: calc(100vh - 120px);
}

.sidebar-resizer {
  width: 12px;
  cursor: col-resize;
  align-self: stretch;
  background: transparent;
  position: relative;
  z-index: 10;
  margin: 0 0.5rem;
  flex-shrink: 0;
}

.sidebar-resizer::after {
  content: '';
  position: absolute;
  left: 5px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--border);
  transition: background-color 0.15s;
  border-radius: 1px;
}

.sidebar-resizer:hover::after,
.sidebar-resizer:active::after {
  background-color: var(--primary);
}

.sidebar-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.tree-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.services-tree-loading,
.services-tree-empty {
  font-size: 0.85rem;
  color: var(--text-3);
  padding: 1rem 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.services-tree-scroll {
  flex: 1;
  overflow-y: auto;
  margin-top: 0.5rem;
  padding-right: 0.25rem;
}

.alert-mini {
  font-size: 0.72rem;
  margin-top: 0.25rem;
  padding: 0.35rem 0.5rem;
  border-radius: 4px;
  text-align: center;
}

.alert-mini.success {
  background: var(--green-bg);
  color: var(--green);
}

.alert-mini.error {
  background: var(--red-bg);
  color: var(--red);
}

.ai-main-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* Empty State Card */
.ai-empty-state-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 4rem 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 1.25rem;
  min-height: 400px;
}

.empty-state-icon {
  color: var(--text-3);
  background: rgba(255, 255, 255, 0.02);
  width: 80px;
  height: 80px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--border);
}

.empty-state-title {
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--text);
}

.empty-state-desc {
  font-size: 0.88rem;
  color: var(--text-2);
  max-width: 440px;
  line-height: 1.6;
}

/* Category Details Header Card */
.category-detail-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.category-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
}

.category-info {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.category-name {
  font-size: 1.2rem;
  font-weight: 700;
  color: var(--text);
}

.category-path {
  font-size: 0.78rem;
  color: var(--text-2);
}

.category-actions {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  flex-wrap: wrap;
}

/* Toggle Switch Styles */
.toggle-control {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.toggle-label {
  font-size: 0.85rem;
  color: var(--text-2);
  font-weight: 500;
}

.switch {
  position: relative;
  display: inline-block;
  width: 40px;
  height: 20px;
}

.switch input {
  opacity: 0;
  width: 0;
  height: 0;
}

.slider {
  position: absolute;
  cursor: pointer;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: var(--text-3);
  transition: .3s;
  border-radius: 20px;
}

.slider:before {
  position: absolute;
  content: "";
  height: 14px;
  width: 14px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  transition: .3s;
  border-radius: 50%;
}

input:checked + .slider {
  background-color: var(--green);
}

input:checked + .slider:before {
  transform: translateX(20px);
}

.toggle-status {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-3);
  min-width: 65px;
}

.toggle-status.enabled {
  color: var(--green);
}

/* Skill Leaves Grid and Card */
.skill-empty-state {
  font-size: 0.85rem;
  color: var(--text-3);
  text-align: center;
  padding: 2rem 0;
}

.skill-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1.25rem;
}

.skill-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  transition: border-color 0.15s, transform 0.15s;
}

.skill-card:hover {
  border-color: var(--border-hover);
}

.skill-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.5rem;
}

.skill-name {
  font-size: 0.88rem;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text);
}

.btn-icon-only {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-2);
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  padding: 0;
  flex-shrink: 0;
}

.btn-icon-only svg {
  width: 14px;
  height: 14px;
  fill: currentColor;
}

.btn-icon-only:hover:not(:disabled) {
  color: var(--text);
  border-color: var(--border-hover);
  background: rgba(255, 255, 255, 0.03);
}

.btn-icon-only:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.skill-progress-wrap {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.skill-progress-bar-bg {
  height: 6px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 3px;
  overflow: hidden;
}

.skill-progress-bar-fg {
  height: 100%;
  background: var(--primary);
  border-radius: 3px;
  transition: width 0.3s;
}

.skill-progress-text {
  font-size: 0.72rem;
  color: var(--text-2);
  display: flex;
  justify-content: space-between;
}

.skill-stats-breakdown {
  font-size: 0.72rem;
  color: var(--text-3);
  display: flex;
  gap: 0.75rem;
}

/* Quota Inline Editor */
.quota-inline-edit-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  border-top: 1px solid var(--border);
  padding-top: 0.75rem;
  margin-top: 0.25rem;
}

.quota-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.78rem;
  color: var(--text-2);
}

.quota-input-mini {
  width: 55px;
  background: rgba(0, 0, 0, 0.4);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text);
  font-size: 0.75rem;
  padding: 0.2rem 0.4rem;
  text-align: center;
  outline: none;
  font-family: 'JetBrains Mono', monospace;
  transition: border-color 0.15s, background 0.15s;
}

.quota-input-mini:focus {
  border-color: var(--primary);
  background: rgba(0, 0, 0, 0.6);
}

.quota-input-mini::-webkit-inner-spin-button, 
.quota-input-mini::-webkit-outer-spin-button { 
  -webkit-appearance: none; 
  margin: 0; 
}

.quota-input-mini {
  -moz-appearance: textfield;
}
</style>
