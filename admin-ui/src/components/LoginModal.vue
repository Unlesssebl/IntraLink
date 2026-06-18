<template>
  <div v-if="!authStore.isLoggedIn" class="overlay open">
    <div class="modal">
      <div class="modal-logo">
        <div class="logo-icon">
          <svg viewBox="0 0 24 24">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </div>
        <span class="modal-title">IntraLink Admin</span>
      </div>
      <p class="modal-desc">
        Для входа в панель администратора используйте ваши учетные данные от IntraService.
      </p>
      
      <div class="form-group">
        <label class="form-label" for="username-input">Логин</label>
        <input 
          v-model="username" 
          @keydown.enter="handleLogin"
          type="text" 
          id="username-input" 
          class="form-control" 
          placeholder="Введите логин..." 
          required 
          autocomplete="username" 
          :disabled="loading"
        />
      </div>
      
      <div class="form-group">
        <label class="form-label" for="password-input">Пароль</label>
        <input 
          v-model="password" 
          @keydown.enter="handleLogin"
          type="password" 
          id="password-input" 
          class="form-control" 
          placeholder="Введите пароль..." 
          required 
          autocomplete="current-password" 
          :disabled="loading"
        />
      </div>

      <div v-if="errorMsg" class="alert alert-error" style="display: flex;">
        <svg viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
        {{ errorMsg }}
      </div>

      <button 
        @click="handleLogin" 
        :disabled="loading || !username || !password" 
        class="btn btn-primary" 
        style="width:100%"
      >
        <template v-if="loading">
          <div class="spinner"></div> Вход...
        </template>
        <template v-else>
          Войти
        </template>
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useAuthStore } from '../stores/auth';

const authStore = useAuthStore();
const username = ref('');
const password = ref('');
const loading = ref(false);
const errorMsg = ref('');

const handleLogin = async () => {
  if (!username.value.trim() || !password.value || loading.value) return;
  
  loading.value = true;
  errorMsg.value = '';
  
  try {
    await authStore.login(username.value.trim(), password.value);
    // После успешного входа сбрасываем поля
    username.value = '';
    password.value = '';
  } catch (err) {
    errorMsg.value = err.message || 'Ошибка сети при авторизации';
  } finally {
    loading.value = false;
  }
};
</script>
