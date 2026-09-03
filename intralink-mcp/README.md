# IntraLink FastMCP Hub (`intralink-mcp`)

Инструментальный шлюз Model Context Protocol (MCP) для AI-агента Antigravity (AGY).

Предоставляет полноценный набор агентских инструментов для автономного разбора очереди заявок IntraService, проведения сетевой экспресс-диагностики, семантического поиска в базе знаний pgvector и безопасного запуска действий через Command Bus с поддержкой Human-in-the-Loop (HitL).

---

## 🛠 Доступные инструменты (Tools)

| Инструмент | Описание | Эндпоинт Core API |
|---|---|---|
| `triage_batch` | Получение пачки заявок с авто-шаблонами, детекцией дубликатов и телеметрией хостов 0ms | `GET /api/v1/triage/batch` |
| `get_ticket_details` | Детальная карточка: переписка, вложения, телеметрия ПК, RAG-совпадения и AI-синтез | `GET /api/v1/triage/tasks/{task_id}` |
| `apply_triage_decision` | Атомарное применение решения к группе заявок (с защитой Dead Man's Switch) | `POST /api/v1/triage/apply` |
| `diagnose_host` | Экспресс-диагностика доступности рабочей станции (Ping, DNS, SMB:445, WinRM:5985) | `GET /api/v1/admin/diagnose-host` |
| `search_kb` | Векторный RAG-поиск в базе исторических решений (PostgreSQL + pgvector) | `POST /api/v1/triage/rag/search` |
| `submit_action_command` | Запуск целевого действия (установка принтера, Wi-Fi, AD) через Command Bus | `POST /api/v1/commands/submit` |
| `list_skills` | Просмотр каталога действий, действующих политик безопасности и Killswitch | `GET /api/v1/skills` |

---

## 🚀 Настройка в Antigravity IDE / Claude Desktop

В файле конфигурации MCP (`mcp_config.json`):

```json
{
  "mcpServers": {
    "intralink": {
      "command": "python",
      "args": [
        "-m",
        "intralink-mcp.server"
      ],
      "env": {
        "CORE_API_URL": "http://127.0.0.1:8000",
        "BOT_API_KEY": "<your-pre-shared-bot-api-key>"
      }
    }
  }
}
```
