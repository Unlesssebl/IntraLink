# Helpdesk Agent (AGY-Native Tools & RAG Engine)

Техническая документация для разработчиков и AI-агентов по микромодулю `helpdesk_agent`.

---

## 📌 Назначение и Концепция
`helpdesk_agent` — это легковесный, высокоскоростной набор I/O-инструментов, сетевой диагностики и семантического поиска (RAG), предназначенный для выполнения задач технической поддержки в IntraService **напрямую из среды Antigravity (AGY)**.

> [!IMPORTANT]
> **Принцип единого интеллекта (No Second Brain):**
> Внутри скриптов `helpdesk_agent` **нет внутренних LLM-вызовов или тяжелых эвристических парсеров**. Скрипты выполняют чистый I/O, а весь анализ текста инцидента, сопоставление с каталогом услуг и формулирование ответа строго по шаблонам инженера Беликова Алена выполняет сам **AI-агент в диалоге AGY**.

---

## 🏗 Архитектура компонентов

```
helpdesk_agent/
├── helpdesk_tool.py     # Единая CLI-точка входа для выполнения команд из AGY
├── kb.py                # Векторный семантический поиск, FastEmbed, умный фильтр качества
├── diagnostics.py       # Асинхронный ICMP-пинг, DNS-резолвинг, проверка порта SMB 445
├── intraservice_api.py  # Асинхронный клиент к REST API IntraService (Basic Auth)
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
  - `get_task_details(task_id)` — получение полей задачи и распарсенных кастомных полей (`_parsed_fields`).
  - `get_task_history(task_id)` — получение истории действий и комментариев инженеров (`GET /api/tasklifetime?taskid=...`).
  - `add_comment(task_id, comment, is_private=False)` — добавление комментария (`PUT /api/task/{id}`).
  - `update_status(task_id, status_id)` — смена статуса заявки.
  - `add_expenses(task_id, minutes)` — списание трудозатрат (`POST /api/taskexpenses`).

### 2. `diagnostics.py` (Сетевая диагностика)
- **Извлечение хостов:** Регулярные выражения для поиска ПК (`TEMPO-...`, `WKS-...`, `PC-...`, `DESKTOP-...`) и IPv4-адресов в описании и кастомных полях.
- **Проверки:**
  - `async_ping(host)` — асинхронный ICMP пинг (нативно для Windows и Linux через `subprocess`).
  - `run_host_diagnostics(host)` — DNS-резолвинг + Ping + проверка открытого порта `SMB 445`.
  - Форматирует статус: `🟢 В СЕТИ [TEMPO-PC01 -> 10.245.X.X] Ping: 1ms | Порты (SMB: ✓)`.

### 3. `kb.py` (RAG База знаний и FastEmbed)
- **Векторизация:** Локальная модель `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` через `FastEmbed` (ONNX runtime на CPU, размерность 384 dim, время отклика ~10 мс).
- **Умный фильтр качества (`smart_filter_task`):**
  - Исключает тривиальные пустые ответы («ок», «сделано», «готово», «++»).
  - Отбирает развернутые инструкции специалистов для статуса `29`.
  - Извлекает правила корректного перенаправления для отмененных заявок статуса `30`.
- **Семантический поиск (`search_knowledge_base`):**
  - Вычисляет косинусное сходство векторов $\text{sim}(A, B) = \frac{A \cdot B}{\|A\| \|B\|}$.
  - Возвращает топ-3 релевантных исторических решения с процентом сходства.
- **Хранение данных:** Локальный кэш `knowledge_base.json` с автосохранением и возможностью синхронизации с PostgreSQL `task_knowledge_base`.

---

## ⚡ Справочник команд `helpdesk_tool.py`

| Команда | Описание | Пример вызова |
|---|---|---|
| `queue` | Просмотр очереди заявок | `uv run python helpdesk_tool.py queue --limit 10` |
| `task <ID>` | Карточка задачи + кастомные поля + RAG-контекст | `uv run python helpdesk_tool.py task 139022` |
| `diagnose <HOST>` | Сетевая проверка ПК / IP | `uv run python helpdesk_tool.py diagnose TEMPO-PC01` |
| `search-kb "<query>"` | Семантический поиск по решениям | `uv run python helpdesk_tool.py search-kb "сбой печати 1С"` |
| `sync-kb` | Умный сбор и индексация закрытых задач | `uv run python helpdesk_tool.py sync-kb --limit 50` |
| `catalog` | Поиск по дереву услуг | `uv run python helpdesk_tool.py catalog --search "Wi-Fi"` |
| `history <ID>` | Просмотр журнала изменений задачи | `uv run python helpdesk_tool.py history 139022` |
| `apply <ID>` | Применение решения (статус + комментарий) | `uv run python helpdesk_tool.py apply 139022 --status 27 --comment "..."` |

---

## 🛡️ Правила безопасности и регламент
1. **Human-in-the-Loop:** Любые мутирующие операции в IntraService (команда `apply`) выполняются **только после явного подтверждения оператором** в чате.
2. **Изоляция учетных данных:** Пароли передаются через переменные окружения (`.env`), которые исключены из репозитория через `.gitignore`.
3. **Fail-Safe Fallback:** При недоступности внешней сети или PostgreSQL модуль RAG мгновенно переключается на локальный `FastEmbed` и локальный JSON-кэш без сбоев.
