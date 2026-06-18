<template>
  <section class="screen active">
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Ручная установка принтера</div>
          <div class="card-subtitle">
            Заполните все поля и нажмите «Запустить» — воркер выполнит установку автоматически
          </div>
        </div>
      </div>
      <div class="card-body">
        <form @submit.prevent="handleInstallSubmit" novalidate>
          <!-- Целевой компьютер -->
          <div class="form-section">
            <div class="form-section-title">1. Целевой компьютер</div>
            <div class="form-grid-2">
              <div class="form-group">
                <label class="form-label" for="f-target-pc">Имя компьютера</label>
                <input 
                  v-model="targetPc" 
                  type="text" 
                  id="f-target-pc" 
                  class="form-control"
                  placeholder="Например: WS-102 или PC-IVANOV" 
                  required 
                  autocomplete="off" 
                  :disabled="running"
                />
                <span class="form-hint">Сетевое имя ПК, на который нужно установить принтер.</span>
              </div>
            </div>
          </div>

          <!-- Модель принтера -->
          <div class="form-section">
            <div class="form-section-title">2. Модель принтера</div>
            <div class="form-grid-2">
              <div class="form-group">
                <label class="form-label" for="f-model">Принтер</label>
                <select v-model="modelKey" id="f-model" class="form-control" required :disabled="running || cacheStore.kbLoading">
                  <option value="">— Выберите модель —</option>
                  <option 
                    v-for="p in printers" 
                    :key="p.model_key" 
                    :value="p.model_key"
                  >
                    {{ p.display_name }} ({{ p.vendor }})
                  </option>
                </select>
                <span class="form-hint">Выберите модель из базы поддерживаемых принтеров.</span>
              </div>
              
              <div class="form-group">
                <label class="form-label" for="f-conn-type">Тип подключения</label>
                <select v-model="connectionType" id="f-conn-type" class="form-control" required :disabled="running">
                  <option value="tcpip">Сетевой — TCP/IP</option>
                  <option value="usb">Локальный — USB</option>
                </select>
                <span class="form-hint">Как принтер подключён к компьютеру — по сети или через USB-кабель.</span>
              </div>
            </div>
          </div>

          <!-- Сетевой адрес (только если tcpip) -->
          <div v-show="connectionType === 'tcpip'" class="form-section" id="addr-section">
            <div class="form-section-title">3. Сетевой адрес</div>
            <div class="form-grid-2">
              <div class="form-group">
                <label class="form-label" for="f-addr">IP-адрес или DNS-имя принтера</label>
                <input 
                  v-model="printerAddress" 
                  type="text" 
                  id="f-addr" 
                  class="form-control"
                  placeholder="Например: 192.168.1.100 или printer-hp.corp"
                  autocomplete="off" 
                  :disabled="running"
                />
                <span class="form-hint">Укажите адрес сетевого принтера. Обязательно для TCP/IP-подключения.</span>
              </div>
            </div>
          </div>

          <!-- Алерт об ошибках/успехе -->
          <div v-if="alertMsg" class="alert" :class="`alert-${alertType}`" style="display: flex;">
            <svg v-if="alertType === 'error'" viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            <svg v-else-if="alertType === 'success'" viewBox="0 0 24 24">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            {{ alertMsg }}
          </div>

          <button type="submit" class="btn btn-primary" :disabled="running">
            <template v-if="running">
              <div class="spinner"></div> Запуск...
            </template>
            <template v-else>
              <svg viewBox="0 0 24 24">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              Запустить установку
            </template>
          </button>
        </form>
      </div>
    </div>

    <!-- Встроенный терминал -->
    <Terminal 
      v-if="terminalActive"
      :title="terminalTitle" 
      :logs="terminalLogs" 
      @clear="clearTerminalLogs"
    />
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { apiFetch } from '../api';
import { useCacheStore } from '../stores/cache';
import Terminal from '../components/Terminal.vue';

const cacheStore = useCacheStore();

const targetPc = ref('');
const modelKey = ref('');
const connectionType = ref('tcpip');
const printerAddress = ref('');

const running = ref(false);
const alertMsg = ref('');
const alertType = ref('info');

const terminalActive = ref(false);
const terminalTitle = ref('Ожидание задачи...');
const terminalLogs = ref([]);

let logSse = null;

const printers = computed(() => {
  return cacheStore.kbData?.printers || [];
});

const clearTerminalLogs = () => {
  terminalLogs.value = [];
};

const showInstallAlert = (type, msg) => {
  alertType.value = type;
  alertMsg.value = msg;
};

// Запуск установки
const handleInstallSubmit = async () => {
  alertMsg.value = '';
  
  const target = targetPc.value.trim();
  const model = modelKey.value;
  const connType = connectionType.value;
  const addr = printerAddress.value.trim();
  
  if (!target) return showInstallAlert('error', 'Введите имя компьютера.');
  if (!model) return showInstallAlert('error', 'Выберите модель принтера.');
  if (connType === 'tcpip' && !addr) return showInstallAlert('error', 'Укажите IP-адрес или DNS-имя принтера.');
  
  running.value = true;
  terminalActive.value = false;
  clearTerminalLogs();
  
  if (logSse) {
    logSse.close();
    logSse = null;
  }

  try {
    const res = await apiFetch('/admin/api/print-jobs', {
      method: 'POST',
      body: JSON.stringify({
        target_pc: target,
        model_key: model,
        connection_type: connType,
        printer_address: connType === 'tcpip' ? addr : null
      })
    });
    
    showInstallAlert('success', `Задача #${res.task_id} запущена. Следите за логами ниже.`);
    startTerminalLogs(res.task_id);
  } catch (err) {
    showInstallAlert('error', err.message || 'Ошибка соединения с сервером.');
  } finally {
    running.value = false;
  }
};

const startTerminalLogs = (jobId) => {
  terminalActive.value = true;
  terminalTitle.value = `Задача #${jobId} — Лог установки`;
  terminalLogs.value = ['[SYSTEM] Подключение к потоку логов... ожидание воркера.'];
  
  if (logSse) {
    logSse.close();
  }
  
  logSse = new EventSource(`/admin/api/print-jobs/${jobId}/logs`);
  let firstMsg = true;
  
  logSse.onmessage = (e) => {
    if (firstMsg) {
      terminalLogs.value = [];
      firstMsg = false;
    }
    terminalLogs.value.push(e.data);
  };
  
  logSse.onerror = () => {
    terminalLogs.value.push('[SYSTEM] Поток логов завершён.');
    if (logSse) {
      logSse.close();
      logSse = null;
    }
  };
};

onMounted(async () => {
  try {
    await cacheStore.fetchKnowledgeBase();
  } catch (err) {
    showInstallAlert('error', 'Ошибка загрузки поддерживаемых моделей принтеров.');
  }
});

onUnmounted(() => {
  if (logSse) {
    logSse.close();
  }
});
</script>
