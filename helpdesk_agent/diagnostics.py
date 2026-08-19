import asyncio
import logging
import os
import re
import socket
import subprocess
from typing import Any

logger = logging.getLogger("helpdesk_agent.diagnostics")

# Кириллические омоглифы -> латиница для имен ПК
CYRILLIC_HOMOGLYPHS = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "Х": "X", "а": "a", "е": "e", "о": "o", "р": "p",
    "с": "c", "у": "y", "х": "x",
}

# Регулярные выражения для поиска имен хостов и IP-адресов
PC_NAME_REGEX = re.compile(
    r"\b([A-Za-zА-Яа-я0-9\-_]*(?:TEMPO|ТЕМПО|WKS|ВКС|PC|РС|SRV|NOTE|LAPTOP|COMP|DESKTOP)[A-Za-zА-Яа-я0-9\-_]*)\b",
    re.IGNORECASE,
)
IP_REGEX = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)


def normalize_host_name(raw_host: str) -> str:
    """Транслитерирует кириллические омоглифы в латиницу для корректного DNS-резолвинга."""
    cleaned = raw_host.strip().strip(",;.()[]{}'\"")
    trans_chars = [CYRILLIC_HOMOGLYPHS.get(ch, ch) for ch in cleaned]
    return "".join(trans_chars).upper()


def extract_potential_hosts(
    text: str, custom_fields: dict[str, str] | None = None
) -> list[str]:
    """
    Извлекает потенциальные имена хостов и IP-адреса из текста заявки и кастомных полей.
    """
    hosts: list[str] = []
    seen: set[str] = set()

    def add_host(val: str):
        normalized = normalize_host_name(val)
        if not normalized or len(normalized) < 3:
            return
        # Исключаем чистые числа (телефоны/комнаты), номера телефонов формата XX-XX и общие стоп-слова
        if (
            normalized.isdigit()
            or re.match(r"^\d+[\-_]\d+$", normalized)
            or normalized.lower() in (
                "комп", "компьютер", "ноутбук", "пк", "wifi", "интернет", "helpdesk", "windows", "нет номера"
            )
        ):
            return
        lower = normalized.lower()
        if lower not in seen:
            seen.add(lower)
            hosts.append(normalized)

    # 1. Проверяем кастомные поля (наивысший приоритет)
    if custom_fields:
        for val in custom_fields.values():
            if not val:
                continue
            for m in PC_NAME_REGEX.findall(val):
                add_host(m)
            for m in IP_REGEX.findall(val):
                add_host(m)

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

        is_online = (proc.returncode == 0) and (
            "TTL=" in out_text.upper() or "BYTES=" in out_text.upper() or "ВРЕМЯ=" in out_text.upper() or "TIME=" in out_text.upper()
        )
        
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


async def check_tcp_port(host: str, port: int, timeout: float = 1.2) -> bool:
    """
    Проверяет доступность TCP-порта (SMB 445, WinRM 5985, RDP 3389).
    """
    try:
        conn = asyncio.open_connection(host, port)
        _, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def resolve_dns(host: str) -> str | None:
    """Резолвит DNS имя в IP-адрес."""
    try:
        loop = asyncio.get_running_loop()
        ip = await loop.run_in_executor(None, socket.gethostbyname, host)
        return ip
    except Exception:
        return None


async def run_host_diagnostics(target: str) -> dict[str, Any]:
    """
    Комплексная диагностика хоста: DNS -> ICMP Ping -> SMB 445 -> WinRM 5985.
    Комбинированная логика: если пинг заблокирован брандмауэром, но порт SMB/WinRM открыт,
    хост справедливо считается находящимся онлайн.
    """
    target = normalize_host_name(target)
    
    # 1. DNS Резолвинг
    ip = await resolve_dns(target) if not re.match(r"^\d+\.\d+\.\d+\.\d+$", target) else target

    # 2. Параллельный запуск ICMP Ping и проверок портов
    ping_task = async_ping(target, count=2)
    smb_task = check_tcp_port(target, 445)
    winrm_task = check_tcp_port(target, 5985)

    ping_res, smb_ok, winrm_ok = await asyncio.gather(ping_task, smb_task, winrm_task)

    # 3. Комплексный вывод статуса
    is_online = ping_res.get("is_online") or smb_ok or winrm_ok

    return {
        "target": target,
        "resolved_ip": ip,
        "is_online": is_online,
        "avg_rtt": ping_res.get("avg_rtt"),
        "icmp_ping_ok": ping_res.get("is_online", False),
        "smb_port_445": smb_ok,
        "winrm_port_5985": winrm_ok,
    }


def format_diagnostics_summary(diag: dict[str, Any]) -> str:
    """Форматирует результат диагностики в компактную визуальную строку."""
    target = diag.get("target", "Unknown")
    ip = diag.get("resolved_ip")
    is_online = diag.get("is_online", False)
    rtt = diag.get("avg_rtt")
    smb = "SMB:✓" if diag.get("smb_port_445") else "SMB:✗"

    if is_online:
        ip_info = f" [{ip}]" if ip and ip != target else ""
        rtt_info = f" RTT: {rtt}" if rtt else ""
        return f"🟢 В СЕТИ: {target}{ip_info}{rtt_info} | {smb}"
    else:
        dns_info = f" (DNS: {ip})" if ip else " (DNS не найден)"
        return f"🔴 НЕ В СЕТИ: {target}{dns_info} | ICMP и порты не отвечают"
