<template>
  <section class="screen active">
    <!-- Метрики AI -->
    <StatsRow :items="statsItems" />

    <div class="ai-layout">
      <!-- Левая колонка: Форма настроек и Дерево услуг -->
      <div class="ai-sidebar">
        <form @submit.prevent="saveAIConfig" novalidate style="width: 100%; display: flex; flex-direction: column; gap: 1rem;">
          
          <!-- Режим автоответа -->
          <div class="form-group">
            <label class="form-label" for="f-ai-mode">Режим автоответа</label>
            <select v-model="aiMode" id="f-ai-mode" class="form-control" required :disabled="savingConfig">
              <option value="comment_only">Только комментарий (Безопасный)</option>
              <option value="comment_and_wait">Комментарий + статус "Требует уточнения"</option>
              <option value="comment_and_resolve">Комментарий + статус "Выполнена" (при высоком confidence)</option>
            </select>
          </div>

          <!-- Квоты RAG по умолчанию -->
          <div class="form-group">
            <label class="form-label">Квоты по умолчанию</label>
            <div style="display: flex; gap: 0.5rem;">
              <div style="flex: 1;">
                <span style="font-size: 0.7rem; color: var(--text-2);">Выполнена:</span>
                <input 
                  v-model.number="globalClosed" 
                  type="number" 
                  class="form-control" 
                  placeholder="Выполнена" 
                  title="Квота для статуса Выполнена" 
                  style="padding: 0.45rem; margin-top: 0.2rem;" 
                  :disabled="savingConfig"
                />
              </div>
              <div style="flex: 1;">
                <span style="font-size: 0.7rem; color: var(--text-2);">Отменена:</span>
                <input 
                  v-model.number="globalCancelled" 
                  type="number" 
                  class="form-control" 
                  placeholder="Отменена" 
                  title="Квота для статуса Отменена" 
                  style="padding: 0.45rem; margin-top: 0.2rem;" 
                  :disabled="savingConfig"
                />
              </div>
            </div>
          </div>

          <!-- Дерево разделов -->
          <div class="form-group">
            <label class="form-label">Разделы автоответа (AI)</label>
            <div v-if="cacheStore.servicesTreeLoading" class="services-tree-container">
              <p style="color:var(--text-3);font-size:0.85rem;">Загрузка дерева услуг...</p>
            </div>
            <div v-else-if="!servicesTree || servicesTree.length === 0" class="services-tree-container">
              <p style="color:var(--red);font-size:0.85rem;">Каталог услуг пуст</p>
            </div>
            <div v-else class="services-tree-container" style="max-height: 250px;">
              <ServicesTree 
                :nodes="servicesTree"
                v-model="aiServiceIds"
                :selected-id="selectedServiceId"
                prefix="ai"
                :show-progress="true"
                :progress-data="ragStats"
                :service-quotas="ragServiceQuotas"
                :global-quotas="ragGlobalQuotas"
                @select-node="handleSelectCategory"
              />
            </div>
            <span class="form-hint">
              Отметьте чекбоксы для активации автоответов ИИ. Нажмите на название раздела, чтобы управлять его RAG-примерами справа.
            </span>
          </div>

          <!-- Оповещение об успешности сохранения настроек -->
          <div v-if="configAlertMsg" class="alert" :class="`alert-${configAlertType}`" style="display: flex;">
            <svg v-if="configAlertType === 'success'" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
            <svg v-else viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
            {{ configAlertMsg }}
          </div>

          <!-- Кнопка сохранения конфигурации -->
          <button type="submit" class="btn btn-primary" id="btn-save-ai-config" style="width: 100%; justify-content: center;" :disabled="savingConfig">
            <template v-if="savingConfig">
              <div class="spinner"></div> Сохранение...
            </template>
            <template v-else>
              Сохранить конфиг AI
            </template>
          </button>
        </form>
      </div>

      <!-- Правая колонка: Карточки навыков, Лог, Библиотека примеров, Тестирование -->
      <div class="ai-main-content">
        
        <!-- Карточки навыков для выбранного раздела -->
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title" id="selected-category-title">
                Карточки навыков: {{ selectedServiceName || 'Выберите категорию в дереве' }}
              </div>
              <div class="card-subtitle">Управление лимитами сбора и запуск обучения для конечных услуг</div>
            </div>
          </div>
          <div class="card-body">
            <div v-if="!selectedServiceId" style="color:var(--text-3);font-size:0.85rem;">
              Нажмите на название папки/услуги в дереве слева, чтобы открыть её навыки.
            </div>
            <div v-else-if="skillLeaves.length === 0" style="color:var(--text-3);font-size:0.85rem;">
              В этом разделе нет конечных услуг (листьев).
            </div>
            <div v-else class="skill-grid">
              <div v-for="leaf in skillLeaves" :key="leaf.id" class="skill-card">
                <div class="skill-card-header">
                  <span class="skill-name">{{ leaf.name }}</span>
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
                
                <div style="font-size:0.75rem; color:var(--text-3); display:flex; flex-direction:column; gap:0.2rem;">
                  <span>Выполнена: {{ getLeafStatusCount(leaf.id, 'Закрыта') }} / {{ getLeafQuota(leaf.id, 28) }}</span>
                  <span>Отменена: {{ getLeafStatusCount(leaf.id, 'Отменена') }} / {{ getLeafQuota(leaf.id, 30) }}</span>
                </div>
                
                <div class="skill-actions">
                  <button class="btn btn-outline btn-sm" @click="openQuotaSettings(leaf)">Лимиты</button>
                  <button class="btn btn-primary btn-sm" @click="triggerRAGBuild([leaf.id])">Обучить</button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Кнопки глобального запуска RAG и Лог сборщика -->
        <div class="card">
          <div class="card-header" style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div class="card-title">Синхронизация базы знаний RAG (pgvector)</div>
              <div class="card-subtitle">Загрузка примеров до наполнения квот по всем активным разделам</div>
            </div>
            <button class="btn btn-outline btn-sm" id="btn-rag-build" @click="triggerRAGBuild(null)" style="margin-left: auto;" :disabled="ragRunning">
              {{ ragRunning ? 'Выполняется сбор...' : 'Запустить полный сбор RAG' }}
            </button>
          </div>
          <div class="card-body" style="padding: 0 1.5rem 1.5rem;">
            <!-- Встроенный RAG Terminal -->
            <Terminal 
              v-if="ragTerminalActive"
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
          
          <!-- Пагинация с показом "Страница X из Y" (UX Фикс №9) -->
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

      </div>
    </div>

    <!-- Модалка лимитов -->
    <QuotaModal 
      :is-open="quotaModalOpen"
      :service-id="quotaModalServiceId"
      :service-name="quotaModalServiceName"
      :initial-closed="quotaModalInitialClosed"
      :initial-cancelled="quotaModalInitialCancelled"
      @close="closeQuotaSettings"
      @save="saveIndividualQuota"
    />
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, inject } from 'vue';
import { apiFetch } from '../api';
import { useCacheStore } from '../stores/cache';
import StatsRow from '../components/StatsRow.vue';
import ServicesTree from '../components/ServicesTree.vue';
import Terminal from '../components/Terminal.vue';
import QuotaModal from '../components/QuotaModal.vue';
import { usePolling } from '../composables/usePolling';

const cacheStore = useCacheStore();

// Метрики
const aiMetrics = ref({ classifications: 0, replied: 0, redirected: 0 });

const statsItems = computed(() => [
  { value: aiMetrics.value.classifications, label: 'Классификаций (с запуска)', color: 'blue', icon: 'info' },
  { value: aiMetrics.value.replied, label: 'Автоответов отправлено (с запуска)', color: 'green', icon: 'check' },
  { value: aiMetrics.value.redirected, label: 'Перенаправлено в другой раздел (с запуска)', color: 'red', icon: 'redirect' } // UX Фиксы №5 и №6
]);

// Конфигурация AI
const aiMode = ref('comment_only');
const aiServiceIds = ref([]);
const globalClosed = ref(10);
const globalCancelled = ref(5);
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

// SSE Лог RAG
const ragTerminalActive = ref(false);
const ragLogs = ref([]);
let ragSse = null;

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

// Модалка квот
const quotaModalOpen = ref(false);
const quotaModalServiceId = ref(0);
const quotaModalServiceName = ref('');
const quotaModalInitialClosed = ref(10);
const quotaModalInitialCancelled = ref(5);

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
      
      globalClosed.value = ragGlobalQuotas.value["28"] || 10;
      globalCancelled.value = ragGlobalQuotas.value["30"] || 5;
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

// Сохранение общей формы конфигурации AI (Включая квоты по умолчанию)
const saveAIConfig = async () => {
  savingConfig.value = true;
  configAlertMsg.value = '';
  
  try {
    // Отправляем две конфигурации параллельно (UX фикс №3)
    await Promise.all([
      // 1. AI-конфиг (режим и чекбоксы разделов)
      apiFetch('/admin/api/ai-worker/config', {
        method: 'POST',
        body: JSON.stringify({
          auto_reply_service_ids: aiServiceIds.value,
          auto_reply_mode: aiMode.value
        })
      }),
      // 2. Квоты RAG
      apiFetch('/admin/api/ai-worker/rag/quotas', {
        method: 'POST',
        body: JSON.stringify({
          filter_id: ragFilterId.value,
          global_quotas: { "28": globalClosed.value, "30": globalCancelled.value },
          service_quotas: ragServiceQuotas.value
        })
      })
    ]);
    
    configAlertType.value = 'success';
    configAlertMsg.value = 'Конфигурация AI и лимиты успешно сохранены';
    await fetchAIStatus();
    await fetchRAGQuotasAndStats();
  } catch (err) {
    configAlertType.value = 'error';
    configAlertMsg.value = err.message || 'Ошибка сохранения конфигурации';
  } finally {
    savingConfig.value = false;
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
  ragTerminalActive.value = true;
  ragLogs.value = ['[SYSTEM] Подключение к логам RAG...'];
  
  if (ragSse) {
    ragSse.close();
  }
  
  ragSse = new EventSource('/admin/api/ai-worker/rag/logs');
  let firstMsg = true;
  
  ragSse.onmessage = (e) => {
    if (firstMsg) {
      ragLogs.value = [];
      firstMsg = false;
    }
    ragLogs.value.push(e.data);
  };
  
  ragSse.onerror = () => {
    ragLogs.value.push('[SYSTEM] Подключение к логам RAG завершено.');
    if (ragSse) {
      ragSse.close();
      ragSse = null;
    }
    ragRunning.value = false;
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

// Открытие модалки лимитов для карточки навыка
const openQuotaSettings = (leaf) => {
  quotaModalServiceId.value = leaf.id;
  quotaModalServiceName.value = leaf.name;
  quotaModalInitialClosed.value = getLeafQuota(leaf.id, 28);
  quotaModalInitialCancelled.value = getLeafQuota(leaf.id, 30);
  quotaModalOpen.value = true;
};

const closeQuotaSettings = () => {
  quotaModalOpen.value = false;
};

const saveIndividualQuota = async ({ serviceId, closed, cancelled }) => {
  ragServiceQuotas.value[serviceId] = { "28": closed, "30": cancelled };
  
  try {
    await apiFetch('/admin/api/ai-worker/rag/quotas', {
      method: 'POST',
      body: JSON.stringify({
        filter_id: ragFilterId.value,
        global_quotas: ragGlobalQuotas.value,
        service_quotas: ragServiceQuotas.value
      })
    });
    closeQuotaSettings();
    await fetchRAGQuotasAndStats();
  } catch (err) {
    alert('Ошибка при сохранении лимитов: ' + err.message);
  }
};

// Регистрация обновления в Topbar
const registerRefresh = inject('registerRefresh');
let unregisterRefresh = null;

const refreshAll = () => {
  fetchAIStatus();
  fetchRAGQuotasAndStats();
  fetchExamples();
};

let polling = null;

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
  gap: 1.5rem;
  margin-top: 1.5rem;
  align-items: flex-start;
}
.ai-sidebar {
  width: 320px;
  flex-shrink: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}
.ai-main-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}
</style>
