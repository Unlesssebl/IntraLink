# Helpdesk Agent & Execution Hub

Интерактивный инженерный кокпит и инструменты автоматизации техподдержки в среде **Antigravity (AGY)**.

---

## 📌 Назначение и Концепция
`helpdesk_agent` — это набор инструментов прямого I/O, сетевой экспресс-диагностики, детерминированных правил триажа (Rule Engine), семантического поиска решений (FastEmbed + pgvector) и модулей прямого исполнения в корпоративной инфраструктуре (Active Directory, WinRM, SMB).

📖 **Справочник оперативных Workflow и слэш-команд:** [`WORKFLOWS.md`](WORKFLOWS.md)

> [!NOTE]
> **Принцип прямого взаимодействия:**
> Скрипты выполняют быстрый I/O, диагностику и исполнение действий в инфраструктуре. Анализ контекста, интерактивный диалог и согласование действий выполняются AI-ассистентом в интерфейсе AGY в режиме Human-in-the-Loop.

---

## 🏗 Ключевые компоненты

* **`helpdesk_tool.py`** — центральная CLI-точка входа для выполнения команд (`batch`, `task`, `apply`, `diag`, `screen`, `kb`, `sync`, `wlan`, `summary`).
* **`executors/`** — модули прямого исполнения в инфраструктуре:
  * `ad.py` — автоматизация Active Directory (выдача Wi-Fi `WLAN-WORKNET`, доменные группы, Single-DC Affinity).
  * `printers.py` — удаленная установка и диагностика принтеров через WinRM/SMB с WMI Bootstrap.
* **`rules/`** — детерминированный движок триажа (Rule Engine):
  * `redirect.py` — классификация неверно поданных заявок и расчет маршрута переноса.
  * `deduplication.py` / `file_locks.py` / `offline_host.py` / `credentials.py` — типовые правила обработки инцидентов.
* **`kb.py`** — семантический RAG-поиск по базе закрытых заявок (FastEmbed `paraphrase-multilingual-MiniLM-L12-v2` + PostgreSQL pgvector).
* **`diagnostics.py`** — сетевая экспресс-проверка хоста (ICMP Ping, DNS-резолвинг, порты SMB:445 и WinRM:5985).
* **`intraservice_api.py`** — асинхронный клиент к REST API IntraService (Basic Auth, кастомные поля, вложения, жизненный цикл).
* **`templates.json` / `template_engine.py`** — каталог корпоративных шаблонов ответов и расчет трудозатрат.
* **`llm/ollama_client.py`** — клиент локальной нейросети Ollama (Qwen2.5:1.5B) для моментальной суммаризации длинных переписок по инцидентам.

---

## ⚡ Быстрые команды CLI

| Команда | Описание | Пример вызова |
|---|---|---|
| `batch` | Пакетный разбор стопки заявок со сводной матрицей | `uv run python helpdesk_tool.py batch --limit 5` |
| `task <ID>` | Детальная карточка задачи + кастомные поля + RAG | `uv run python helpdesk_tool.py task 139022` |
| `diagnose <HOST>` | Сетевая проверка ПК / IP (Ping, DNS, SMB, WinRM) | `uv run python helpdesk_tool.py diagnose NTEMW0047` |
| `attachment <ID>` | Скачивание и анализ скриншотов из заявки | `uv run python helpdesk_tool.py attachment 139063` |
| `search-kb "<query>"` | Семантический RAG-поиск по базе решений | `uv run python helpdesk_tool.py search-kb "сбой печати 1С"` |
| `sync-kb` | Индексация закрытых задач в pgvector | `uv run python helpdesk_tool.py sync-kb --limit 50` |
| `catalog` | Поиск по дереву услуг каталога | `uv run python helpdesk_tool.py catalog --search "Wi-Fi"` |
| `summary <ID>` | Локальная AI-суммаризация истории инцидента | `uv run python helpdesk_tool.py summary 139022` |
| `apply <ID>` | Применение решения (статус + комментарий + списание времени) | `uv run python helpdesk_tool.py apply 139022 --status 29 --comment "..." --minutes 10` |
