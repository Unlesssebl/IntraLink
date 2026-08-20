# Helpdesk Agent (AGY-Native Tools & RAG Engine)

Техническая документация для разработчиков и AI-агентов по микромодулю `helpdesk_agent`.

---

## 📌 Назначение и Концепция
`helpdesk_agent` — это легковесный, высокоскоростной набор I/O-инструментов, сетевой диагностики, адаптивных шаблонов и семантического поиска (RAG), предназначенный для выполнения задач технической поддержки в IntraService **напрямую из среды Antigravity (AGY)** в интерактивном режиме (Human-in-the-Loop через модальные окна `ask_question`).

> [!IMPORTANT]
> **Принцип единого интеллекта (No Second Brain):**
> Внутри скриптов `helpdesk_agent` **нет внутренних LLM-вызовов или тяжелых эвристических парсеров**. Скрипты выполняют чистый I/O, а весь анализ текста инцидента, сопоставление с каталогом услуг и формулирование ответа строго по шаблонам инженера Беликова Алена выполняет сам **AI-агент в диалоге AGY**.

---

## 🏗 Архитектура компонентов

```
helpdesk_agent/
├── helpdesk_tool.py     # Единая CLI-точка входа для выполнения команд из AGY (batch, task, apply, attachment...)
├── kb.py                # Векторный семантический поиск, FastEmbed, умный фильтр качества, автообучение
├── normalizer.py        # Нормализатор имен устройств (омоглифы, префиксы NTEMW, KZMK, ZTE, Левенштейн)
├── template_engine.py   # Движок адаптивных шаблонов и авто-подбора
├── templates.json       # Каталог корпоративных шаблонов поддержки
├── diagnostics.py       # Асинхронный ICMP-пинг, DNS-резолвинг, проверка порта SMB 445 и WinRM 5985
├── intraservice_api.py  # Асинхронный клиент к REST API IntraService (Basic Auth, кастомные поля, вложения)
├── knowledge_base.json  # Локальный векторный кэш базы знаний
├── GEMINI.md            # Системная персона, регламент Human-in-the-Loop и шаблоны
├── pyproject.toml       # Зависимости проекта uv (aiohttp, asyncpg, pgvector, fastembed)
└── README.md            # Справочник для разработчика
```

---

## 🛠 Описание модулей

### 1. `intraservice_api.py` (API Клиент)
- **Аутентификация:** `Basic Auth` (Base64 `login:password`). По умолчанию используется сервисный аккаунт `IntraService_dev`.
- **Методы:**
  - `get_service_catalog()` — дерево услуг (`GET /api/service?for=createtask&pagesize=1000`).
  - `get_tasks_by_filter(filter_id, page, page_size)` — выборка задач по системному фильтру (например, `984` — 1-я линия).
  - `get_tasks_by_status(status_ids, page, page_size)` — выборка по статусам (`29` — Выполнена, `30` — Отменена).
  - `get_task_details(task_id)` — получение полей задачи и распарсенных кастомных полей (`_parsed_fields`, `_field_meta`).
  - `get_task_history(task_id)` — получение истории действий и комментариев инженеров (`GET /api/tasklifetime?taskid=...`).
  - `add_comment(task_id, comment, is_private=False)` — добавление комментария (`PUT /api/task/{id}`).
  - `update_status(task_id, status_id)` — смена статуса заявки.
  - `add_expenses(task_id, minutes)` — списание трудозатрат (`POST /api/taskexpenses`).
  - `download_attachment_file(task_id, file_id, save_path)` — скачивание вложений (скриншоты ошибок).

### 2. `normalizer.py` (Нормализатор устройств)
- Портирован из микросервиса `printer-worker`.
- Транслитерация кириллических омоглифов (`ОСАЕРХМТКВ` $\rightarrow$ `OCAEPXMTKB`).
- Распознавание корпоративных префиксов (`NTEMW`, `TKT`, `TNT`, `KMK`, `TNM`, `TEMPO`, `WKS`, `PC`, `SRV`, `NOTE`, `LAPTOP`, `COMP`, `DESKTOP`, `ZTE`, `NTZ`, `KZMK`).
- Исправление опечаток в префиксах по расстоянию Левенштейна.
- Жесткая фильтрация названий предприятий и доменов (`NTZ-TEMPO`, `TEMPO`).

### 3. `diagnostics.py` (Сетевая диагностика)
- Извлечение хостов: поиск по префиксам и IP-адресам, игнорирование email-адресов и чистых чисел (телефонов/комнат).
- Комплексный статус: если пинг заблокирован брандмауэром, но порт `SMB 445` или `WinRM 5985` открыт, хост справедливо считается находящимся онлайн.
- Форматирует статус: `🟢 В СЕТИ: NTEMW0047 [10.245.9.14] RTT: 1ms | SMB:✓`.

### 4. `template_engine.py` & `templates.json` (Адаптивные шаблоны)
- Интеллектуальный подбор шаблона:
  - `1c_issue` — при сбоях 1С (очистка кэша, заблокированные сессии).
  - `hardware_repair` — при аппаратных сбоях (принести системный блок в каб. 112, статус `48`).
  - `pc_offline` — при выключенном ПК (чек-лист кабеля, статус `35`).
  - `printer_issue` — при проблемах с печатью (очередь spooler, порт).
  - `wifi_access` — при запросе Wi-Fi WORK-NET (статус `29`).
  - `wrong_service` — при неверном разделе (статус `30`, перенаправление).

### 5. `kb.py` (RAG База знаний и FastEmbed)
- Локальная модель `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` через `FastEmbed` (ONNX runtime на CPU, размерность 384 dim, время отклика ~10 мс).
- Умный фильтр качества (`smart_filter_task`): отсекает пустые ответы («ок», «сделано») и сохраняет полезный опыт.
- Непрерывное автообучение при каждом закрытии заявки через `apply`.

---

## ⚡ Справочник команд `helpdesk_tool.py`

| Команда | Описание | Пример вызова |
|---|---|---|
| `batch` | Пакетный разбор стопки заявок со сводной матрицей | `uv run python helpdesk_tool.py batch --limit 5` |
| `task <ID>` | Карточка задачи + кастомные поля + RAG-контекст | `uv run python helpdesk_tool.py task 139022` |
| `diagnose <HOST>` | Сетевая проверка ПК / IP | `uv run python helpdesk_tool.py diagnose NTEMW0047` |
| `attachment <ID>` | Скачивание вложений заявки (скриншоты) | `uv run python helpdesk_tool.py attachment 139063` |
| `search-kb "<query>"` | Семантический поиск по решениям | `uv run python helpdesk_tool.py search-kb "сбой печати 1С"` |
| `sync-kb` | Умный сбор и индексация закрытых задач | `uv run python helpdesk_tool.py sync-kb --limit 50` |
| `catalog` | Поиск по дереву услуг | `uv run python helpdesk_tool.py catalog --search "Wi-Fi"` |
| `history <ID>` | Просмотр журнала изменений задачи | `uv run python helpdesk_tool.py history 139022` |
| `apply <ID>` | Применение решения (статус + комментарий) | `uv run python helpdesk_tool.py apply 139022 --status 27 --comment "..."` |
