# 🛠️ IntraService Helpdesk Tools (I/O & Diagnostics)

Набор легковесных инструментов ввода-вывода и сетевой диагностики для выполнения заявок в системе IntraService через AI-агента Antigravity (AGY).

Вся логика принятия решений, сопоставления шаблонов и анализ инцидентов выполняется **непосредственно AI-агентом в чате AGY** в соответствии с персональной ролью инженера технической поддержки **Беликова Алена** (ООО «АйТи ТЭМПО», тел. `49-87`, АБК-3, каб. 112).

---

## ⚡ Доступные команды (`helpdesk_tool.py`)

```bash
# Получить очередь открытых заявок 1-й линии:
uv run python helpdesk_tool.py queue --limit 10

# Детальная карточка инцидента с кастомными полями и комментариями:
uv run python helpdesk_tool.py task 139022

# Сетевая диагностика ПК заявителя (Ping, DNS, SMB):
uv run python helpdesk_tool.py diagnose TEMPO-PC01

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
├── helpdesk_tool.py     # CLI-инструмент ввода-вывода (queue, task, diagnose, apply, catalog, history)
├── diagnostics.py       # Быстрая сетевая диагностика (ICMP/DNS/SMB)
├── intraservice_api.py  # Асинхронный клиент к API IntraService
├── GEMINI.md            # Системная персона и шаблоны инженера Беликова Алена
├── pyproject.toml       # Минимальные зависимости (aiohttp, pydantic)
└── README.md            # Документация пакета
```
