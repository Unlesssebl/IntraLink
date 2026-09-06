# Единый план реализации: Аудит решений, автопилот и Web UI

Документ является **единственным источником правды (Single Source of Truth, SSOT)** по сквозной реализации журнала решений, устранению опасного поведения автопилота, стабилизации AI-контура и унификации Web UI.
Требования зафиксированы в [`requirements.md`](requirements.md).

---

## 1. Архитектурный контекст и статус бэкенда

Цель проекта: расширить текущий `core-api` и PostgreSQL без введения новых микросервисов. Rule Engine, RAG, AI-синтез, политика и исполнение должны работать как прозрачные этапы единого процесса принятия решений, а Web UI — отображать консолидированное предложение с исчерпывающими основаниями, изолированным черновиком и фактическим результатом.

### Статус серверной реализации (Backend Readiness — 100%)
- **Раздел 1 (Безопасность автопилота):** Неподдерживаемые сценарии переводятся в `paused` (`unsupported_scenario`); инфраструктурные сбои и частичный успех останавливают цикл без отмены заявки.
- **Раздел 2 (SSOT учетных данных):** Сервисная учетная запись IntraService и доменные доступы хранятся в PostgreSQL (`system_settings`) с шифрованием Fernet и синхронизируются в Redis (`app/services/vault.py`).
- **Раздел 3 (Реестр сценариев):** Таблица `autopilot_scenarios` в PostgreSQL управляет включением сценариев по разделам каталога.
- **Раздел 4 (Журнал решений):** Введены сущности `decision_records`, `decision_steps` и `decision_feedback` (`app/services/decision_journal.py`).
- **Раздел 5 (Серверные контракты):** Эндпоинты `/api/v2/tasks/{task_id}/decisions` и `/api/v2/decisions/{id}/feedback` активны в `app/routers/decisions.py`. Карточка триажа возвращает структурированный объект `decision` (поля `sources`, `readiness`, `proposal`, `completeness`, `policy`, `steps`).
- **Раздел 5.1 (Стабилизация AI-кэша — ВЫПОЛНЕНО):** В `app/services/triage_service.py` динамический timestamp `telemetry_collected_at` заменен на стабильный логический статус `telemetry_status` (`online`/`offline`), устранив паразитный сброс кэша AI-синтеза при плановых пингах хостов.

---

## 2. Архитектурный аудит Web UI (Deep Reflect)

В текущей реализации Web UI (`intra-web`) выявлены критические проблемы:
1. **Слепой опрос (Blind Short Polling 15с):** Каждые 15 секунд клиент запрашивает 200 тикетов (`App.tsx`). Это перегружает шлюз и вызывает каскадный ре-рендер `TicketInspector`.
2. **Затирание черновика оператора:** При получении нового массива `tickets` ссылки на объекты меняются. Срабатывающий `useEffect([ticket.id, loadDetails])` строкой `setReplyText(initialText)` уничтожает набираемый инженером текст ответа.
3. **Разрозненность компонентов:** `AiTriageCard`, `RagMatchesSection`, `AiSummarySection`, `DiagnosticsSection` и `ReplyActionForm` находятся в разных частях инспектора вместо единой консолидированной панели.
4. **Нарушение иерархии управления:** Главная кнопка выполнения действия оторвана от карточки решения и расположена в самом низу формы ответа.
5. **Очередь заявок:** В `QueuePage` отсутствуют фильтры аудита (`paused`, `waiting_approval`, `system_error`, `fallback`, `autopilot`), а состояния `TicketRun` не отображаются в виде заметных статус-бейджей.
6. **Дизайн-система:** Наличие эмодзи вместо строгих векторных SVG-иконок (Linear / Vercel style).

### Принятые решения:
1. **Адаптивный дифференциальный опрос (Adaptive Polling):**
   - Опрос общего списка 200 тикетов в `App.tsx` замедляется до **60 секунд** с проверкой `document.visibilityState === 'visible'`.
   - В инспекторе: частый опрос (3 с) работает **только в переходных статусах `TicketRun` (`running`, `pending`)**. При переходе в стабильные статусы (`completed`, `paused`, `waiting_approval`, `system_error`) опрос **полностью останавливается (Zero Polling)**.
2. **Архитектурная изоляция черновика (Decoupled Draft Buffer):**
   - Текст ответа хранится в независимом стейте с синхронизацией в `sessionStorage` (`intralink_draft_${taskId}`). Внешние обновления очереди никогда не перезаписывают введённый текст.
3. **Неблокирующий Staleness Guard:**
   - При изменении заявки на сервере черновик не сбрасывается; выводится ненавязчивый янтарный баннер: *«Заявка обновилась на сервере. [Пересчитать по актуальным данным]»*.
4. **Консолидация в единую панель:**
   - Замена 5 разрозненных карточек на единую `UnifiedDecisionPanel.tsx` с иерархией сверху вниз.

---

## 3. Стандарты дизайн-системы (Linear / Vercel Standard)

В соответствии с правилами разработки фронтенда ([`.agents/skills/frontend-design/SKILL.md`]):
- **Полный отказ от эмодзи:** Запрещено использование любых символов эмодзи в интерфейсе. Все пиктограммы реализуются исключительно через векторные SVG-иконки с единой толщиной штриха (`stroke-width="1.5"` или `2`) и стандартными размерами (`14x14`, `16x16`).
- **Индикаторы реального статуса:** Статус-индикаторы (pills с точечным цветовым индикатором dot и понятной типографикой):
  - `running`: пульсирующая синяя точка (`bg-blue-500 animate-pulse`), фон `bg-blue-50 dark:bg-blue-950/40`;
  - `waiting_approval`: пульсирующая янтарная точка (`bg-amber-500 animate-pulse`), фон `bg-amber-50 dark:bg-amber-950/40`;
  - `paused` («нужно внимание»): статичная оранжевая точка (`bg-amber-600`), фон `bg-amber-50/80 dark:bg-amber-950/30`;
  - `system_error`: статичная красная точка (`bg-rose-500`), фон `bg-rose-50 dark:bg-rose-950/40`;
  - `completed` / `ready`: изумрудная точка (`bg-emerald-500`), фон `bg-emerald-50 dark:bg-emerald-950/40`.
- **Типографика:** Интерфейсный шрифт `Inter` для текстов и меток; моноширинный шрифт `JetBrains Mono` для идентификаторов заявок, версий, хэшей, IP-адресов и временных меток.
- **Цветовая палитра:** Высококонтрастная нейтральная база (`neutral-50`..`neutral-950`).

---

## 4. Матрица файлов (Маппинг изменений)

| Действие | Файл | Описание |
|---|---|---|
| `[DONE]` | `core-api/app/services/triage_service.py` | Стабилизация кэша AI-резолюции (`telemetry_status` вместо `telemetry_collected_at`) |
| `[NEW]` | `intra-web/src/components/inspector/useUnifiedDecision.ts` | Хук состояния черновика, адаптивного поллинга, Staleness Guard, вкладок и фидбэка |
| `[NEW]` | `intra-web/src/components/inspector/UnifiedDecisionPanel.tsx` | Единая монолитная панель управления решением |
| `[MODIFY]` | `intra-web/src/components/Icons.tsx` | Векторные SVG-иконки (`IconPause`, `IconCheckCircle`, `IconAlertCircle`, `IconFileCode`, `IconServer`, `IconDatabase`) |
| `[MODIFY]` | `intra-web/src/App.tsx` | Адаптивный 60-секундный опрос очереди с дедупликацией и проверкой видимости окна |
| `[MODIFY]` | `intra-web/src/components/TicketInspector.tsx` | Замена разрозненных секций на единый `UnifiedDecisionPanel`, изоляция черновика |
| `[MODIFY]` | `intra-web/src/components/inspector/RequesterCard.tsx` | Компактный статус-бейдж доступности хоста без дублирования полной диагностики |
| `[MODIFY]` | `intra-web/src/pages/QueuePage.tsx` | Чипы фильтров аудита, цветные pills `TicketRun`, адаптивный опрос раннеров, кэш сбоев |
| `[DELETE]` | `intra-web/src/components/inspector/AiTriageCard.tsx` | **Удаление:** функционал полностью перенесен в `UnifiedDecisionPanel` |
| `[DELETE]` | `intra-web/src/components/inspector/ReplyActionForm.tsx` | **Удаление:** редактор и отправка объединены в `UnifiedDecisionPanel` |
| `[DELETE]` | `intra-web/src/components/inspector/RagMatchesSection.tsx` | **Удаление:** перенесено во вкладку оснований `UnifiedDecisionPanel` |
| `[DELETE]` | `intra-web/src/components/inspector/AiSummarySection.tsx` | **Удаление:** перенесено во вкладку оснований `UnifiedDecisionPanel` |
| `[DELETE]` | `intra-web/src/components/inspector/TicketRunCard.tsx` | **Удаление:** статус и управление циклом вынесены в Hero Header панели |

---

## 5. Детальная спецификация компонентов

### 5.1. Векторные SVG-иконки (`Icons.tsx`)
SVG-иконки с единым стилем (`stroke="currentColor" strokeWidth="2" fill="none"`):
- `IconPause`: пауза цикла;
- `IconCheckCircle`: подтверждение / валидность;
- `IconAlertCircle`: ошибки и предупреждения;
- `IconFileCode`: технические сведения / JSON payload;
- `IconServer`: сетевая диагностика хоста;
- `IconDatabase`: база знаний RAG.

### 5.2. Строгий TypeScript-контракт хука (`useUnifiedDecision.ts`)
```typescript
export interface UseUnifiedDecisionProps {
  ticket: Ticket;
  details: TaskDetails | null;
  rawId: number;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
  onClose?: () => void;
}

export interface UseUnifiedDecisionReturn {
  // Изолированное состояние формы ответа
  replyText: string;
  setReplyText: (val: string) => void;
  replyMode: 'reply' | 'internal';
  setReplyMode: (mode: 'reply' | 'internal') => void;
  expenses: number;
  setExpenses: (val: number) => void;
  selectedTemplateKey: string;
  setSelectedTemplateKey: (key: string) => void;
  selectedStatusOverride: number | null;
  setSelectedStatusOverride: (statusId: number | null) => void;

  // Вкладки оснований
  selectedTab: 'rules' | 'rag' | 'ai' | 'diagnostics' | 'completeness';
  setSelectedTab: (tab: 'rules' | 'rag' | 'ai' | 'diagnostics' | 'completeness') => void;

  // Состояние жизненного цикла и защиты
  ticketRun: TicketRun | null;
  isStale: boolean;
  submitting: boolean;
  reanalyzing: boolean;
  feedbackSubmitted: boolean;

  // Действия оператора
  handleApplyDecision: () => Promise<void>;
  handleCancelTicket: () => Promise<void>;
  handleTakeTicket: () => Promise<void>;
  handleReanalyze: () => Promise<void>;
  handleHitlApprove: () => Promise<void>;
  handleHitlReject: () => Promise<void>;
  handleTogglePauseRun: () => Promise<void>;
  handleSubmitFeedback: (
    verdict: 'correct' | 'partially_correct' | 'incorrect' | 'insufficient_data',
    reasonCode?: string
  ) => Promise<void>;
  insertSnippet: (snippet: string) => void;
}
```

### 5.3. Архитектура UnifiedDecisionPanel.tsx
Компонент организует взаимодействие с заявкой строго сверху вниз:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. HERO HEADER                                                         │
│ [Автопилот/Ручной] [Статус TicketRun] [Кнопки Пауза/Плей] [HitL Блок]  │
│ Предлагаемое действие -> Целевой статус | Последствия: 10 мин | Ready  │
│ [ГЛАВНАЯ КНОПКА ВЫПОЛНЕНИЯ]  [Альтернативы: Взять / Отменить / Статус] │
│ ────────────────────────────────────────────────────────────────────── │
│ 2. STALENESS BANNER (появляется только при изменении заявки на сервере)│
│ "Заявка обновилась на сервере" -> [Пересчитать]                        │
│ ────────────────────────────────────────────────────────────────────── │
│ 3. РЕДАКТОР ОТВЕТА И ПАРАМЕТРОВ (ИЗОЛИРОВАННЫЙ БУФЕР)                  │
│ [Ответ заявителю / Внутренний коммент] [Списание мин: 10]              │
│ [Текстовое поле с защитой от затирания]                                │
│ Быстрые вставки: [+ AI-синтез] [+ Из RAG] [Выбор шаблона регламента]   │
│ ────────────────────────────────────────────────────────────────────── │
│ 4. ОСНОВАНИЯ РЕШЕНИЯ (ТАБЫ)                                            │
│ [Правила регламента] [База знаний RAG] [AI-синтез] [Диагностика] [Контекст]│
│ Содержимое выбранного таба (факты, цитаты RAG, RTT/SMB/WinRM, лимиты)  │
│ ────────────────────────────────────────────────────────────────────── │
│ 5. ХРОНИКА И АУДИТ                                                     │
│ Человекочитаемые события (RU) + Раскрывающийся аккордеон с JSON        │
│ ────────────────────────────────────────────────────────────────────── │
│ 6. ОЦЕНКА РЕШЕНИЯ ОПЕРАТОРОМ (FEEDBACK)                               │
│ [Верно] [Частично] [Ошибка] [Мало данных] -> выбор причины ошибки      │
└────────────────────────────────────────────────────────────────────────┘
```

#### Обработка Cold-Start и Fallback (Graceful Degradation):
При отсутствии серверного объекта `decision` (старая закрытая заявка или отсутствие связи):
- Панель не падает, а деградирует до ручного режима: скрываются блоки автопилота и HitL.
- Дефолтное действие: «Ручная обработка оператором», целевой статус `27` («В работе»), списание `10` мин.
- Главная кнопка: «Сохранить и отправить ответ».
- Вкладка «Правила»: информационная плашка *«Специфических регламентных правил не обнаружено. Применяются стандартные действия 1-й линии»*.

### 5.4. Очередь заявок (`QueuePage.tsx` и `App.tsx`)
- Интервал фонового опроса очереди в `App.tsx`: **60 секунд** с проверкой `document.visibilityState === 'visible'`.
- Чипы фильтров аудита без эмодзи: `Внимание`, `Подтверждение`, `Ошибка связи`, `Fallback`, `Автопилот`.
- Статус-бейджи: цветные pills жизненного цикла `TicketRun` в таблице и канбан-карточках.
- Отказоустойчивость: при сбое связи к `/api/v2/ticket-runs` сохранять последнее известное состояние с баннером предупреждения.

---

## 6. Поэтапный график работ

### Этап 0: Бэкенд — стабилизация кэша триажа (`core-api`) — [ВЫПОЛНЕНО]
1. [x] В `triage_service.py` заменить `telemetry_collected_at` на `telemetry_status`.
2. [x] Прогнать тесты бэкенда: `$env:PYTHONPATH=".;core-api"; uv run pytest core-api/tests/test_admin_queue.py -q` (7 passed).

### Этап 1: Дизайн-токены, иконки и хук адаптивного состояния (`intra-web`) — [ВЫПОЛНЕНО]
1. [x] Добавить 6 векторных иконок в `intra-web/src/components/Icons.tsx`.
2. [x] Реализовать хук `useUnifiedDecision.ts` (изолированный черновик, адаптивный поллинг 3с/0с, Staleness Guard).
3. [x] Оптимизировать таймер очереди в `App.tsx` (60с с проверкой активности окна).

### Этап 2: UnifiedDecisionPanel и удаление дубликатов — [ВЫПОЛНЕНО]
1. [x] Реализовать `UnifiedDecisionPanel.tsx` (Hero Header, редактор, вкладки оснований, хроника, feedback, Fallback).
2. [x] Подключить `UnifiedDecisionPanel` в `TicketInspector.tsx`.
3. [x] Облегчить `RequesterCard.tsx` (только экспресс-индикатор хоста).
4. [x] Удалить 5 устаревших файлов (`AiTriageCard.tsx`, `ReplyActionForm.tsx`, `RagMatchesSection.tsx`, `AiSummarySection.tsx`, `TicketRunCard.tsx`).

### Этап 3: Модернизация QueuePage — [ВЫПОЛНЕНО]
1. [x] Реализовать чипы фильтров аудита в панели фильтров очереди.
2. [x] Встроить статус-индикаторы `TicketRun` в таблицу и канбан-доску.
3. [x] Добавить сохранение кэша циклов и баннер деградации при сбоях связи.

### Этап 4: Верификация и приёмка — [ВЫПОЛНЕНО]
1. [x] Сборка фронтенда: `npm --prefix intra-web run build` (0 ошибок типизации, сгенерирован бандл).
2. [x] Полный прогон тестов бэкенда: `$env:PYTHONPATH=".;core-api"; uv run pytest core-api/tests/ -q` (347 passed).
3. [x] Прогон тестов общего пакета: `$env:PYTHONPATH="."; uv run pytest shared/ -q` (10 passed).
4. [ ] Финальная сценарная приёмка пользователем в браузере.

---

## 7. Сквозная верификация и приёмка

1. **Сборка фронтенда:** `npm --prefix intra-web run build` (без ошибок `tsc -b`).
2. **Бэкенд-тесты:** `$env:PYTHONPATH=".;core-api"; uv run pytest core-api/tests/ -q` (347 passed).
3. **Сценарный аудит:**
   - **Защита черновика:** Ввод текста в поле ответа гарантированно сохраняется при фоновом обновлении очереди через 60 секунд.
   - **Адаптивный поллинг:** Запросы раз в 3с идут только при статусах `running`/`pending`; в статусах `waiting_approval`, `paused`, `completed` запросы полностью прекращаются (Zero Polling).
   - **Staleness Guard:** Внешнее изменение заявки вызывает появление неблокирующего баннера устаревания без очистки черновика оператора.
   - **Дизайн-система:** В интерфейсе отсутствуют эмодзи; используются только строгие векторные SVG-иконки и цветные pills статусов.
   - **Фильтры очереди:** Чипы `Внимание`, `Подтверждение`, `Ошибка связи`, `Fallback`, `Автопилот` корректно фильтруют список заявок.
   - **Отказоустойчивость:** При сбое `/api/v2/ticket-runs` интерфейс сохраняет отображение предыдущего состояния и выводит предупреждающий баннер.
