# 🤖 Helpdesk CLI (`helpdesk-cli`) — AGY Tooling SDK

Машинный инструментарий и SDK **исключительно для AI-агента Antigravity (AGY)**.

---

## 📌 Назначение и Концепция

> [!IMPORTANT]
> **Пакет спроектирован как Machine-Actionable Tooling для AI-агента:**
> Инженеры и пользователи взаимодействуют с системой через диалог с AI-агентом, Web SPA `/admin` или Telegram-бот.
> Пакет `helpdesk-cli` вызывается агентом AGY при исполнении слэш-команд (`/triage`, `/task`, `/diag`, `/screen`, `/kb`, `/sync`, `/redirect`) и специализированных навыков (`.agents/skills/`).

Сервис полностью абстрагирован от прямого доступа к базе данных или внешним сервисам — все вызовы проксируются через **Core API Gateway** по HTTP REST.

---

## 🏗 Архитектура компонентов

* **[`helpdesk.py`](../../../helpdesk-cli/helpdesk.py)** — единая CLI-точка входа для вызова команд.
* **[`GEMINI.md`](../../../helpdesk-cli/GEMINI.md)** — системный контекст Helpdesk-оператора для AGY CLI (персона инженера, guardrails).
* **[`core_api_client.py`](../../../helpdesk-cli/core_api_client.py)** — асинхронный HTTP REST-клиент к Core API Gateway (`X-Bot-Api-Key`).
* **`commands/`** — модульные обработчики:
  * `triage.py` — пакетный разбор очереди (`batch`), отмена дубликатов (`duplicates`), редиректы каталога (`redirect`);
  * `tasks.py` — карточка заявки (`task`), применение решений (`apply`), вложения (`attachment`);
  * `kb.py` — семантический RAG-поиск (`search-kb`), синхронизация базы (`sync-kb`);
  * `diagnose.py` — сетевая экспресс-диагностика (делегируется в `shared.diagnostics`);
  * `identity.py` — операции с учетными записями (`ad`, `wlan`, `create-user`).

---

## 💡 Вызов команд (Self-Documenting)

Все доступные флаги и параметры документированы прямо в CLI:

```bash
# Справка по всем доступным командам
uv run python helpdesk-cli/helpdesk.py --help

# Справка по конкретной команде
uv run python helpdesk-cli/helpdesk.py batch --help
uv run python helpdesk-cli/helpdesk.py apply --help
```

---

## 📖 Справочник Workflows

Сценарии диалогового взаимодействия инженера с AI-агентом зафиксированы в:
👉 **[`WORKFLOWS.md`](WORKFLOWS.md)**
