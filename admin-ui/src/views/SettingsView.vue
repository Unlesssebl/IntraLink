<template>
  <section class="screen active">
    <!-- КАРТОЧКА 1: Мониторинг здоровья инфраструктуры -->
    <div class="card mb-3">
      <div class="card-header">
        <div>
          <div class="card-title">🖥️ Статус сервисов и интеграций</div>
          <div class="card-subtitle">Мониторинг подключения шлюза к IntraService API, Redis и воркерам</div>
        </div>
      </div>
      <div class="card-body">
        <div class="health-grid">
          <div class="health-card">
            <div class="health-head">
              <span class="health-title">IntraService API</span>
              <span class="status-indicator online"></span>
            </div>
            <div class="health-val">Подключено</div>
            <div class="health-sub">Фильтр #984 (Очередь 1-й линии)</div>
          </div>

          <div class="health-card">
            <div class="health-head">
              <span class="health-title">Redis State & Streams</span>
              <span class="status-indicator online"></span>
            </div>
            <div class="health-val">Активен</div>
            <div class="health-sub">stream:intraservice_events</div>
          </div>

          <div class="health-card">
            <div class="health-head">
              <span class="health-title">База знаний RAG</span>
              <span class="status-indicator online"></span>
            </div>
            <div class="health-val">pgvector Tier-1</div>
            <div class="health-sub">FastEmbed семантический поиск</div>
          </div>

          <div class="health-card">
            <div class="health-head">
              <span class="health-title">WinRM & SMB Доступ</span>
              <span class="status-indicator" :class="{ online: isDomainConfigured }"></span>
            </div>
            <div class="health-val">{{ isDomainConfigured ? 'Настроено' : 'Не задано' }}</div>
            <div class="health-sub">{{ domainUsername || 'Переменные окружения' }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- КАРТОЧКА 2: Доменная учетная запись (WinRM / SMB) -->
    <div class="card mb-3">
      <div class="card-header">
        <div>
          <div class="card-title">🔑 Доменная учетная запись (WinRM / SMB)</div>
          <div class="card-subtitle">Конфигурация учетных данных для удаленной установки принтеров на рабочие станции</div>
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
                <div class="password-input-wrap">
                  <input 
                    v-model="domainPassword" 
                    :type="showPassword ? 'text' : 'password'" 
                    id="f-domain-password" 
                    class="form-control"
                    placeholder="Введите новый пароль..." 
                    autocomplete="new-password" 
                    :disabled="savingDomain"
                  />
                  <button type="button" class="pwd-toggle-btn" @click="showPassword = !showPassword">
                    {{ showPassword ? '👁️' : '🔒' }}
                  </button>
                </div>
                <span class="form-hint">Пароль надежно шифруется Fernet и сохраняется в защищенном хранилище Redis.</span>
              </div>
            </div>
          </div>
          
          <button type="submit" class="btn btn-primary" :disabled="savingDomain || !domainUsername">
            <template v-if="savingDomain">
              <div class="spinner"></div> Сохранение...
            </template>
            <template v-else>
              <svg viewBox="0 0 24 24" width="16" height="16">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" stroke="currentColor" stroke-width="2" fill="none"></path>
                <polyline points="17 21 17 13 7 13 7 21" stroke="currentColor" stroke-width="2" fill="none"></polyline>
                <polyline points="7 3 7 8 15 8" stroke="currentColor" stroke-width="2" fill="none"></polyline>
              </svg>
              Сохранить учетные данные
            </template>
          </button>
        </form>
      </div>
    </div>

    <!-- КАРТОЧКА 3: Синхронизация индексов драйверов -->
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">🖨️ Индексация базы драйверов принтеров</div>
          <div class="card-subtitle">Обход SMB-шары, распаковка архивов и обновление справочника поддерживаемых моделей</div>
        </div>
      </div>
      <div class="card-body">
        <div class="indexer-actions-row">
          <button @click="triggerFastReindex" class="btn btn-primary" :disabled="rebuildingIndex">
            <template v-if="rebuildingIndex && fastReindexing">
              <div class="spinner"></div> Переиндексация...
            </template>
            <template v-else>
              <svg viewBox="0 0 24 24" width="16" height="16">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="currentColor"></polygon>
              </svg>
              Быстрая переиндексация (Секунды)
            </template>
          </button>

          <button @click="triggerRebuildIndex" class="btn btn-outline" :disabled="rebuildingIndex">
            <template v-if="rebuildingIndex && !fastReindexing">
              <div class="spinner"></div> Запуск полной синхронизации...
            </template>
            <template v-else>
              <svg viewBox="0 0 24 24" width="16" height="16">
                <polyline points="23 4 23 10 17 10" stroke="currentColor" stroke-width="2" fill="none"/>
                <polyline points="1 20 1 14 7 14" stroke="currentColor" stroke-width="2" fill="none"/>
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" stroke="currentColor" stroke-width="2" fill="none"/>
              </svg>
              Полная синхронизация SMB
            </template>
          </button>
        </div>

        <div class="indexer-status-card" style="margin-top: 1rem;">
          <template v-if="indexerStatus.is_running">
            <div class="indexer-running">
              <div class="spinner"></div>
              <span>{{ fastReindexing ? 'Быстрая переиндексация выполняется...' : 'Полная синхронизация... (это займет несколько минут)' }}</span>
            </div>
          </template>
          <template v-else-if="indexerStatus.last_run">
            <div v-if="indexerStatus.last_result?.status === 'error'" class="indexer-result error">
              <span>⚠️ Ошибка последней синхронизации ({{ formatLastRun(indexerStatus.last_run) }}): {{ indexerStatus.last_result.error }}</span>
            </div>
            <div v-else class="indexer-result success">
              <span>
                {{ indexerStatus.last_result?.mode === 'fast' ? '⚡ Быстрая переиндексация' : '✓ Синхронизация' }}
                завершена {{ formatLastRun(indexerStatus.last_run) }}
                <template v-if="indexerStatus.last_result">
                  — проиндексировано <strong>{{ indexerStatus.last_result.indexed }}</strong> моделей
                  ({{ indexerStatus.last_result.duration_sec }}с)
                </template>
              </span>
            </div>
          </template>
          <template v-else>
            <div class="indexer-hint">
              <b>Быстрая переиндексация</b> — сканирует каталог <code>extracted-drv-inf</code> за считанные секунды.<br>
              <b>Полная синхронизация</b> — обходит удаленную SMB-шару, копирует новые архивы и распаковывает драйверы.
            </div>
          </template>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, inject, onUnmounted } from 'vue';
import { apiFetch } from '../api';
import { useToastStore } from '../stores/toast';

const toastStore = useToastStore();

// Секция 1: WinRM
const domainUsername = ref('');
const domainPassword = ref('');
const showPassword = ref(false);
const isDomainConfigured = ref(false);
const domainStatusText = ref('Загрузка...');
const domainStatusColor = ref('var(--text-3)');
const savingDomain = ref(false);

const fetchDomainAuthStatus = async () => {
  try {
    const data = await apiFetch('/admin/api/domain-auth');
    isDomainConfigured.value = data.is_configured;
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

const saveDomainAuth = async () => {
  savingDomain.value = true;
  try {
    await apiFetch('/admin/api/domain-auth', {
      method: 'POST',
      body: JSON.stringify({
        username: domainUsername.value.trim(),
        password: domainPassword.value ? domainPassword.value : null
      })
    });
    
    toastStore.success('Учетная запись домена сохранена в Redis');
    domainPassword.value = '';
    await fetchDomainAuthStatus();
  } catch (err) {
    toastStore.error(err.message || 'Ошибка сохранения учетной записи');
  } finally {
    savingDomain.value = false;
  }
};

// Секция 2: Индексация драйверов
const rebuildingIndex = ref(false);
const fastReindexing = ref(false);
const indexerStatus = ref({ is_running: false, last_run: null });
let indexerPollInterval = null;

const checkIndexerStatus = async () => {
  try {
    const data = await apiFetch('/admin/api/printers/index-status');
    indexerStatus.value = data;
    rebuildingIndex.value = !!data.is_running;
  } catch (err) {
    console.error('Ошибка проверки статуса индексатора:', err);
  }
};

const formatLastRun = (ts) => {
  if (!ts) return '';
  const date = new Date(ts * 1000);
  return date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const triggerRebuildIndex = async () => {
  rebuildingIndex.value = true;
  fastReindexing.value = false;
  try {
    await apiFetch('/admin/api/printers/rebuild-index', { method: 'POST' });
    toastStore.info('Полная синхронизация SMB запущена');
    checkIndexerStatus();
  } catch (err) {
    rebuildingIndex.value = false;
    toastStore.error(err.message || 'Ошибка запуска синхронизации');
  }
};

const triggerFastReindex = async () => {
  rebuildingIndex.value = true;
  fastReindexing.value = true;
  try {
    await apiFetch('/admin/api/printers/fast-reindex', { method: 'POST' });
    toastStore.success('Быстрая переиндексация запущена');
    checkIndexerStatus();
  } catch (err) {
    rebuildingIndex.value = false;
    fastReindexing.value = false;
    toastStore.error(err.message || 'Ошибка запуска быстрой переиндексации');
  }
};

const registerRefresh = inject('registerRefresh');
let unregisterRefresh = null;

const refreshAll = () => {
  fetchDomainAuthStatus();
  checkIndexerStatus();
};

onMounted(() => {
  refreshAll();
  indexerPollInterval = setInterval(checkIndexerStatus, 5000);
  if (registerRefresh) {
    unregisterRefresh = registerRefresh(refreshAll);
  }
});

onUnmounted(() => {
  if (indexerPollInterval) clearInterval(indexerPollInterval);
  if (unregisterRefresh) unregisterRefresh();
});
</script>

<style scoped>
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1rem;
}

.health-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.health-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.health-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-2);
}
.health-val {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
}
.health-sub {
  font-size: 0.75rem;
  color: var(--text-3);
}

.password-input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.password-input-wrap .form-control {
  padding-right: 2.5rem;
}
.pwd-toggle-btn {
  position: absolute;
  right: 0.6rem;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.9rem;
}

.indexer-actions-row {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.indexer-status-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.85rem 1rem;
}

.indexer-running {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  color: var(--blue);
  font-size: 0.85rem;
}

.indexer-result {
  font-size: 0.85rem;
}
.indexer-result.success {
  color: var(--green);
}
.indexer-result.error {
  color: var(--red);
}

.indexer-hint {
  font-size: 0.8rem;
  color: var(--text-2);
  line-height: 1.5;
}
</style>
