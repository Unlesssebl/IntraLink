<template>
  <div class="queue-dashboard">
    <!-- 1. Аналитические мини-виджеты очереди -->
    <QueueAnalyticsWidgets 
      :tasks="queueStore.tasks" 
      :host-status-map="queueStore.hostStatusMap"
      @filter-confidence="handleConfidenceFilter"
    />

    <!-- 2. Двухстрочный Linear Toolbar -->
    <div class="linear-toolbar">
      <!-- 2.1. Верхняя строка: Заголовок, переключатель потоков, поиск, переключатель вида, кнопка обновления -->
      <div class="toolbar-main-row">
        <!-- Заголовок и счетчик -->
        <div class="toolbar-brand">
          <div class="toolbar-title">
            <span>Очередь 1-й линии</span>
            <span class="count-badge">{{ queueStore.filteredTasks.length }}</span>
          </div>
          <span class="toolbar-sub">Фильтр #{{ queueStore.filterId }}</span>
        </div>

        <!-- Сегментированный переключатель потоков обработки (Linear Segment Control) -->
        <div class="stream-segment-control">
          <button 
            class="segment-btn" 
            :class="{ active: queueStore.aiSolutionFilter === 'all' }"
            @click="queueStore.aiSolutionFilter = 'all'"
          >
            <span>Все</span>
            <span class="segment-badge">{{ queueStore.aiStats.total }}</span>
          </button>

          <button 
            class="segment-btn ready" 
            :class="{ active: queueStore.aiSolutionFilter === 'ready' }"
            @click="queueStore.aiSolutionFilter = 'ready'"
          >
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            <span>Готов ответ AI</span>
            <span class="segment-badge ready">{{ queueStore.aiStats.ready }}</span>
          </button>

          <button 
            class="segment-btn manual" 
            :class="{ active: queueStore.aiSolutionFilter === 'manual' }"
            @click="queueStore.aiSolutionFilter = 'manual'"
          >
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            <span>Требуют решения</span>
            <span class="segment-badge manual">{{ queueStore.aiStats.manual }}</span>
          </button>
        </div>

        <!-- Поиск, Вид и Обновить -->
        <div class="toolbar-controls-right">
          <!-- Поисковая строка -->
          <div class="search-input-wrap">
            <svg viewBox="0 0 24 24" width="14" height="14" class="search-svg">
              <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" fill="none"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
            <input 
              v-model="queueStore.searchQuery" 
              type="text" 
              class="search-input" 
              placeholder="Поиск (#ID, ПК, заявитель)..."
            />
            <button v-if="queueStore.searchQuery" class="clear-btn" @click="queueStore.searchQuery = ''">
              <svg viewBox="0 0 24 24" width="12" height="12"><line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2"/><line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2"/></svg>
            </button>
          </div>

          <!-- Переключатель вида (Карточки / Таблица) -->
          <div class="view-switch-group">
            <button 
              class="view-btn" 
              :class="{ active: queueStore.viewMode === 'cards' }" 
              title="Вид карточек Linear"
              @click="queueStore.viewMode = 'cards'"
            >
              <svg viewBox="0 0 24 24" width="14" height="14">
                <rect x="3" y="3" width="7" height="7" stroke="currentColor" stroke-width="2" fill="none"/>
                <rect x="14" y="3" width="7" height="7" stroke="currentColor" stroke-width="2" fill="none"/>
                <rect x="3" y="14" width="7" height="7" stroke="currentColor" stroke-width="2" fill="none"/>
                <rect x="14" y="14" width="7" height="7" stroke="currentColor" stroke-width="2" fill="none"/>
              </svg>
            </button>
            <button 
              class="view-btn" 
              :class="{ active: queueStore.viewMode === 'grid' }" 
              title="Инженерная таблица"
              @click="queueStore.viewMode = 'grid'"
            >
              <svg viewBox="0 0 24 24" width="14" height="14">
                <line x1="3" y1="6" x2="21" y2="6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="3" y1="12" x2="21" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="3" y1="18" x2="21" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </button>
          </div>

          <!-- Кнопка обновления -->
          <button class="btn btn-primary btn-sm refresh-btn" :disabled="queueStore.loading" @click="queueStore.fetchQueue(false)">
            <svg v-if="queueStore.loading" class="spin" viewBox="0 0 24 24" width="13" height="13">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" fill="none" opacity="0.3"/>
              <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-width="3" fill="none"/>
            </svg>
            <svg v-else viewBox="0 0 24 24" width="13" height="13">
              <polyline points="23 4 23 10 17 10" stroke="currentColor" stroke-width="2" fill="none"/>
              <polyline points="1 20 1 14 7 14" stroke="currentColor" stroke-width="2" fill="none"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" stroke="currentColor" stroke-width="2" fill="none"/>
            </svg>
            <span>Обновить</span>
          </button>
        </div>
      </div>

      <!-- 2.2. Нижняя строка: Дропдаун сервиса + Быстрые фильтры + Пакетные действия -->
      <div class="toolbar-filter-row">
        <div class="filters-left">
          <!-- Выпадающий список сервисов -->
          <div class="service-select-wrap">
            <select 
              :value="queueStore.activeServiceTab" 
              class="linear-select service-select"
              @change="(e: any) => queueStore.selectServiceTab(e.target.value)"
            >
              <option value="all">📁 Все сервисы ({{ queueStore.tasks.length }})</option>
              <option v-for="s in queueStore.serviceTabs" :key="s.key" :value="s.key">
                {{ s.name }} ({{ s.count }})
              </option>
            </select>
          </div>

          <!-- Подсервисы (если есть) -->
          <div v-if="queueStore.activeServiceTab !== 'all' && queueStore.subServiceTabs.length > 0" class="subservice-select-wrap">
            <select 
              v-model="queueStore.activeSubServiceTab" 
              class="linear-select subservice-select"
            >
              <option value="all">Все подсервисы</option>
              <option v-for="sub in queueStore.subServiceTabs" :key="sub.key" :value="sub.key">
                {{ sub.name }} ({{ sub.count }})
              </option>
            </select>
          </div>

          <div class="filter-divider"></div>

          <!-- Фильтр оценки -->
          <div class="filter-chip-group">
            <span class="group-label">Оценка:</span>
            <button 
              class="filter-chip" 
              :class="{ active: queueStore.confidenceFilter === 'all' }"
              @click="queueStore.confidenceFilter = 'all'"
            >
              Все
            </button>
            <button 
              class="filter-chip green" 
              :class="{ active: queueStore.confidenceFilter === 'high' }"
              @click="queueStore.confidenceFilter = 'high'"
            >
              ≥9
            </button>
            <button 
              class="filter-chip blue" 
              :class="{ active: queueStore.confidenceFilter === 'medium' }"
              @click="queueStore.confidenceFilter = 'medium'"
            >
              6–8
            </button>
            <button 
              class="filter-chip yellow" 
              :class="{ active: queueStore.confidenceFilter === 'low' }"
              @click="queueStore.confidenceFilter = 'low'"
            >
              &lt;6
            </button>
          </div>

          <div class="filter-divider"></div>

          <!-- Фильтр ПК -->
          <div class="filter-chip-group">
            <span class="group-label">ПК:</span>
            <button 
              class="filter-chip" 
              :class="{ active: queueStore.hostFilter === 'all' }"
              @click="queueStore.hostFilter = 'all'"
            >
              Все
            </button>
            <button 
              class="filter-chip green" 
              :class="{ active: queueStore.hostFilter === 'online' }"
              @click="queueStore.hostFilter = 'online'"
            >
              <span class="mini-dot green"></span> Онлайн
            </button>
            <button 
              class="filter-chip red" 
              :class="{ active: queueStore.hostFilter === 'offline' }"
              @click="queueStore.hostFilter = 'offline'"
            >
              <span class="mini-dot red"></span> Офлайн
            </button>
          </div>

          <!-- Быстрые чекбоксы -->
          <div class="filter-toggles-inline">
            <NotionCheckbox v-model="queueStore.hasAttachmentsOnly" label="Вложения" size="sm" />
            <NotionCheckbox v-model="queueStore.redirectOnly" label="Редиректы" size="sm" />
          </div>

          <!-- Кнопка сброса -->
          <button 
            v-if="queueStore.hasActiveFilters" 
            class="reset-filters-btn" 
            @click="queueStore.resetFilters"
            title="Сбросить все активные фильтры"
          >
            <svg viewBox="0 0 24 24" width="12" height="12"><line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2"/><line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2"/></svg>
            <span>Сброс</span>
          </button>
        </div>

        <!-- Пакетные действия справа -->
        <div class="bulk-actions-right">
          <button class="btn btn-outline btn-xs" @click="queueStore.selectAllFiltered(queueStore.filteredTasks)">
            Выбрать все ({{ queueStore.filteredTasks.length }})
          </button>
          <button class="btn btn-outline btn-xs" @click="queueStore.selectAllConfident(queueStore.filteredTasks)">
            Типовые (≥9)
          </button>
          <button v-if="queueStore.selectedTaskIds.size > 0" class="btn btn-ghost btn-xs" @click="queueStore.deselectAll">
            Снять
          </button>

          <button 
            v-if="queueStore.selectedTaskIds.size > 0"
            class="btn btn-primary btn-xs bulk-apply-btn" 
            :disabled="queueStore.submittingIds.size > 0"
            @click="queueStore.applyBulkSelected"
          >
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
            <span>Применить ({{ queueStore.selectedTaskIds.size }})</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Загрузка и пустое состояние -->
    <div v-if="queueStore.loading && queueStore.tasks.length === 0" class="loading-state">
      <div class="spinner"></div>
      <p>Загрузка очереди заявок из IntraService...</p>
    </div>

    <div v-else-if="queueStore.filteredTasks.length === 0" class="empty-state">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" width="36" height="36" fill="none" stroke="var(--accent-primary)" stroke-width="1.75">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
          <polyline points="22 4 12 14.01 9 11.01"/>
        </svg>
      </div>
      <h3>Все заявки обработаны</h3>
      <p v-if="queueStore.hasActiveFilters">
        Нет заявок, соответствующих выбранным фильтрам. 
        <button class="inline-link-btn" @click="queueStore.resetFilters">Сбросить фильтры</button>.
      </p>
      <p v-else>
        В разделе <strong>«{{ queueStore.activeServiceTab === 'all' ? 'Все сервисы' : queueStore.activeServiceTab }}»</strong> нет открытых заявок.
      </p>
    </div>

    <!-- Режим 1: Карточки заявок (Linear Unified Card) -->
    <div v-else-if="queueStore.viewMode === 'cards'" class="tasks-grid" :class="{ 'is-compact': queueStore.density === 'compact' }">
      <div 
        v-for="t in queueStore.filteredTasks" 
        :key="t.id" 
        class="task-card"
        :class="{ 
          'is-selected': queueStore.selectedTaskIds.has(t.id),
          'is-submitting': queueStore.submittingIds.has(t.id), 
          'is-done': queueStore.doneIds.has(t.id) 
        }"
      >
        <!-- 1. Card Header: Checkbox + #ID + Title + Status + Score + Details -->
        <div class="card-header-bar">
          <div class="header-left">
            <NotionCheckbox 
              :model-value="queueStore.selectedTaskIds.has(t.id)"
              @update:model-value="queueStore.toggleSelect(t.id)"
              title="Выбрать заявку для пакетного применения"
            />

            <a 
              :href="`https://servicedesk.corporate.loc/Task/View/${t.id}`" 
              target="_blank" 
              class="task-id-link"
              title="Открыть заявку в IntraService"
            >
              #{{ t.id }}
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" class="link-arrow"><path d="M7 17L17 7M17 7H7M17 7V17"/></svg>
            </a>

            <!-- Главный заголовок инцидента -->
            <span class="incident-title-text">{{ t.name }}</span>

            <span class="task-status-tag">{{ t.status_name || 'Открыта' }}</span>
            <span class="task-category-tag" :class="t.badge_color">{{ t.category_label }}</span>
          </div>

          <div class="header-right">
            <div class="score-pill" :class="getScoreClass(t.score)" title="Оценка уверенности AI-правила">
              {{ t.score }}/10
            </div>
            <button class="btn btn-outline btn-xs drawer-btn" @click="queueStore.openTaskDrawer(t.id)" title="История и вложения">
              <svg v-if="t.has_attachments" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 3px;"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              <span>Детали</span>
            </button>
          </div>
        </div>

        <!-- 2. Метаданные (Инлайн-полоса в стиле Linear) -->
        <div class="card-meta-bar">
          <!-- Заявитель -->
          <div class="meta-item">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            <span class="meta-bold">{{ t.creator }}</span>
            <span v-if="t.department" class="meta-sub">({{ t.department }})</span>
          </div>

          <span v-if="t.room" class="meta-badge">каб. {{ t.room }}</span>
          
          <span v-if="t.phone" class="meta-item">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
            <span>{{ t.phone }}</span>
          </span>

          <span v-if="t.created" class="meta-sub-time">{{ formatDate(t.created) }}</span>

          <div class="meta-divider"></div>

          <!-- ПК и сеть -->
          <div class="meta-item host-meta">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
            <span class="meta-mono">{{ t.pc_name || 'ПК не указан' }}</span>
            <span v-if="getHostDiag(t.pc_name)?.loading" class="ping-pill loading">...</span>
            <span v-else-if="getHostDiag(t.pc_name)?.is_online" class="ping-pill online" :title="`Ping: ${getHostDiag(t.pc_name)?.avg_rtt}`">
              <span class="mini-dot green"></span> {{ getHostDiag(t.pc_name)?.avg_rtt }}
            </span>
            <span v-else-if="t.pc_name" class="ping-pill offline" title="ПК недоступен в сети">
              <span class="mini-dot red"></span> Офлайн
            </span>

            <button 
              v-if="t.pc_name && getHostDiag(t.pc_name)?.is_online === false" 
              class="quick-offline-btn" 
              title="Переключить в статус 35 (ПК не в сети) и вставить чек-лист"
              @click="setOfflineTemplate(t)"
            >
              🔴 Шаблон #35
            </button>
          </div>

          <div class="meta-divider"></div>

          <!-- Раздел каталога -->
          <div class="meta-item service-meta">
            <span class="meta-service-name">{{ t.root_service_name }}</span>
            <span v-if="t.service_name && t.service_name !== t.root_service_name" class="meta-subservice">➔ {{ t.service_name }}</span>
          </div>

          <!-- Редирект бейдж -->
          <span v-if="t.is_redirect" class="redirect-badge">
            ⚠️ Неверный раздел ➔ <strong>{{ t.target_service_name }}</strong>
          </span>
        </div>

        <!-- 3. Блок контекста: AI-анализ решения и оригинал заявки -->
        <div class="card-content-block">
          <!-- Решение AI (Сфокусированный блок) -->
          <div v-if="t.ai_summary" class="ai-summary-card">
            <div class="ai-card-header">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
              <span>Решение и анализ AI-ассистента:</span>
            </div>
            <div class="ai-card-text">{{ t.ai_summary }}</div>
          </div>

          <!-- Оригинал заявки (Аккуратный сворачиваемый блок) -->
          <details class="raw-desc-accordion" :open="!t.ai_summary">
            <summary class="raw-desc-summary">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
              <span>Исходный текст заявителя</span>
            </summary>
            <div class="raw-desc-body">
              {{ t.description || 'Описание в заявке отсутствует' }}
            </div>
          </details>
        </div>

        <!-- 4. Галерея вложений (если есть) -->
        <div v-if="t.attachments && t.attachments.length > 0" class="attachments-row">
          <span class="attachments-label">Вложения ({{ t.attachments.length }}):</span>
          <div class="att-chips">
            <a 
              v-for="att in t.attachments" 
              :key="att.id" 
              :href="att.url" 
              target="_blank" 
              class="att-chip-link"
              :class="{ 'is-img': isImageFile(att.name, att.content_type) }"
              :title="`Открыть ${att.name}`"
            >
              <svg v-if="isImageFile(att.name, att.content_type)" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
              <svg v-else viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
              <span class="att-name">{{ att.name }}</span>
              <span v-if="att.size" class="att-size">{{ formatFileSize(att.size) }}</span>
            </a>
          </div>
        </div>

        <!-- 5. Action Hub: Редактор ответа заявителю -->
        <div class="action-hub-box">
          <div class="hub-header-line">
            <span class="hub-title">Ответ заявителю:</span>
            <div class="hub-template-controls">
              <select 
                :value="t.template_key || 'general'" 
                class="linear-select tmpl-select"
                @change="(e: any) => queueStore.selectTemplateForTask(t, e.target.value)"
                title="Выбрать корпоративный шаблон"
              >
                <option v-for="tmpl in queueStore.templates" :key="tmpl.key" :value="tmpl.key">
                  {{ tmpl.name }} (статус {{ tmpl.status_id }})
                </option>
              </select>

              <button 
                class="btn btn-ghost btn-xs reset-comment-btn" 
                title="Сбросить комментарий к исходному AI-шаблону"
                @click="queueStore.resetTaskComment(t)"
              >
                Сброс
              </button>
            </div>
          </div>

          <div class="textarea-container">
            <textarea 
              v-model="t.suggested_comment" 
              class="linear-textarea" 
              rows="2" 
              placeholder="Введите комментарий ответа для заявителя..."
              @keydown.ctrl.enter="queueStore.applySingleAction(t)"
              @keydown.meta.enter="queueStore.applySingleAction(t)"
            ></textarea>
          </div>

          <div class="execution-bar">
            <div class="exec-left-items">
              <div class="exec-field">
                <span class="field-label">Статус:</span>
                <select v-model.number="t.target_status_id" class="linear-select status-select">
                  <option :value="27">27: В работе</option>
                  <option :value="29">29: Выполнена (Закрыть)</option>
                  <option :value="30">30: Отменена (Редирект/Дубль)</option>
                  <option :value="35">35: Требует уточнения (Офлайн)</option>
                  <option :value="48">48: Ожидание устройства</option>
                </select>
              </div>

              <div class="exec-field">
                <span class="field-label">Минут:</span>
                <input 
                  v-model.number="t.expenses" 
                  type="number" 
                  min="0" 
                  max="480" 
                  step="5" 
                  class="linear-input expenses-input" 
                />
                <div class="quick-exp-group">
                  <button type="button" class="exp-btn" @click="adjustExpenses(t, 5)">+5</button>
                  <button type="button" class="exp-btn" @click="adjustExpenses(t, 10)">+10</button>
                </div>
              </div>

              <NotionCheckbox 
                v-model="t.is_private" 
                label="Приватный"
                size="sm"
                title="Приватный комментарий для IT-отдела"
              />
            </div>

            <button 
              class="btn btn-primary btn-sm apply-action-btn" 
              :disabled="queueStore.submittingIds.has(t.id)"
              @click="queueStore.applySingleAction(t)"
              title="Отправить в IntraService (Ctrl + Enter)"
            >
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
              <span v-if="queueStore.submittingIds.has(t.id)">Отправка...</span>
              <span v-else>Применить ({{ t.target_status_id }})</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Режим 2: Инженерная таблица (Data Grid) -->
    <div v-else class="table-container" :class="{ 'is-compact': queueStore.density === 'compact' }">
      <table class="grid-table">
        <thead>
          <tr>
            <th width="40">
              <NotionCheckbox 
                :model-value="queueStore.filteredTasks.length > 0 && queueStore.selectedTaskIds.size === queueStore.filteredTasks.length"
                @change="handleSelectAllToggle"
                size="sm"
              />
            </th>
            <th class="sortable-th" width="100" @click="queueStore.toggleSort('id')">
              <span>№ Заявки</span>
              <span class="sort-icon">{{ getSortIcon('id') }}</span>
            </th>
            <th class="sortable-th" width="90" @click="queueStore.toggleSort('score')">
              <span>Оценка</span>
              <span class="sort-icon">{{ getSortIcon('score') }}</span>
            </th>
            <th class="sortable-th" width="170" @click="queueStore.toggleSort('creator')">
              <span>Заявитель</span>
              <span class="sort-icon">{{ getSortIcon('creator') }}</span>
            </th>
            <th class="sortable-th" width="150" @click="queueStore.toggleSort('pc_name')">
              <span>ПК / Сеть</span>
              <span class="sort-icon">{{ getSortIcon('pc_name') }}</span>
            </th>
            <th class="sortable-th" width="190" @click="queueStore.toggleSort('service')">
              <span>Сервис IntraService</span>
              <span class="sort-icon">{{ getSortIcon('service') }}</span>
            </th>
            <th>Тема и инцидент</th>
            <th width="240">Шаблон ответа</th>
            <th width="120">Действие</th>
          </tr>
        </thead>
        <tbody>
          <tr 
            v-for="t in queueStore.filteredTasks" 
            :key="t.id"
            :class="{ 
              'row-selected': queueStore.selectedTaskIds.has(t.id),
              'row-submitting': queueStore.submittingIds.has(t.id),
              'row-done': queueStore.doneIds.has(t.id)
            }"
          >
            <td>
              <NotionCheckbox 
                :model-value="queueStore.selectedTaskIds.has(t.id)"
                @update:model-value="queueStore.toggleSelect(t.id)"
                size="sm"
              />
            </td>
            <td class="td-mono">
              <a 
                :href="`https://servicedesk.corporate.loc/Task/View/${t.id}`" 
                target="_blank" 
                class="grid-id-link"
              >
                #{{ t.id }}
              </a>
            </td>
            <td>
              <div class="score-cell">
                <span class="score-pill" :class="getScoreClass(t.score)">{{ t.score }}</span>
                <span v-if="t.has_ai_solution" class="grid-ai-tag ready" title="Готов ответ AI">AI</span>
                <span v-else class="grid-ai-tag manual" title="Требуется ручной разбор">Ручной</span>
              </div>
            </td>
            <td>
              <div class="applicant-cell">
                <span class="cell-name">{{ t.creator }}</span>
                <span v-if="t.phone" class="cell-sub">
                  <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 2px;"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
                  {{ t.phone }}
                </span>
              </div>
            </td>
            <td>
              <div class="host-cell">
                <span class="cell-pc">{{ t.pc_name || '—' }}</span>
                <span v-if="getHostDiag(t.pc_name)?.loading" class="ping-pill loading">...</span>
                <span v-else-if="getHostDiag(t.pc_name)?.is_online" class="ping-pill online">
                  <span class="mini-dot green"></span> {{ getHostDiag(t.pc_name)?.avg_rtt }}
                </span>
                <span v-else-if="t.pc_name" class="ping-pill offline">
                  <span class="mini-dot red"></span> Офлайн
                </span>
              </div>
            </td>
            <td>
              <div class="service-cell" :title="t.service_path || t.root_service_name">
                <span class="service-pill">{{ t.root_service_name }}</span>
                <span v-if="t.service_name && t.service_name !== t.root_service_name" class="service-sub-text">{{ t.service_name }}</span>
              </div>
            </td>
            <td>
              <div class="incident-cell">
                <div class="incident-title" @click="queueStore.openTaskDrawer(t.id)">
                  <svg v-if="t.has_attachments" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" class="att-svg" title="Есть вложения"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                  <svg v-if="t.is_redirect" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="var(--yellow)" stroke-width="2" class="redirect-svg" title="Неверный раздел"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                  <span>{{ t.name }}</span>
                </div>
                <div class="incident-desc">{{ t.description }}</div>
              </div>
            </td>
            <td>
              <select 
                :value="t.template_key || 'general'" 
                class="linear-select grid-template-select"
                @change="(e: any) => queueStore.selectTemplateForTask(t, e.target.value)"
              >
                <option v-for="tmpl in queueStore.templates" :key="tmpl.key" :value="tmpl.key">
                  {{ tmpl.name }}
                </option>
              </select>
            </td>
            <td>
              <button 
                class="btn btn-primary btn-xs grid-apply-btn" 
                :disabled="queueStore.submittingIds.has(t.id)"
                @click="queueStore.applySingleAction(t)"
              >
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                <span v-if="queueStore.submittingIds.has(t.id)">...</span>
                <span v-else>Применить</span>
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import { useQueueStore, type SortColumn } from '../stores/queue';
import { usePolling } from '../composables/usePolling';
import QueueAnalyticsWidgets from '../components/QueueAnalyticsWidgets.vue';
import NotionCheckbox from '../components/NotionCheckbox.vue';

const queueStore = useQueueStore();

const handleGlobalKeydown = (e: KeyboardEvent) => {
  if (e.altKey && e.key.toLowerCase() === 'r') {
    e.preventDefault();
    queueStore.fetchQueue(false);
  }
};

onMounted(() => {
  queueStore.fetchQueue();
  window.addEventListener('keydown', handleGlobalKeydown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleGlobalKeydown);
});

usePolling(() => {
  queueStore.fetchQueue(true);
}, 15000);

const getScoreClass = (score: number) => {
  if (score >= 9) return 'score-high';
  if (score >= 6) return 'score-medium';
  return 'score-low';
};

const getHostDiag = (pcName?: string) => {
  if (!pcName) return null;
  return queueStore.hostStatusMap[pcName.trim()];
};

const getSortIcon = (col: SortColumn) => {
  if (queueStore.sortBy !== col) return '↕';
  return queueStore.sortDirection === 'asc' ? '▲' : '▼';
};

const handleSelectAllToggle = (e: any) => {
  if (e.target.checked) {
    queueStore.selectAllFiltered(queueStore.filteredTasks);
  } else {
    queueStore.deselectAll();
  }
};

const handleConfidenceFilter = (level: 'high' | 'medium' | 'low') => {
  queueStore.confidenceFilter = level;
};

const formatDate = (dateStr?: string) => {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const hours = String(d.getHours()).padStart(2, '0');
    const mins = String(d.getMinutes()).padStart(2, '0');
    return `${day}.${month} ${hours}:${mins}`;
  } catch {
    return dateStr;
  }
};

const formatFileSize = (bytes?: number) => {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const isImageFile = (name?: string, ct?: string) => {
  if (ct && ct.startsWith('image/')) return true;
  if (!name) return false;
  return /\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(name);
};

const setOfflineTemplate = (task: any) => {
  queueStore.selectTemplateForTask(task, 'pc_offline');
  task.target_status_id = 35;
  task.target_status_name = 'Требует уточнения';
};

const adjustExpenses = (task: any, delta: number) => {
  const current = Number(task.expenses) || 0;
  task.expenses = Math.max(0, current + delta);
};
</script>

<style scoped>
.queue-dashboard {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

/* ─── 2-Row Linear Toolbar ─── */
.linear-toolbar {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  box-shadow: var(--shadow-sm);
}

.toolbar-main-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.toolbar-brand {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.toolbar-title {
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.count-badge {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  font-weight: 600;
  background: var(--tag-default-bg);
  color: var(--text-secondary);
  padding: 0.1rem 0.45rem;
  border-radius: 4px;
}

.toolbar-sub {
  font-size: 0.74rem;
  color: var(--text-muted);
}

/* Segment Control */
.stream-segment-control {
  display: inline-flex;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 2px;
  gap: 2px;
}

.segment-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.25rem 0.65rem;
  border-radius: 4px;
  font-size: 0.74rem;
  font-weight: 500;
  color: var(--text-secondary);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.15s ease;
}

.segment-btn:hover {
  color: var(--text-primary);
}

.segment-btn.active {
  background: var(--bg-surface);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
  font-weight: 600;
}

.segment-btn.ready.active {
  color: var(--tag-purple-text);
  background: var(--tag-purple-bg);
}

.segment-btn.manual.active {
  color: var(--tag-yellow-text);
  background: var(--tag-yellow-bg);
}

.segment-badge {
  font-size: 0.68rem;
  font-family: var(--font-mono);
  font-weight: 600;
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--tag-default-bg);
  color: var(--text-secondary);
}

.segment-badge.ready {
  background: rgba(168, 85, 247, 0.2);
  color: var(--tag-purple-text);
}

.segment-badge.manual {
  background: rgba(245, 158, 11, 0.2);
  color: var(--tag-yellow-text);
}

/* Controls Right */
.toolbar-controls-right {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.search-input-wrap {
  position: relative;
  width: 220px;
}

.search-input {
  width: 100%;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0.32rem 1.6rem 0.32rem 1.85rem;
  font-size: 0.78rem;
  color: var(--text-primary);
  outline: none;
  font-family: var(--font-sans);
  transition: all 0.15s ease;
}

.search-input:focus {
  background: var(--bg-surface);
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.15);
}

.search-svg {
  position: absolute;
  left: 0.55rem;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  pointer-events: none;
}

.clear-btn {
  position: absolute;
  right: 0.45rem;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
}

.clear-btn:hover {
  color: var(--text-primary);
}

.view-switch-group {
  display: flex;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 2px;
  gap: 2px;
}

.view-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 26px;
  background: none;
  border: none;
  color: var(--text-muted);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.view-btn:hover {
  color: var(--text-primary);
}

.view-btn.active {
  background: var(--bg-surface);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
}

.refresh-btn {
  font-size: 0.76rem;
  padding: 0.32rem 0.75rem;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

/* ─── Toolbar Row 2: Filters & Bulk Actions ─── */
.toolbar-filter-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.85rem;
  padding-top: 0.55rem;
  border-top: 1px solid var(--border-subtle);
  flex-wrap: wrap;
}

.filters-left {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.linear-select {
  font-size: 0.74rem;
  padding: 0.24rem 0.55rem;
  border-radius: 5px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  outline: none;
  font-family: var(--font-sans);
  transition: all 0.15s ease;
}

.linear-select:focus {
  border-color: var(--accent-primary);
}

.service-select {
  font-weight: 500;
  max-width: 220px;
}

.subservice-select {
  max-width: 170px;
}

.filter-divider {
  width: 1px;
  height: 16px;
  background: var(--border-subtle);
}

.filter-chip-group {
  display: flex;
  align-items: center;
  gap: 0.2rem;
}

.group-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--text-muted);
  margin-right: 0.1rem;
}

.filter-chip {
  font-size: 0.72rem;
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.filter-chip:hover {
  color: var(--text-primary);
  border-color: var(--border-hover);
}

.filter-chip.active {
  background: var(--text-primary);
  color: #ffffff;
  border-color: transparent;
  font-weight: 600;
}

.filter-chip.green.active {
  background: var(--tag-green-bg);
  color: var(--tag-green-text);
  border-color: var(--tag-green-border);
}

.filter-chip.blue.active {
  background: var(--tag-blue-bg);
  color: var(--tag-blue-text);
  border-color: var(--tag-blue-border);
}

.filter-chip.yellow.active {
  background: var(--tag-yellow-bg);
  color: var(--tag-yellow-text);
  border-color: var(--tag-yellow-border);
}

.filter-chip.red.active {
  background: var(--tag-red-bg);
  color: var(--tag-red-text);
  border-color: var(--tag-red-border);
}

.filter-toggles-inline {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding-left: 0.2rem;
}

.reset-filters-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 0.72rem;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.15rem 0.35rem;
  border-radius: 3px;
  transition: all 0.15s ease;
}

.reset-filters-btn:hover {
  color: #ef4444;
  background: var(--bg-hover);
}

.bulk-actions-right {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.bulk-apply-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-weight: 600;
}

/* ─── Linear Unified Task Cards Grid ─── */
.tasks-grid {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

.tasks-grid.is-compact {
  gap: 0.5rem;
}

.task-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 0.9rem 1.15rem;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  box-shadow: var(--shadow-sm);
  transition: all 0.2s ease;
}

.task-card:hover {
  border-color: var(--border-hover);
  box-shadow: var(--shadow-md);
}

.task-card.is-selected {
  border-color: var(--accent-primary);
  background: var(--bg-selected);
}

.task-card.is-submitting {
  opacity: 0.6;
  pointer-events: none;
}

.task-card.is-done {
  border-left: 3px solid #10b981;
}

/* Card Header Bar */
.card-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  min-width: 0;
}

.task-id-link {
  font-family: var(--font-mono);
  font-size: 0.84rem;
  font-weight: 700;
  color: var(--accent-primary);
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.task-id-link:hover {
  text-decoration: underline;
}

.incident-title-text {
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.35;
}

.task-status-tag {
  font-size: 0.68rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: var(--tag-default-bg);
  color: var(--tag-default-text);
  border: 1px solid var(--tag-default-border);
}

.task-category-tag {
  font-size: 0.68rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  border: 1px solid transparent;
}

.task-category-tag.cat-account {
  background: var(--tag-blue-bg);
  color: var(--tag-blue-text);
  border-color: var(--tag-blue-border);
}

.task-category-tag.cat-software {
  background: var(--tag-purple-bg);
  color: var(--tag-purple-text);
  border-color: var(--tag-purple-border);
}

.task-category-tag.cat-hardware {
  background: var(--tag-yellow-bg);
  color: var(--tag-yellow-text);
  border-color: var(--tag-yellow-border);
}

.task-category-tag.cat-general {
  background: var(--tag-default-bg);
  color: var(--tag-default-text);
  border-color: var(--tag-default-border);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  flex-shrink: 0;
}

.score-pill {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0.1rem 0.45rem;
  border-radius: 4px;
  border: 1px solid transparent;
}

.score-pill.score-high {
  background: var(--tag-green-bg);
  color: var(--tag-green-text);
  border-color: var(--tag-green-border);
}

.score-pill.score-medium {
  background: var(--tag-blue-bg);
  color: var(--tag-blue-text);
  border-color: var(--tag-blue-border);
}

.score-pill.score-low {
  background: var(--tag-yellow-bg);
  color: var(--tag-yellow-text);
  border-color: var(--tag-yellow-border);
}

.drawer-btn {
  display: inline-flex;
  align-items: center;
}

/* Card Meta Bar (Single inline metadata line) */
.card-meta-bar {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
  flex-wrap: wrap;
  background: var(--bg-subtle);
  padding: 0.35rem 0.65rem;
  border-radius: 5px;
  border: 1px solid var(--border-subtle);
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.meta-bold {
  font-weight: 600;
  color: var(--text-primary);
}

.meta-sub {
  color: var(--text-muted);
  font-size: 0.72rem;
}

.meta-badge {
  font-size: 0.68rem;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  padding: 0.05rem 0.35rem;
  border-radius: 3px;
  color: var(--text-secondary);
}

.meta-sub-time {
  font-size: 0.7rem;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.meta-divider {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--text-muted);
}

.meta-mono {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text-primary);
}

.ping-pill {
  font-family: var(--font-mono);
  font-size: 0.66rem;
  padding: 0.05rem 0.35rem;
  border-radius: 3px;
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.ping-pill.online {
  background: var(--tag-green-bg);
  color: var(--tag-green-text);
}

.ping-pill.offline {
  background: var(--tag-red-bg);
  color: var(--tag-red-text);
}

.mini-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}

.mini-dot.green {
  background: #10b981;
}

.mini-dot.red {
  background: #ef4444;
}

.quick-offline-btn {
  font-size: 0.66rem;
  padding: 0.08rem 0.35rem;
  border-radius: 3px;
  background: var(--tag-red-bg);
  color: var(--tag-red-text);
  border: 1px solid var(--tag-red-border);
  cursor: pointer;
  transition: all 0.15s ease;
}

.quick-offline-btn:hover {
  filter: brightness(0.95);
}

.meta-service-name {
  color: var(--text-primary);
  font-weight: 500;
}

.meta-subservice {
  color: var(--text-muted);
  font-size: 0.72rem;
}

.redirect-badge {
  font-size: 0.7rem;
  background: var(--tag-yellow-bg);
  color: var(--tag-yellow-text);
  border: 1px solid var(--tag-yellow-border);
  padding: 0.08rem 0.4rem;
  border-radius: 4px;
}

/* Card Content: AI Solution & Raw Description */
.card-content-block {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.ai-summary-card {
  background: var(--tag-purple-bg);
  border: 1px solid var(--tag-purple-border);
  border-left: 3px solid #9333ea;
  border-radius: 6px;
  padding: 0.55rem 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.ai-card-header {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--tag-purple-text);
}

.ai-card-text {
  font-size: 0.78rem;
  color: var(--text-primary);
  line-height: 1.4;
}

.raw-desc-accordion {
  font-size: 0.76rem;
}

.raw-desc-summary {
  font-size: 0.72rem;
  font-weight: 500;
  color: var(--text-muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  user-select: none;
}

.raw-desc-summary:hover {
  color: var(--text-primary);
}

.raw-desc-body {
  margin-top: 0.3rem;
  padding: 0.5rem 0.75rem;
  background: var(--bg-subtle);
  border-radius: 5px;
  color: var(--text-secondary);
  font-size: 0.76rem;
  line-height: 1.4;
  white-space: pre-wrap;
  word-break: break-word;
}

/* Attachments */
.attachments-row {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.72rem;
  flex-wrap: wrap;
}

.attachments-label {
  color: var(--text-muted);
  font-weight: 600;
}

.att-chips {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
}

.att-chip-link {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-size: 0.7rem;
  text-decoration: none;
  transition: all 0.15s ease;
}

.att-chip-link:hover {
  border-color: var(--accent-primary);
  color: var(--accent-primary);
}

.att-size {
  font-family: var(--font-mono);
  font-size: 0.64rem;
  color: var(--text-muted);
}

/* Action Hub Box */
.action-hub-box {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  padding-top: 0.55rem;
  border-top: 1px dashed var(--border-subtle);
}

.hub-header-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.hub-title {
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--text-secondary);
}

.hub-template-controls {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.tmpl-select {
  font-size: 0.72rem;
  padding: 0.18rem 0.45rem;
}

.reset-comment-btn {
  font-size: 0.7rem;
}

.linear-textarea {
  width: 100%;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0.45rem 0.65rem;
  font-size: 0.8rem;
  color: var(--text-primary);
  outline: none;
  font-family: var(--font-sans);
  line-height: 1.4;
  resize: vertical;
  transition: all 0.15s ease;
}

.linear-textarea:focus {
  background: var(--bg-surface);
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.15);
}

.execution-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.exec-left-items {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  flex-wrap: wrap;
}

.exec-field {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.74rem;
}

.field-label {
  color: var(--text-muted);
  font-weight: 600;
}

.status-select {
  font-size: 0.72rem;
  padding: 0.18rem 0.45rem;
  min-width: 160px;
}

.linear-input {
  font-family: var(--font-mono);
  font-size: 0.74rem;
  text-align: center;
  padding: 0.18rem 0.35rem;
  border-radius: 4px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  outline: none;
}

.linear-input:focus {
  border-color: var(--accent-primary);
}

.expenses-input {
  width: 46px;
}

.quick-exp-group {
  display: flex;
  gap: 2px;
}

.exp-btn {
  font-family: var(--font-mono);
  font-size: 0.66rem;
  padding: 0.15rem 0.35rem;
  border-radius: 3px;
  background: var(--tag-default-bg);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
}

.exp-btn:hover {
  background: var(--tag-blue-bg);
  color: var(--tag-blue-text);
  border-color: transparent;
}

.apply-action-btn {
  font-size: 0.78rem;
  padding: 0.35rem 0.85rem;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-weight: 600;
  margin-left: auto;
}

/* ─── Grid Table ─── */
.table-container {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  overflow-x: auto;
  box-shadow: var(--shadow-sm);
}

.grid-table {
  width: 100%;
  border-collapse: collapse;
}

.grid-table th {
  background: var(--bg-subtle);
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
  padding: 0.65rem 0.85rem;
  text-align: left;
  border-bottom: 1px solid var(--border-subtle);
}

.sortable-th {
  cursor: pointer;
  user-select: none;
}

.sortable-th:hover {
  color: var(--text-primary);
}

.sort-icon {
  margin-left: 0.3rem;
  font-size: 0.65rem;
}

.grid-table td {
  padding: 0.65rem 0.85rem;
  border-bottom: 1px solid var(--border-subtle);
  font-size: 0.8rem;
  vertical-align: middle;
  color: var(--text-primary);
}

.grid-table tr:hover td {
  background: var(--bg-hover);
}

.grid-id-link {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--accent-primary);
  text-decoration: none;
}

.applicant-cell, .host-cell {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.cell-name {
  font-weight: 500;
  color: var(--text-primary);
}

.cell-sub {
  font-size: 0.68rem;
  color: var(--text-muted);
}

.service-pill {
  font-size: 0.74rem;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 150px;
  display: block;
}

.incident-cell {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  max-width: 340px;
}

.incident-title {
  font-weight: 600;
  color: var(--text-primary);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.incident-title:hover {
  color: var(--accent-primary);
}

.incident-desc {
  font-size: 0.72rem;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.grid-template-select {
  font-size: 0.74rem;
  padding: 0.25rem 0.5rem;
}

.grid-apply-btn {
  font-size: 0.72rem;
  padding: 0.25rem 0.5rem;
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.loading-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3.5rem 1rem;
  text-align: center;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  gap: 0.85rem;
}

.empty-icon {
  font-size: 2.2rem;
}

.inline-link-btn {
  background: none;
  border: none;
  color: var(--accent-primary);
  cursor: pointer;
  text-decoration: underline;
  padding: 0;
  font-size: inherit;
}

.grid-ai-tag {
  font-size: 0.64rem;
  font-weight: 600;
  padding: 1px 4px;
  border-radius: 3px;
  text-transform: uppercase;
}

.grid-ai-tag.ready {
  background: var(--tag-purple-bg);
  color: var(--tag-purple-text);
}

.grid-ai-tag.manual {
  background: var(--tag-default-bg);
  color: var(--text-muted);
}
</style>
