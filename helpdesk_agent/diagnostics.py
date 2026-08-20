import asyncio
import logging
import os
import re
import socket
import subprocess
import time
from typing import Any
from normalizer import (
    normalize_pc_name,
    is_valid_pc_name,
    resolve_pc_candidates,
    normalize_printer_address,
    KNOWN_PC_PREFIXES,
)

logger = logging.getLogger("helpdesk_agent.diagnostics")

# In-Memory TTL-кэш диагностики хостов (host -> (timestamp, result))
_DIAG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 180.0  # 3 минуты

IP_REGEX = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

# Паттерн поиска устройств: любые буквенно-цифровые комбинации с возможными пробелами/дефисами
DEVICE_PATTERN = re.compile(
    r"\b([A-Za-zА-Яа-я\-_]{2,7})[\s\-_]*(\d{2,5})\b",
    re.IGNORECASE,
)


KNOWN_PHONE_FIELD_IDS = {"1088", "1144", "1207", "1077", "1096", "1188", "1209"}


def extract_potential_hosts(
    text: str,
    custom_fields: dict[str, str] | None = None,
    company: str = "",
    dept: str = "",
) -> list[str]:
    """
    Интеллектуальное извлечение хостнеймов и IP из кастомных полей и текста:
    1. Явные валидные имена ПК (NTEMW1434, KMK0122, TKT0001, KPK0011).
    2. IP-адреса.
    3. Токены устройств из текста (DEVICE_PATTERN).
    4. Fallback-кандидаты по номеру (только если явное имя ПК не найдено).
    """
    explicit_hosts: list[str] = []
    digit_candidates: list[str] = []
    seen: set[str] = set()

    def add_explicit(val: str):
        if not val or "@" in val:
            return
        cleaned = val.strip()
        if IP_REGEX.match(cleaned):
            if cleaned not in seen:
                seen.add(cleaned)
                explicit_hosts.append(cleaned)
            return

        normalized = normalize_pc_name(cleaned)
        if normalized and is_valid_pc_name(normalized):
            lower = normalized.lower()
            if lower not in seen:
                seen.add(lower)
                explicit_hosts.append(normalized)

    # 1. Проверяем кастомные поля на явные имена ПК и IP
    if custom_fields:
        for fid, val in custom_fields.items():
            if not val:
                continue
            cleaned = val.strip()
            if is_valid_pc_name(cleaned):
                add_explicit(cleaned)
            for m in DEVICE_PATTERN.finditer(val):
                add_explicit(m.group(0))
            for m in IP_REGEX.findall(val):
                add_explicit(m)

            # Сохраняем возможные номера ПК только из не-телефонных полей
            if str(fid) not in KNOWN_PHONE_FIELD_IDS and cleaned.isdigit() and 2 <= len(cleaned) <= 5:
                cands = resolve_pc_candidates(cleaned, company=company, dept=dept)
                for c in cands:
                    if c.lower() not in seen:
                        digit_candidates.append(c)

    # 2. Проверяем основной текст заявки
    if text:
        clean_text = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "", text)
        for m in DEVICE_PATTERN.finditer(clean_text):
            add_explicit(m.group(0))
        for m in IP_REGEX.findall(clean_text):
            add_explicit(m)

    # Если есть хотя бы одно явное имя ПК, возвращаем его
    if explicit_hosts:
        return explicit_hosts

    # Иначе возвращаем подобранные кандидаты
    return digit_candidates


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

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec * count + 1.5)
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
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return {"host": host, "is_online": False, "avg_rtt": None, "error": "Timeout"}
    except Exception as e:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return {"host": host, "is_online": False, "avg_rtt": None, "error": str(e)}


async def check_tcp_port(host: str, port: int, timeout: float = 0.8) -> bool:
    """
    Проверяет доступность TCP-порта в режиме Fail-Fast (SMB 445, WinRM 5985).
    """
    try:
        conn = asyncio.open_connection(host, port)
        _, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
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


async def run_single_host_diag(target: str) -> dict[str, Any]:
    """Диагностика одиночного хоста: DNS -> ICMP -> SMB 445 -> WinRM 5985."""
    ip = await resolve_dns(target) if not re.match(r"^\d+\.\d+\.\d+\.\d+$", target) else target
    ping_task = async_ping(target, count=1, timeout_sec=0.8)
    smb_task = check_tcp_port(target, 445, timeout=0.8)
    winrm_task = check_tcp_port(target, 5985, timeout=0.8)

    ping_res, smb_ok, winrm_ok = await asyncio.gather(ping_task, smb_task, winrm_task)
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


async def run_host_diagnostics(target: str, use_cache: bool = True, fallback_candidates: list[str] | None = None) -> dict[str, Any]:
    """
    Комплексная диагностика хоста с поддержкой алиасов и TTL-кэшированием.
    Если основной target оффлайн и переданы кандидаты, проверяет кандидатов.
    """
    normalized_target = normalize_pc_name(target) or target.strip().upper()
    now = time.time()

    if use_cache and normalized_target in _DIAG_CACHE:
        cached_time, cached_res = _DIAG_CACHE[normalized_target]
        if now - cached_time < _CACHE_TTL_SEC:
            return cached_res

    res = await run_single_host_diag(normalized_target)

    # Если оффлайн и есть fallback кандидаты (например, при подборе префикса KZMK1561 vs NTEMW1561)
    if not res["is_online"] and fallback_candidates:
        other_cands = [c for c in fallback_candidates if c != normalized_target]
        for alt_host in other_cands:
            alt_res = await run_single_host_diag(alt_host)
            if alt_res["is_online"]:
                res = alt_res
                break

    _DIAG_CACHE[normalized_target] = (now, res)
    return res


def format_diagnostics_summary(diag: dict[str, Any]) -> str:
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
