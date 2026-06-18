<template>
  <section class="screen active">
    <!-- КАРТОЧКА 1: Доменная учетная запись (WinRM / SMB) -->
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Доменная учетная запись (WinRM / SMB)</div>
          <div class="card-subtitle">Конфигурация учетных данных для удаленной установки принтеров</div>
        </div>
      </div>
      <div class="card-body">
        <form @submit.prevent="saveDomainAuth" novalidate>
          <div class="form-section">
            <div class="form-grid-2">
              <div class="form-group">
                <label class="form-label" for="f-domain-username">Имя пользователя</label>
                <input 
                  v-model="domainUsername" 
                  type="text" 
                  id="f-domain-username" 
                  class="form-control"
                  placeholder="Например: DOMAIN\administrator" 
                  required 
                  autocomplete="off" 
                  :disabled="savingDomain"
                />
                <span class="form-hint" :style="{ color: domainStatusColor }">
                  {{ domainStatusText }}
                </span>
              </div>
              <div class="form-group">
                <label class="form-label" for="f-domain-password">Пароль</label>
                <input 
                  v-model="domainPassword" 
                  type="password" 
                  id="f-domain-password" 
                  class="form-control"
                  placeholder="Введите пароль..." 
                  required 
                  autocomplete="new-password" 
                  :disabled="savingDomain"
                />
                <span class="form-hint">Пароль будет зашифрован и сохранен в Redis.</span>
              </div>
            </div>
          </div>
          
          <div v-if="domainAlertMsg" class="alert" :class="`alert-${domainAlertType}`" style="display: flex;">
            <svg v-if="domainAlertType === 'success'" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
            <svg v-else viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
            {{ domainAlertMsg }}
          </div>
          
          <button type="submit" class="btn btn-primary" :disabled="savingDomain || !domainUsername || !domainPassword">
            <template v-if="savingDomain">
              <div class="spinner"></div> Сохранение...
            </template>
            <template v-else>
              <svg viewBox="0 0 24 24">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path>
                <polyline points="17 21 17 13 7 13 7 21"></polyline>
                <polyline points="7 3 7 8 15 8"></polyline>
              </svg>
              Сохранить учетные данные
            </template>
          </button>
        </form>
      </div>
    </div>

    <!-- КАРТОЧКА 2: Системные настройки (RAG и Printer Worker) -->
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Системные настройки</div>
          <div class="card-subtitle">Общая конфигурация фильтрации RAG и разделов обслуживания</div>
        </div>
      </div>
      <div class="card-body">
        <form @submit.prevent="saveSystemConfig" novalidate>
          
          <!-- Раздел RAG -->
          <div class="form-section">
            <div class="form-section-title">Настройки RAG</div>
            <div class="form-group">
              <label class="form-label" for="f-rag-filter-id">ID фильтра "1 линия"</label>
              <input 
                v-model.number="ragFilterId" 
                type="number" 
                id="f-rag-filter-id" 
                class="form-control" 
                placeholder="ID фильтра..." 
                required 
                :disabled="savingSystem"
              />
              <span class="form-hint">ID системного фильтра в IntraService для поиска заявок первой линии.</span>
            </div>
          </div>
          
          <!-- Раздел Printer Worker -->
          <div class="form-section" style="margin-top: 1.5rem;">
            <div class="form-section-title">Настройки Printer Worker</div>
            <div class="form-group">
              <label class="form-label">Разделы принтеров для диспетчера</label>
              <div v-if="cacheStore.servicesTreeLoading" class="services-tree-container">
                <p style="color:var(--text-3);font-size:0.85rem;">Загрузка дерева услуг...</p>
              </div>
              <div v-else-if="!servicesTree || servicesTree.length === 0" class="services-tree-container">
                <p style="color:var(--red);font-size:0.85rem;">Каталог услуг пуст</p>
              </div>
              <div v-else class="services-tree-container" style="max-height: 250px;">
                <ServicesTree 
                  :nodes="servicesTree"
                  v-model="printerServiceIds"
                  prefix="printer"
                  :show-progress="false"
                />
              </div>
              <span class="form-hint">Эти разделы обрабатываются printer-worker'ом для автоматической установки принтеров.</span>
            </div>
          </div>

          <div v-if="systemAlertMsg" class="alert" :class="`alert-${systemAlertType}`" style="display: flex;">
            <svg v-if="systemAlertType === 'success'" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
            <svg v-else viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
            {{ systemAlertMsg }}
          </div>
          
          <button type="submit" class="btn btn-primary" :disabled="savingSystem">
            <template v-if="savingSystem">
              <div class="spinner"></div> Сохранение...
            </template>
            <template v-else>
              <svg viewBox="0 0 24 24">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path>
                <polyline points="17 21 17 13 7 13 7 21"></polyline>
                <polyline points="7 3 7 8 15 8"></polyline>
              </svg>
              Сохранить системные настройки
            </template>
          </button>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, computed, inject, onUnmounted } from 'vue';
import { apiFetch } from '../api';
import { useCacheStore } from '../stores/cache';
import ServicesTree from '../components/ServicesTree.vue';

const cacheStore = useCacheStore();

// Секция 1: WinRM
const domainUsername = ref('');
const domainPassword = ref('');
const domainStatusText = ref('Загрузка...');
const domainStatusColor = ref('var(--text-3)');
const savingDomain = ref(false);
const domainAlertMsg = ref('');
const domainAlertType = ref('success');

// Секция 2: RAG + Printer
const ragFilterId = ref(null);
const printerServiceIds = ref([]);
const savingSystem = ref(false);
const systemAlertMsg = ref('');
const systemAlertType = ref('success');

const servicesTree = computed(() => cacheStore.servicesTree);

// Загрузка доменных учетных данных
const fetchDomainAuthStatus = async () => {
  try {
    const data = await apiFetch('/admin/api/domain-auth');
    if (data.is_configured) {
      domainUsername.value = data.username || '';
      domainStatusText.value = `Текущая учетная запись: ${data.username}`;
      domainStatusColor.value = 'var(--green)';
    } else {
      domainUsername.value = '';
      domainStatusText.value = 'Учетная запись не настроена (используются переменные окружения)';
      domainStatusColor.value = 'var(--yellow)';
    }
  } catch (err) {
    domainStatusText.value = 'Ошибка загрузки статуса учетной записи';
    domainStatusColor.value = 'var(--red)';
  }
};

// Загрузка системного конфига
const fetchSystemConfig = async () => {
  try {
    const data = await apiFetch('/admin/api/system-config');
    ragFilterId.value = data.rag_filter_id || '';
    printerServiceIds.value = data.printer_service_ids || [];
  } catch (err) {
    console.error('Ошибка загрузки системного конфига:', err);
  }
};

// Сохранение учетных данных
const saveDomainAuth = async () => {
  savingDomain.value = true;
  domainAlertMsg.value = '';
  
  try {
    await apiFetch('/admin/api/domain-auth', {
      method: 'POST',
      body: JSON.stringify({
        username: domainUsername.value.trim(),
        password: domainPassword.value
      })
    });
    
    domainAlertType.value = 'success';
    domainAlertMsg.value = 'Учетная запись домена успешно сохранена';
    domainPassword.value = '';
    await fetchDomainAuthStatus();
  } catch (err) {
    domainAlertType.value = 'error';
    domainAlertMsg.value = err.message || 'Ошибка сохранения учетной записи';
  } finally {
    savingDomain.value = false;
  }
};

// Сохранение системного конфига
const saveSystemConfig = async () => {
  savingSystem.value = true;
  systemAlertMsg.value = '';
  
  try {
    await apiFetch('/admin/api/system-config', {
      method: 'POST',
      body: JSON.stringify({
        printer_service_ids: printerServiceIds.value,
        rag_filter_id: parseInt(ragFilterId.value) || 0
      })
    });
    
    systemAlertType.value = 'success';
    systemAlertMsg.value = 'Системные настройки успешно сохранены';
    await fetchSystemConfig();
  } catch (err) {
    systemAlertType.value = 'error';
    systemAlertMsg.value = err.message || 'Ошибка сохранения настроек';
  } finally {
    savingSystem.value = false;
  }
};

// Регистрация обновления в Topbar
const registerRefresh = inject('registerRefresh');
let unregisterRefresh = null;

const refreshAll = () => {
  fetchDomainAuthStatus();
  fetchSystemConfig();
};

onMounted(async () => {
  // Загружаем дерево услуг, если оно еще не загружено
  try {
    await cacheStore.fetchServicesTree();
  } catch (e) {
    console.error(e);
  }
  
  refreshAll();
  
  if (registerRefresh) {
    unregisterRefresh = registerRefresh(refreshAll);
  }
});

onUnmounted(() => {
  if (unregisterRefresh) unregisterRefresh();
});
</script>
