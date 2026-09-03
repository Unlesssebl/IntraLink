"""
FastMCP / JSON-RPC 2.0 Сервер инструментов IntraLink для AI-агента Antigravity (AGY).
Предоставляет агентские инструменты триажа, карточки заявок, экспресс-диагностики хостов,
поиска в базе знаний (pgvector RAG) и запуска инфраструктурных действий через Command Bus.
"""

import asyncio
import json
import logging
import sys
from typing import Any

from client import CoreApiClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [INTRALINK-MCP] - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("intralink_mcp.server")

SERVER_INFO = {
    "name": "intralink-mcp",
    "version": "1.0.0",
}

TOOLS_REGISTRY = [
    {
        "name": "triage_batch",
        "description": "Получить пачку заявок из очереди 1-й линии IntraService с авто-шаблонами инженера, телеметрией хоста и детекцией дубликатов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 5, "description": "Размер пачки (по умолчанию 5)"},
                "filter_id": {"type": "integer", "default": 984, "description": "ID фильтра IntraService (по умолчанию 984)"},
                "service_prefix": {"type": "string", "description": "Фильтр по префиксу раздела (01..16) или названию"},
                "redirect_only": {"type": "boolean", "default": False, "description": "Выбрать только заявки, требующие отмены/редиректа"},
                "include_skipped": {"type": "boolean", "default": False, "description": "Включить пропущенные в смене"},
            },
        },
    },
    {
        "name": "get_ticket_details",
        "description": "Получить расширенную карточку заявки: текст проблемы, история переписки, вложения, экспресс-телеметрия ПК, RAG-похожие заявки и синтез решения.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID заявки IntraService"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "apply_triage_decision",
        "description": "Атомарно применить решение к одной или нескольким заявкам (статус 29, 30, 48, списание трудозатрат, авто-индексация в базу знаний) с защитой Dead Man's Switch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "integer"}, "description": "Список ID заявок"},
                "status_id": {"type": "integer", "description": "ID целевого статуса (29 - Выполнена, 30 - Отменена, 48 - Аппаратный ремонт, 27 - В работе)"},
                "comment": {"type": "string", "default": "", "description": "Текст комментария заявителю"},
                "expenses": {"type": "integer", "default": 0, "description": "Трудозатраты в минутах"},
                "dry_run": {"type": "boolean", "default": False, "description": "Режим симуляции (без внесения изменений в IntraService)"},
                "confirmed_by_human": {"type": "boolean", "default": False, "description": "Флаг ручного подтверждения оператора для обхода лимита Dead Man's Switch (>10 заявок/мин)"},
            },
            "required": ["task_ids", "status_id"],
        },
    },
    {
        "name": "diagnose_host",
        "description": "Сетевая экспресс-диагностика рабочей станции (ICMP Ping, DNS-резолв, порты SMB:445, WinRM:5985, WMI:135) за 400 мс.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Имя хоста (например, NTEMW0144) или IP-адрес"},
            },
            "required": ["host"],
        },
    },
    {
        "name": "search_kb",
        "description": "Семантический векторный RAG-поиск в базе исторических решений Helpdesk (PostgreSQL + pgvector).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Текст запроса или описание симптомов сбоя"},
                "limit": {"type": "integer", "default": 3, "description": "Максимальное число совпадений"},
                "threshold": {"type": "number", "default": 0.70, "description": "Порог косинусного расстояния (0.0 - точное совпадение, 1.0 - любое)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "submit_action_command",
        "description": "Поставить инфраструктурную задачу в единую шину исполнения (Command Bus / Redis Streams): установка принтера, доступ к Wi-Fi, создание пользователя AD.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "description": "Тип действия: install_printer | grant_wlan | create_user | reset_password | diagnose_host"},
                "target": {"type": "object", "description": "Целевой объект (pc_name, printer_name, identity, task_id, etc.)"},
                "params": {"type": "object", "default": {}, "description": "Дополнительные параметры выполнения"},
                "mode": {"type": "string", "default": "auto", "description": "Режим исполнения: auto (автономно) | confirm (HITL) | dry_run (симуляция)"},
            },
            "required": ["action_type", "target"],
        },
    },
    {
        "name": "list_skills",
        "description": "Получить каталог всех зарегистрированных действий/навыков системы с их действующими политиками безопасности и статусом Killswitch.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


class IntraLinkMCPServer:
    """JSON-RPC 2.0 сервер по протоколу Model Context Protocol (MCP)."""

    def __init__(self):
        self.api_client = CoreApiClient()

    async def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> str:
        """Диспетчер вызова инструментов MCP."""
        try:
            if name == "triage_batch":
                res = await self.api_client.get_triage_batch(
                    limit=arguments.get("limit", 5),
                    filter_id=arguments.get("filter_id", 984),
                    service_prefix=arguments.get("service_prefix"),
                    redirect_only=arguments.get("redirect_only", False),
                    include_skipped=arguments.get("include_skipped", False),
                )
                return json.dumps(res, ensure_ascii=False, indent=2)

            elif name == "get_ticket_details":
                res = await self.api_client.get_ticket_details(task_id=arguments["task_id"])
                return json.dumps(res, ensure_ascii=False, indent=2)

            elif name == "apply_triage_decision":
                res = await self.api_client.apply_triage_decision(
                    task_ids=arguments["task_ids"],
                    status_id=arguments["status_id"],
                    comment=arguments.get("comment", ""),
                    expenses=arguments.get("expenses", 0),
                    dry_run=arguments.get("dry_run", False),
                    confirmed_by_human=arguments.get("confirmed_by_human", False),
                )
                return json.dumps(res, ensure_ascii=False, indent=2)

            elif name == "diagnose_host":
                res = await self.api_client.diagnose_host(host=arguments["host"])
                return json.dumps(res, ensure_ascii=False, indent=2)

            elif name == "search_kb":
                res = await self.api_client.search_kb(
                    query=arguments["query"],
                    limit=arguments.get("limit", 3),
                    threshold=arguments.get("threshold", 0.70),
                )
                return json.dumps(res, ensure_ascii=False, indent=2)

            elif name == "submit_action_command":
                res = await self.api_client.submit_action_command(
                    action_type=arguments["action_type"],
                    target=arguments["target"],
                    params=arguments.get("params"),
                    mode=arguments.get("mode", "auto"),
                )
                return json.dumps(res, ensure_ascii=False, indent=2)

            elif name == "list_skills":
                res = await self.api_client.list_skills()
                return json.dumps(res, ensure_ascii=False, indent=2)

            else:
                raise ValueError(f"Неизвестный инструмент: '{name}'")

        except Exception as e:
            logger.exception("Ошибка при выполнении инструмента %s: %s", name, e)
            return json.dumps({"error": str(e), "tool": name}, ensure_ascii=False)

    async def process_message(self, request_raw: str) -> str | None:
        """Обработка одного JSON-RPC 2.0 сообщения."""
        if not request_raw.strip():
            return None

        try:
            req = json.loads(request_raw)
        except Exception as e:
            return json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            })

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        # 1. Initialize
        if method == "initialize":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": SERVER_INFO,
                },
            })

        # 2. Initialized notification (no response)
        elif method == "notifications/initialized":
            return None

        # 3. Ping
        elif method == "ping":
            return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {}})

        # 4. List tools
        elif method == "tools/list":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS_REGISTRY},
            })

        # 5. Call tool
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            res_text = await self.handle_tool_call(tool_name, arguments)
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": res_text}],
                    "isError": False,
                },
            })

        else:
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method '{method}' not found"},
            })

    async def run_stdio(self) -> None:
        """Основной цикл прослушивания stdin / stdout."""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        logger.info("IntraLink MCP Server запущен и ожидает JSON-RPC запросов на stdin...")

        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8")
            resp_str = await self.process_message(line)
            if resp_str:
                sys.stdout.write(resp_str + "\n")
                sys.stdout.flush()


def main():
    server = IntraLinkMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
