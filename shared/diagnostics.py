import asyncio
import logging
import os
import re
import socket
import subprocess
import time
from typing import Any
try:
    from shared.normalizer import (
        normalize_pc_name,
        is_valid_pc_name,
        resolve_pc_candidates,
        normalize_printer_address,
        KNOWN_PC_PREFIXES,
    )
except ImportError:
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

# Пул ограничения параллельных сетевых проверок для предотвращения DNS/socket спайков
_NETWORK_SEMAPHORE = asyncio.Semaphore(6)

IP_REGEX = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

# Паттерн поиска устройств: любые буквенно-цифровые комбинации с возможными пробелами/дефисами
DEVICE_PATTERN = re.compile(
    r"\b([A-Za-zА-Яа-я\-_]{2,7})[\s\-_]*(\d{2,5})\b",
    re.IGNORECASE,
)

KNOWN_PHONE_FIELD_IDS = {"1088", "1144", "1207", "1077", "1096", "1188", "1209"}
DOMAIN_SUFFIX = ".corporate.loc"


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


async def async_ping(host: str, count: int = 2, timeout_sec: float = 1.0) -> dict[str, Any]:
    """
    Выполняет асинхронный ICMP-пинг хоста (адаптировано для Windows и Linux).
    По умолчанию отправляет 2 пакета для надежного преодоления ARP-задержек.
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
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec * count + 2.0)
        out_text = stdout.decode("cp866" if is_win else "utf-8", errors="ignore")
        lower_out = out_text.lower()

        # Проверка на ответы маршрутизатора об ошибке или 100% потерю
        has_unreachable = (
            "недоступен" in lower_out
            or "unreachable" in lower_out
            or "100% потерь" in lower_out
            or "100% loss" in lower_out
            or "превышен интервал" in lower_out
            or "timed out" in lower_out
        )

        has_reply = (
            "ttl=" in lower_out
            or "байт=" in lower_out
            or "bytes=" in lower_out
            or "время=" in lower_out
            or "time=" in lower_out
        )

        is_online = (proc.returncode == 0) and has_reply and not ("100% потерь" in lower_out or "100% loss" in lower_out)

        if has_unreachable and not ("ttl=" in lower_out):
            is_online = False

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


async def check_tcp_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """
    Проверяет доступность TCP-порта (SMB 445, WinRM 5985, RPC 135).
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


async def resolve_dns(host: str, timeout: float = 1.0) -> str | None:
    """Резолвит DNS имя в IP-адрес с поддержкой доменного суффикса."""
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
        return host

    loop = asyncio.get_running_loop()

    def _resolve(h: str) -> str | None:
        try:
            return socket.gethostbyname(h)
        except Exception:
            if "." not in h:
                try:
                    return socket.gethostbyname(f"{h}{DOMAIN_SUFFIX}")
                except Exception:
                    pass
        return None

    try:
        ip = await asyncio.wait_for(loop.run_in_executor(None, _resolve, host), timeout=timeout)
        return ip
    except Exception:
        return None


async def run_single_host_diag(target: str, creator_ip: str | None = None) -> dict[str, Any]:
    """
    Диагностика одиночного хоста с каскадным опросом:
    DNS / FQDN -> ICMP (Target + IP) -> SMB 445 -> WinRM 5985 -> RPC 135.
    """
    async with _NETWORK_SEMAPHORE:
        # 1. DNS Резолвинг
        ip = await resolve_dns(target)
        target_to_ping = ip or target

        # 2. Параллельная проверка ICMP и портов
        ping_task = async_ping(target_to_ping, count=2, timeout_sec=1.0)
        smb_task = check_tcp_port(target_to_ping, 445, timeout=1.0)
        winrm_task = check_tcp_port(target_to_ping, 5985, timeout=1.0)
        rpc_task = check_tcp_port(target_to_ping, 135, timeout=1.0)

        ping_res, smb_ok, winrm_ok, rpc_ok = await asyncio.gather(ping_task, smb_task, winrm_task, rpc_task)
        is_online = ping_res.get("is_online") or smb_ok or winrm_ok or rpc_ok

        # 3. Fallback на CreatorIP, если указан и хост оффлайн
        if not is_online and creator_ip and IP_REGEX.match(creator_ip.strip()) and creator_ip.strip() != ip:
            clean_creator_ip = creator_ip.strip()
            c_ping = await async_ping(clean_creator_ip, count=2, timeout_sec=1.0)
            c_smb = await check_tcp_port(clean_creator_ip, 445, timeout=1.0)
            c_winrm = await check_tcp_port(clean_creator_ip, 5985, timeout=1.0)
            c_rpc = await check_tcp_port(clean_creator_ip, 135, timeout=1.0)
            if c_ping.get("is_online") or c_smb or c_winrm or c_rpc:
                is_online = True
                ip = clean_creator_ip
                if not ping_res.get("is_online") and c_ping.get("is_online"):
                    ping_res = c_ping
                smb_ok = smb_ok or c_smb
                winrm_ok = winrm_ok or c_winrm
                rpc_ok = rpc_ok or c_rpc

        return {
            "target": target,
            "resolved_ip": ip,
            "is_online": is_online,
            "avg_rtt": ping_res.get("avg_rtt"),
            "icmp_ping_ok": ping_res.get("is_online", False),
            "smb_port_445": smb_ok,
            "winrm_port_5985": winrm_ok,
            "rpc_port_135": rpc_ok,
        }


async def run_host_diagnostics(
    target: str,
    use_cache: bool = True,
    fallback_candidates: list[str] | None = None,
    creator_ip: str | None = None,
) -> dict[str, Any]:
    """
    Комплексная диагностика хоста с поддержкой алиасов, CreatorIP, fallback-кандидатов и Double-Check.
    """
    normalized_target = normalize_pc_name(target) or target.strip().upper()
    now = time.time()

    if use_cache and normalized_target in _DIAG_CACHE:
        cached_time, cached_res = _DIAG_CACHE[normalized_target]
        if now - cached_time < _CACHE_TTL_SEC:
            return cached_res

    res = await run_single_host_diag(normalized_target, creator_ip=creator_ip)

    # Если оффлайн и есть fallback кандидаты
    if not res["is_online"] and fallback_candidates:
        other_cands = [c for c in fallback_candidates if c != normalized_target]
        for alt_host in other_cands:
            alt_res = await run_single_host_diag(alt_host, creator_ip=creator_ip)
            if alt_res["is_online"]:
                res = alt_res
                break

    # Two-tier double check: если хост все еще оффлайн, делаем еще одну контрольную попытку через 0.3 сек
    if not res["is_online"]:
        await asyncio.sleep(0.3)
        retry_res = await run_single_host_diag(normalized_target, creator_ip=creator_ip)
        if retry_res["is_online"]:
            res = retry_res

    _DIAG_CACHE[normalized_target] = (now, res)
    return res


def format_diagnostics_summary(diag: dict[str, Any]) -> str:
    target = diag.get("target", "Unknown")
    ip = diag.get("resolved_ip")
    is_online = diag.get("is_online", False)
    rtt = diag.get("avg_rtt")
    ports = []
    if diag.get("smb_port_445"):
        ports.append("SMB:✓")
    if diag.get("winrm_port_5985"):
        ports.append("WinRM:✓")
    if diag.get("rpc_port_135"):
        ports.append("RPC:✓")
    ports_str = " | " + " ".join(ports) if ports else " | SMB:✗"

    if is_online:
        ip_info = f" [{ip}]" if ip and ip != target else ""
        rtt_info = f" RTT: {rtt}" if rtt else ""
        return f"🟢 В СЕТИ: {target}{ip_info}{rtt_info}{ports_str}"
    else:
        dns_info = f" (DNS: {ip})" if ip else " (DNS не найден)"
        return f"🔴 НЕ В СЕТИ: {target}{dns_info} | ICMP и порты не отвечают"
