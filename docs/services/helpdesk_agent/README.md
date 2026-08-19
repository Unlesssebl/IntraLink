# Helpdesk Agent (AGY-Native Tools)

Комплекс инструментов и навыков для обработки обращений в Helpdesk-системе IntraService непосредственно через AI-ассистента в среде Antigravity (AGY).

---

## 📌 Назначение
Сервис предоставляет легковесный интерфейс взаимодействия с IntraService API от лица инженера технической поддержки (Беликов Ален, ООО «АйТи ТЭМПО», тел. 49-87, АБК-3, каб. 112).

---

## 🛠 Компоненты

1. **`helpdesk_agent/helpdesk_tool.py`**:
   - Единая точка вызова для получения очередей, карточек инцидентов, сетевой диагностики и применения решений.
2. **`helpdesk_agent/diagnostics.py`**:
   - Асинхронный ICMP-пинг, DNS-резолвинг и проверка доступности сетевых портов (SMB 445).
3. **`helpdesk_agent/intraservice_api.py`**:
   - Асинхронный клиент к REST API IntraService (заявки, кастомные поля, история, списание трудозатрат).
4. **`helpdesk_agent/agent_brain.py`**:
   - Модуль сопоставления шаблонов ответов, валидации официального дерева услуг и экспертных эвристик.
5. **`.agents/skills/intraservice-helpdesk/SKILL.md`**:
   - Навык Antigravity для автоматической обработки инцидентов в режиме Human-in-the-Loop.

---

## 📖 Команды CLI

```bash
# Просмотр очереди заявок 1-й линии:
uv run python helpdesk_tool.py queue --limit 10

# Детальный просмотр заявки:
uv run python helpdesk_tool.py task <ID>

# Сетевая диагностика ПК заявителя:
uv run python helpdesk_tool.py diagnose <IP_ИЛИ_ИМЯ_ПК>

# Формирование проекта решения:
uv run python helpdesk_tool.py suggest <ID>

# Применение решения (после одобрения):
uv run python helpdesk_tool.py apply <ID> --status <STATUS_ID> --comment "<ТЕКСТ>" [--expenses <МИНУТЫ>]
```
