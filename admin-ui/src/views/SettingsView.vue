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
                <label class="form-label" for="f-domain-password">Пароль (необязательно)</label>
                <input 
                  v-model="domainPassword" 
                  type="password" 
                  id="f-domain-password" 
                  class="form-control"
                  placeholder="Введите новый пароль (оставьте пустым, если не хотите менять)..." 
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
          
          <button type="submit" class="btn btn-primary" :disabled="savingDomain || !domainUsername">
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

    <!-- КАРТОЧКА 2: Синхронизация индексов драйверов -->
    <div class="card" style="margin-top: 24px;">
      <div class="card-header">
        <div>
          <div class="card-title">Индексация драйверов</div>
          <div class="card-subtitle">Запуск обхода SMB-шары, распаковки архивов и обновления базы поддерживаемых моделей принтеров</div>
        </div>
      </div>
      <div class="card-body">
        <div v-if="indexAlertMsg" class="alert" :class="`alert-${indexAlertType}`" style="display: flex; margin-bottom: 16px;">
          <svg v-if="indexAlertType === 'success'" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
          <svg v-else viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
          {{ indexAlertMsg }}
        </div>
        <button @click="triggerRebuildIndex" class="btn btn-primary" :disabled="rebuildingIndex">
          <template v-if="rebuildingIndex">
            <div class="spinner"></div> Запуск...
          </template>
          <template v-else>
            <svg viewBox="0 0 24 24">
              <polyline points="23 4 23 10 17 10"></polyline>
              <polyline points="1 20 1 14 7 14"></polyline>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
            </svg>
            Синхронизировать драйверы
          </template>
        </button>
        <div class="form-hint" style="margin-top: 12px;">
          Процесс выполняется в фоновом режиме на сервере воркера. Это может занять несколько минут.
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, inject, onUnmounted } from 'vue';
import { apiFetch } from '../api';

// Секция 1: WinRM
const domainUsername = ref('');
const domainPassword = ref('');
const domainStatusText = ref('Загрузка...');
const domainStatusColor = ref('var(--text-3)');
const savingDomain = ref(false);
const domainAlertMsg = ref('');
const domainAlertType = ref('success');

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

// Сохранение учетных данных
const saveDomainAuth = async () => {
  savingDomain.value = true;
  domainAlertMsg.value = '';
  
  try {
    await apiFetch('/admin/api/domain-auth', {
      method: 'POST',
      body: JSON.stringify({
        username: domainUsername.value.trim(),
        password: domainPassword.value ? domainPassword.value : null
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

// Секция 2: Синхронизация индексов
const rebuildingIndex = ref(false);
const indexAlertMsg = ref('');
const indexAlertType = ref('success');

const triggerRebuildIndex = async () => {
  rebuildingIndex.value = true;
  indexAlertMsg.value = '';
  
  try {
    const res = await apiFetch('/admin/api/printers/rebuild-index', {
      method: 'POST'
    });
    indexAlertType.value = 'success';
    indexAlertMsg.value = res.message || 'Процесс запущен';
  } catch (err) {
    indexAlertType.value = 'error';
    indexAlertMsg.value = err.message || 'Ошибка запуска синхронизации';
  } finally {
    rebuildingIndex.value = false;
  }
};

// Регистрация обновления в Topbar
const registerRefresh = inject('registerRefresh');
let unregisterRefresh = null;

const refreshAll = () => {
  fetchDomainAuthStatus();
};

onMounted(() => {
  refreshAll();
  
  if (registerRefresh) {
    unregisterRefresh = registerRefresh(refreshAll);
  }
});

onUnmounted(() => {
  if (unregisterRefresh) unregisterRefresh();
});
</script>

