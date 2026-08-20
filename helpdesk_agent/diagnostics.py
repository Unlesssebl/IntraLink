import asyncio
import logging
import os
import re
import socket
import subprocess
from typing import Any
from normalizer import normalize_pc_name, is_valid_pc_name, KNOWN_PC_PREFIXES

import time

logger = logging.getLogger("helpdesk_agent.diagnostics")

# In-Memory TTL-кэш диагностики хостов (host -> (timestamp, result))
_DIAG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 180.0  # 3 минуты

IP_REGEX = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

# Паттерн поиска имен ПК по известным префиксам
PC_PREFIX_PATTERN = re.compile(
    rf"\b(?:{'|'.join(re.escape(p) for p in KNOWN_PC_PREFIXES)})[A-Za-zА-Яа-я0-9\-_]*\b",
    re.IGNORECASE,
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
        if not val or "@" in val:
            return
        cleaned = val.strip()
        if IP_REGEX.match(cleaned):
            if cleaned not in seen:
                seen.add(cleaned)
                hosts.append(cleaned)
            return

        normalized = normalize_pc_name(cleaned)
        if not normalized or not is_valid_pc_name(normalized):
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
            # Если поле целиком является валидным именем ПК (например NTEMW0047)
            if is_valid_pc_name(val):
                add_host(val)
            for m in PC_PREFIX_PATTERN.findall(val):
                add_host(m)
            for m in IP_REGEX.findall(val):
                add_host(m)

    # 2. Проверяем основной текст заявки (предварительно удалив email-адреса)
    if text:
        clean_text = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "", text)
        for m in PC_PREFIX_PATTERN.findall(clean_text):
            add_host(m)
        for m in IP_REGEX.findall(clean_text):
            add_host(m)

    return hosts


async def async_ping(host: str, count: int = 1, timeout_sec: float = 0.8) -> dict[str, Any]:
    """
    Выполняет асинхронный ICMP-пинг хоста в режиме Fail-Fast (адаптировано для Windows и Linux).
    """
    is_win = os.name == "nt"
    if is_win:
        timeout_ms = int(timeout_sec * 1000)
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(max(1, int(timeout_sec))), host]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec * count + 1.0)
        out_text = stdout.decode("cp866" if is_win else "utf-8", errors="ignore")

        is_online = (proc.returncode == 0) and (
            "TTL=" in out_text.upper() or "BYTES=" in out_text.upper() or "ВРЕМЯ=" in out_text.upper() or "TIME=" in out_text.upper()
        )
        
        rtt_match = re.search(r"(?:Среднее|Average|avg)[ =]+([0-9\.]+)\s*ms", out_text, re.IGNORECASE)
        if not rtt_match:
            rtt_match = re.search(r"(?:время|time)[<=]([0-9\.]+)\s*ms", out_text, re.IGNORECASE)
        avg_rtt = f"{rtt_match.group(1)}ms" if rtt_match else ("0ms" if is_online else None)

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


async def check_tcp_port(host: str, port: int, timeout: float = 0.8) -> bool:
    """
    Проверяет доступность TCP-порта в режиме Fail-Fast (SMB 445, WinRM 5985, RDP 3389).
    """
    try:
        conn = asyncio.open_connection(host, port)
        _, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def resolve_dns(host: str, timeout: float = 0.8) -> str | None:
    """Резолвит DNS имя в IP-адрес с таймаутом."""
    try:
        loop = asyncio.get_running_loop()
        ip = await asyncio.wait_for(loop.run_in_executor(None, socket.gethostbyname, host), timeout=timeout)
        return ip
    except Exception:
        return None


async def run_host_diagnostics(target: str, use_cache: bool = True) -> dict[str, Any]:
    """
    Комплексная диагностика хоста: DNS -> ICMP Ping -> SMB 445 -> WinRM 5985 с TTL-кэшированием.
    """
    normalized_target = normalize_pc_name(target) or target.strip().upper()
    now = time.time()

    # Проверка TTL-кэша
    if use_cache and normalized_target in _DIAG_CACHE:
        cached_time, cached_res = _DIAG_CACHE[normalized_target]
        if now - cached_time < _CACHE_TTL_SEC:
            return cached_res
    
    # 1. DNS Резолвинг
    ip = await resolve_dns(normalized_target) if not re.match(r"^\d+\.\d+\.\d+\.\d+$", normalized_target) else normalized_target

    # 2. Параллельный запуск ICMP Ping и проверок портов (Fail-Fast: 0.8 сек)
    ping_task = async_ping(normalized_target, count=1, timeout_sec=0.8)
    smb_task = check_tcp_port(normalized_target, 445, timeout=0.8)
    winrm_task = check_tcp_port(normalized_target, 5985, timeout=0.8)

    ping_res, smb_ok, winrm_ok = await asyncio.gather(ping_task, smb_task, winrm_task)

    # 3. Комплексный вывод статуса
    is_online = ping_res.get("is_online") or smb_ok or winrm_ok

    result = {
        "target": normalized_target,
        "resolved_ip": ip,
        "is_online": is_online,
        "avg_rtt": ping_res.get("avg_rtt"),
        "icmp_ping_ok": ping_res.get("is_online", False),
        "smb_port_445": smb_ok,
        "winrm_port_5985": winrm_ok,
    }

    # Сохраняем в TTL-кэш
    _DIAG_CACHE[normalized_target] = (now, result)
    return result


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
