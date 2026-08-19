# 🤖 IntraService Helpdesk Tools for Antigravity AI Agent

Компактный и надежный инструментарий для автономного выполнения и ведения заявок в системе IntraService прямо из среды **Antigravity (AGY)** в режиме **Human-in-the-Loop**.

Агент работает от лица инженера технической поддержки **Беликова Алена** (ООО «АйТи ТЭМПО», тел. `49-87`, АБК-3, каб. 112).

---

## ⚡ Быстрые команды (`helpdesk_tool.py`)

Все операции выполняются через единую утилиту:

```bash
# Получить очередь открытых заявок 1-й линии:
uv run python helpdesk_tool.py queue --limit 10

# Детальная карточка инцидента с кастомными полями и комментариями:
uv run python helpdesk_tool.py task 139022

# Сетевая диагностика ПК заявителя (Ping, DNS, порты SMB/WinRM/RDP):
uv run python helpdesk_tool.py diagnose TEMPO-PC01

# Сформировать проект решения ИИ:
uv run python helpdesk_tool.py suggest 139022

# Применить изменения в IntraService (после подтверждения оператором):
uv run python helpdesk_tool.py apply 139022 --status 27 --comment "Ваша заявка принята в работу. По вопросам звоните на номер 49-87." --expenses 15

# Поиск по дереву услуг:
uv run python helpdesk_tool.py catalog --search "Компьютеры"
```

---

## 📂 Структура пакета

```
helpdesk_agent/
├── helpdesk_tool.py     # CLI-инструмент для выполнения команд из AGY
├── agent_brain.py       # Экспертные правила и аналитика
├── diagnostics.py       # Быстрая сетевая диагностика (ICMP/DNS/SMB)
├── intraservice_api.py  # Асинхронный клиент к API IntraService
├── GEMINI.md            # Системная роль и правила коммуникации
├── pyproject.toml       # Минимальные зависимости (aiohttp, pydantic)
└── README.md            # Документация
```
