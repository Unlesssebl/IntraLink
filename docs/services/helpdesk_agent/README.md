# Helpdesk Agent (AGY-Native Tools)

Комплекс инструментов ввода-вывода и сетевой диагностики для обработки обращений в IntraService непосредственно через AI-ассистента в среде Antigravity (AGY).

---

## 📌 Назначение
Сервис предоставляет быстрый и легкий интерфейс I/O к IntraService API. Вся интеллектуальная обработка обращений, маршрутизация и генерация ответов выполняется непосредственно моделью Antigravity (AGY) в стиле инженера технической поддержки (Беликов Ален, ООО «АйТи ТЭМПО», тел. 49-87, АБК-3, каб. 112).

---

## 🛠 Компоненты

1. **`helpdesk_agent/helpdesk_tool.py`**:
   - Единая точка вызова для получения очередей (`queue`), карточек инцидентов (`task`), сетевой диагностики (`diagnose`), истории (`history`), каталога (`catalog`) и применения решений (`apply`).
2. **`helpdesk_agent/diagnostics.py`**:
   - Асинхронный ICMP-пинг, DNS-резолвинг и проверка доступности сетевого порта SMB 445.
3. **`helpdesk_agent/intraservice_api.py`**:
   - Асинхронный клиент к REST API IntraService (заявки, кастомные поля, история, списание трудозатрат).
4. **`.agents/skills/intraservice-helpdesk/SKILL.md`**:
   - Навык Antigravity для выполнения инцидентов в режиме Human-in-the-Loop.

---

## 📖 Команды CLI

```bash
# Просмотр очереди заявок 1-й линии:
uv run python helpdesk_tool.py queue --limit 10

# Детальный просмотр заявки:
uv run python helpdesk_tool.py task <ID>

# Сетевая диагностика ПК заявителя:
uv run python helpdesk_tool.py diagnose <IP_ИЛИ_ИМЯ_ПК>

# Применение решения (после одобрения):
uv run python helpdesk_tool.py apply <ID> --status <STATUS_ID> --comment "<ТЕКСТ>" [--expenses <МИНУТЫ>]
```
