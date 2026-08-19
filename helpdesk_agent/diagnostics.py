import asyncio
import logging
import os
import re
import socket
import subprocess
from typing import Any

logger = logging.getLogger("helpdesk_agent.diagnostics")

# Регулярные выражения для поиска имен хостов и IP-адресов
PC_NAME_REGEX = re.compile(
    r"\b([A-Za-z0-9\-_]*(?:TEMPO|WKS|PC|SRV|NOTE|LAPTOP|COMP|DESKTOP)[A-Za-z0-9\-_]*)\b",
    re.IGNORECASE,
)
IP_REGEX = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)


def extract_potential_hosts(
    text: str, custom_fields: dict[str, str] | None = None
) -> list[str]:
    """
    Извлекает потенциальные имена хостов и IP-адреса из текста заявки и кастомных полей.
    """
    hosts: list[str] = []
    seen: set[str] = set()

    def add_host(val: str):
        cleaned = val.strip().strip(",;.()[]{}'\"")
        if not cleaned or len(cleaned) < 3 or cleaned.lower() in ("комп", "компьютер", "ноутбук", "пк", "wifi", "интернет", "helpdesk", "windows"):
            return
        lower = cleaned.lower()
        if lower not in seen:
            seen.add(lower)
            hosts.append(cleaned)

    # 1. Проверяем кастомные поля (наивысший приоритет)
    if custom_fields:
        for val in custom_fields.values():
            if not val:
                continue
            for m in PC_NAME_REGEX.findall(val):
                add_host(m)
            for m in IP_REGEX.findall(val):
                add_host(m)
            if re.match(r"^[A-Za-z0-9\-_]{3,25}$", val.strip()):
                add_host(val)

    # 2. Проверяем основной текст заявки
    if text:
        for m in PC_NAME_REGEX.findall(text):
            add_host(m)
        for m in IP_REGEX.findall(text):
            add_host(m)

    return hosts


async def async_ping(host: str, count: int = 2, timeout_sec: float = 1.5) -> dict[str, Any]:
    """
    Выполняет асинхронный ICMP-пинг хоста (адаптировано для Windows и Linux).
    """
    is_win = os.name == "nt"
    if is_win:
        timeout_ms = int(timeout_sec * 1000)
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(int(timeout_sec)), host]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec * count + 2.0)
        out_text = stdout.decode("cp866" if is_win else "utf-8", errors="ignore")

        is_online = (proc.returncode == 0) and ("TTL=" in out_text.upper() or "BYTES=" in out_text.upper() or "ВРЕМЯ=" in out_text.upper() or "TIME=" in out_text.upper())
        
        rtt_match = re.search(r"(?:Среднее|Average|avg)[ =]+([0-9\.]+)\s*ms", out_text, re.IGNORECASE)
        avg_rtt = f"{rtt_match.group(1)}ms" if rtt_match else None

        return {
            "host": host,
            "is_online": is_online,
            "avg_rtt": avg_rtt,
            "raw_output": out_text.strip(),
        }
    except asyncio.TimeoutError:
        return {"host": host, "is_online": False, "avg_rtt": None, "error": "Timeout"}
    except Exception as e:
        return {"host": host, "is_online": False, "avg_rtt": None, "error": str(e)}


async def check_tcp_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """
    Проверяет доступность TCP-порта (SMB 445, WinRM 5985, RDP 3389 и т.д.).
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def run_host_diagnostics(host: str) -> dict[str, Any]:
    """
    Выполняет комплексную сетевую диагностику целевого хоста.
    """
    resolved_ip = None
    try:
        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(host, None, family=socket.AF_INET)
        if addr_info and addr_info[0] and addr_info[0][4]:
            resolved_ip = addr_info[0][4][0]
    except Exception:
        pass

    target_for_ping = resolved_ip or host
    ping_res = await async_ping(target_for_ping)

    ports_to_check = {
        445: "SMB",
        5985: "WinRM",
        3389: "RDP",
    }
    open_ports = {}
    if ping_res["is_online"] or resolved_ip:
        tasks = [
            check_tcp_port(target_for_ping, port)
            for port in ports_to_check
        ]
        port_results = await asyncio.gather(*tasks, return_exceptions=True)
        for (port, name), is_open in zip(ports_to_check.items(), port_results):
            open_ports[name] = bool(is_open) if not isinstance(is_open, Exception) else False

    is_accessible = ping_res["is_online"] or any(open_ports.values())

    return {
        "host": host,
        "resolved_ip": resolved_ip,
        "is_online": is_accessible,
        "ping_ok": ping_res["is_online"],
        "avg_rtt": ping_res.get("avg_rtt"),
        "open_ports": open_ports,
    }


def format_diagnostics_summary(diag: dict[str, Any]) -> str:
    """
    Форматирует результат диагностики в компактную строку.
    """
    host = diag.get("host")
    ip = diag.get("resolved_ip") or "DNS не разрешен"
    is_online = diag.get("is_online", False)
    rtt = diag.get("avg_rtt") or "—"
    ports = diag.get("open_ports", {})
    
    ports_str = ", ".join(f"{name}: {'✓' if state else '✗'}" for name, state in ports.items()) if ports else "не проверялись"

    status_icon = "🟢 В СЕТИ" if is_online else "🔴 НЕ В СЕТИ"
    return f"{status_icon} [{host} -> {ip}] Ping: {rtt} | Порты ({ports_str})"
