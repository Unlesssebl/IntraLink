# Helpdesk Agent & Execution Node

Интерактивный инженерный кокпит и инструменты автоматизации техподдержки в среде **Antigravity (AGY)**.

---

## 📌 Назначение и Концепция
`helpdesk_agent` — это локальный CLI-клиент и Execution Node оператора техподдержки. Он выполняет быстрый I/O, сетевую экспресс-диагностику хостов и прямое исполнение действий в домене Windows (Active Directory, WinRM, SMB), делегируя всю работу с данными, правилами триажа и RAG в **Core API Gateway**.

---

## 🏗 Ключевые компоненты

* **`helpdesk_tool.py`** — центральная CLI-точка входа для выполнения команд.
* **`core_api_client.py`** — асинхронный HTTP REST-клиент к Core API Gateway (`X-Bot-Api-Key`).
* **`commands/`** — модульная структура команд CLI:
  * `triage.py` — `batch`, `redirect`, `duplicates`, `queue`
  * `tasks.py` — `task`, `apply`, `history`, `attachment`, `summary`
  * `identity.py` — `ad`, `wlan`, `create-user`
  * `kb.py` — `search-kb`, `sync-kb`, `check-db`, `start-db`
  * `catalog.py` — `catalog`, `services`
  * `session.py` — `skip`, `reset-session`
  * `diagnose.py` — экспресс-диагностика хостов
* **`executors/`** — модули прямого исполнения в инфраструктуре Windows (асинхронные обертки `asyncio.to_thread`):
  * `ad.py` — автоматизация Active Directory (выдача Wi-Fi `WLAN-WORKNET`, создание пользователей, доменные группы).
  * `printers.py` — удаленная установка и диагностика принтеров через WinRM/SMB с WMI Bootstrap.
* **`diagnostics.py`** — сетевая экспресс-проверка хоста (ICMP Ping, DNS-резолвинг, порты SMB:445 и WinRM:5985).
* **`llm/ollama_client.py`** — клиент локальной нейросети Ollama (Qwen2.5:1.5B) для моментальной суммаризации длинных переписок по инцидентам.

---

## ⚡ Быстрые команды CLI

| Команда | Описание | Пример вызова |
|---|---|---|
| `batch` | Пакетный разбор стопки заявок со сводной матрицей | `uv run python helpdesk_tool.py batch --limit 5` |
| `task <ID>` | Детальная карточка задачи + кастомные поля + RAG | `uv run python helpdesk_tool.py task 139022` |
| `diagnose <HOST>` | Сетевая проверка ПК / IP (Ping, DNS, SMB, WinRM) | `uv run python helpdesk_tool.py diagnose NTEMW0047` |
| `attachment <ID>` | Скачивание и анализ вложений из заявки | `uv run python helpdesk_tool.py attachment 139063` |
| `search-kb "<query>"` | Семантический RAG-поиск по базе решений через Core API | `uv run python helpdesk_tool.py search-kb "сбой печати 1С"` |
| `services` | Корневые разделы каталога услуг (01..16) | `uv run python helpdesk_tool.py services` |
| `wlan <ID>` | Автовыдача Wi-Fi в AD + закрытие тикета | `uv run python helpdesk_tool.py wlan 139088` |
| `create-user <ID>` | Создание пользователя в AD + закрытие тикета | `uv run python helpdesk_tool.py create-user 139090` |
| `apply <ID>` | Применение решения (статус + комментарий + списание времени) | `uv run python helpdesk_tool.py apply 139022 --status 29 --comment "..." --expenses 10` |
