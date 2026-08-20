# 🛠️ IntraService Helpdesk Tools (I/O, Diagnostics & RAG)

Набор легковесных инструментов ввода-вывода, сетевой диагностики и семантического RAG-поиска для выполнения заявок в системе IntraService через AI-агента Antigravity (AGY).

Вся логика принятия решений, сопоставления шаблонов и анализ инцидентов выполняется **непосредственно AI-агентом в чате AGY** в соответствии с персональной ролью инженера технической поддержки **Беликова Алена** (ООО «АйТи ТЭМПО», тел. `49-87`, АБК-3, каб. 112).

---

## ⚡ Доступные команды (`helpdesk_tool.py`)

```bash
# Пакетный триаж стопки заявок 1-й линии:
uv run python helpdesk_tool.py batch --limit 5

# Пакетный триаж по конкретному номеру сервиса (например 2 - ПО, 3 - Оргтехника, 6 - 1С):
uv run python helpdesk_tool.py batch --service 2 --limit 5
uv run python helpdesk_tool.py batch --service 03 --limit 5
uv run python helpdesk_tool.py batch --service 6 --limit 5

# Список корневых разделов каталога с номерами (01..16):
uv run python helpdesk_tool.py services

# Получить очередь открытых заявок 1-й линии (общая или по сервису):
uv run python helpdesk_tool.py queue --limit 10
uv run python helpdesk_tool.py queue --service 2 --limit 10

# Детальная карточка инцидента с кастомными полями и комментариями:
uv run python helpdesk_tool.py task 139022

# Сетевая диагностика ПК заявителя (Ping, DNS, SMB):
uv run python helpdesk_tool.py diagnose TEMPO-PC01

# Семантический поиск по базе знаний RAG:
uv run python helpdesk_tool.py search-kb "ошибка при входе в 1С"

# Умная синхронизация закрытых заявок в базу знаний:
uv run python helpdesk_tool.py sync-kb --limit 50

# Поиск и пакетная отмена заявок на редирект:
uv run python helpdesk_tool.py redirect --limit 5
uv run python helpdesk_tool.py redirect --service 2 --limit 10

# Пропуск заявки в текущей смене (не показывать в батчах):
uv run python helpdesk_tool.py skip 139022,139023

# Сброс кэша сессии (вернуть все пропущенные заявки):
uv run python helpdesk_tool.py reset-session

# Применить изменения в IntraService (после подтверждения оператором):
uv run python helpdesk_tool.py apply 139022 --status 27 --comment "Ваша заявка принята в работу. По вопросам звоните на номер 49-87." --expenses 15

# Поиск по каталогу услуг:
uv run python helpdesk_tool.py catalog --search "Компьютеры"

# История изменений заявки:
uv run python helpdesk_tool.py history 139022
```

---

## 📂 Структура пакета

```
helpdesk_agent/
├── helpdesk_tool.py     # CLI-инструмент ввода-вывода (batch, redirect, task, skip, reset-session, apply)
├── session_state.py     # Сессионная память пропущенных и обработанных тикетов
├── template_engine.py   # Генератор шаблонов и семантический RAG-консенсус
├── kb.py                # Семантический поиск RAG, pgvector, Quality Gate
├── diagnostics.py       # Fail-Fast сетевая диагностика (ICMP/DNS/SMB) с TTL-кэшем
├── intraservice_api.py  # Асинхронный клиент к API IntraService с Retry
├── GEMINI.md            # Системная персона и шаблоны инженера Беликова Алена
├── pyproject.toml       # Конфигурация uv пакета
└── README.md            # Документация пакета
```
