"""
Сервис фоновой экспресс-телеметрии и сетевой диагностики рабочих станций (Host Telemetry Service).

Реализует Fail-Fast сетевой каскад с нулевой задержкой при открытии заявки (Pre-fetch 0ms added latency):
1. Быстрый ICMP Ping (таймаут 400 мс).
2. Экспресс-проверка TCP-портов 5985 (WinRM) и 445 (SMB) (таймаут 300 мс).
3. При доступности порта 5985 — защищенный сбор метрик через CIM WinRM
   (диск C:, статус служб Spooler / 1C:Enterprise, активный пользователь) с Host Concurrency Lock.
4. Защита от сетевых штормов: Subnet Rate-Limiting (максимум 3 одновременных зонда на подсеть /24).
5. Кэширование в Redis (diag:<task_id>, TTL 10 минут).
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
import logging
import os
import re
import socket
import subprocess
import time
from typing import Any

from app.config import settings
from app.services.safety import HostConcurrencyLockError, host_concurrency_lock
from app.services.worker import get_redis_client
from app.utils.json_utils import json_dumps, json_loads
from app.utils.normalizer import (
    extract_pc_names_from_text,
    is_valid_pc_name,
    normalize_pc_name,
)

logger = logging.getLogger("core_api.services.host_telemetry")

# ---------------------------------------------------------------------------
# Константы сетевых таймаутов и кэша (SLA Phase 2)
# ---------------------------------------------------------------------------
PING_TIMEOUT_SEC: float = 0.4  # 400 мс
TCP_PROBE_TIMEOUT_SEC: float = 0.3  # 300 мс
TELEMETRY_CACHE_TTL_SEC: int = 600  # 10 минут
MAX_CONCURRENT_PER_SUBNET: int = 3  # Лимит зондов на подсеть /24
DOMAIN_SUFFIX = ".corporate.loc"

IP_REGEX = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

# In-memory семафоры подсетей для локального троттлинга
_SUBNET_SEMAPHORES: dict[str, asyncio.Semaphore] = {}
_GLOBAL_TELEMETRY_SEMAPHORE = asyncio.Semaphore(12)


# ---------------------------------------------------------------------------
# Вспомогательные функции сетевого каскада
# ---------------------------------------------------------------------------


def get_subnet_from_ip(ip: str | None) -> str | None:
    """Извлекает адрес подсети /24 из IPv4 адреса (напр. '10.244.1.45' -> '10.244.1.0/24')."""
    if not ip or not IP_REGEX.match(ip.strip()):
        return None
    octets = ip.strip().split(".")
    return f"{octets[0]}.{octets[1]}.{octets[2]}.0/24"


def extract_pc_from_task(task: dict[str, Any]) -> str | None:
    """
    Интеллектуально извлекает имя ПК из метаданных, кастомных полей или темы/описания заявки.
    """
    if not isinstance(task, dict):
        return None

    # 1. Проверяем нормализованные метаданные _field_meta
    meta = task.get("_field_meta") or {}
    if meta.get("pc_name"):
        norm = normalize_pc_name(meta["pc_name"])
        if norm and is_valid_pc_name(norm):
            return norm

    # 2. Проверяем кастомные поля CustomFields
    cfields = task.get("CustomFields")
    if isinstance(cfields, list):
        for cf in cfields:
            val = str(cf.get("Value") or "").strip()
            if val:
                norm = normalize_pc_name(val)
                if norm and is_valid_pc_name(norm):
                    return norm
    elif isinstance(cfields, dict):
        for _, val in cfields.items():
            val_str = str(val or "").strip()
            if val_str:
                norm = normalize_pc_name(val_str)
                if norm and is_valid_pc_name(norm):
                    return norm

    # 3. Извлекаем из темы и описания задачи
    name_text = str(task.get("Name") or "")
    desc_text = str(task.get("Description") or "")
    full_text = f"{name_text} {desc_text}".strip()
    if full_text:
        pc_names = extract_pc_names_from_text(full_text)
        if pc_names:
            return pc_names[0]

    return None


async def resolve_dns_fast(host: str, timeout_sec: float = 0.4) -> str | None:
    """Быстрый асинхронный резолвинг DNS с проверкой доменного суффикса."""
    if not host:
        return None
    cleaned = host.strip()
    if IP_REGEX.match(cleaned):
        return cleaned

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
        ip = await asyncio.wait_for(
            loop.run_in_executor(None, _resolve, cleaned), timeout=timeout_sec
        )
        return ip
    except Exception:
        return None


async def fast_ping(
    host: str, timeout_sec: float = PING_TIMEOUT_SEC
) -> dict[str, Any]:
    """
    Fail-Fast асинхронный ICMP пинг (1 пакет, таймаут 400 мс).
    """
    is_win = os.name == "nt"
    timeout_ms = max(int(timeout_sec * 1000), 100)
    if is_win:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec + 0.3
        )
        out_text = stdout.decode(
            "cp866" if is_win else "utf-8", errors="ignore"
        )
        lower_out = out_text.lower()

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

        is_online = (
            (proc.returncode == 0)
            and has_reply
            and not (
                "100% потерь" in lower_out
                or "100% loss" in lower_out
                or has_unreachable
            )
        )

        rtt_match = re.search(
            r"(?:время|time)[<=]([0-9\.]+)\s*ms", out_text, re.IGNORECASE
        )
        if not rtt_match:
            rtt_match = re.search(
                r"(?:Среднее|Average|avg)[ =]+([0-9\.]+)\s*ms",
                out_text,
                re.IGNORECASE,
            )
        avg_rtt = (
            f"{rtt_match.group(1)}ms"
            if rtt_match
            else ("<1ms" if is_online else None)
        )

        return {
            "host": host,
            "is_online": is_online,
            "avg_rtt": avg_rtt,
            "raw_output": out_text.strip(),
        }
    except (TimeoutError, asyncio.TimeoutError):
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return {
            "host": host,
            "is_online": False,
            "avg_rtt": None,
            "error": "Ping Timeout (400ms)",
        }
    except Exception as e:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return {
            "host": host,
            "is_online": False,
            "avg_rtt": None,
            "error": str(e),
        }


async def probe_tcp_port(
    host: str, port: int, timeout_sec: float = TCP_PROBE_TIMEOUT_SEC
) -> bool:
    """
    Быстрая неблокирующая проверка доступности TCP-порта (WinRM 5985 / SMB 445 / RPC 135).
    """
    try:
        conn = asyncio.open_connection(host, port)
        _, writer = await asyncio.wait_for(conn, timeout=timeout_sec)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Subnet Rate-Limiter (защита от сетевого шторма в подсети /24)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def subnet_rate_limit(
    subnet: str | None,
    max_concurrent: int = MAX_CONCURRENT_PER_SUBNET,
    redis_client: Any = None,
) -> AsyncGenerator[None, None]:
    """
    Асинхронный контекстный менеджер для ограничения параллельных зондов
    в одной подсети /24 (не более 3 параллельных запросов).
    """
    if not subnet:
        yield
        return

    # 1. Локальный семафор процесса
    if subnet not in _SUBNET_SEMAPHORES:
        _SUBNET_SEMAPHORES[subnet] = asyncio.Semaphore(max_concurrent)

    sem = _SUBNET_SEMAPHORES[subnet]
    await sem.acquire()

    # 2. Распределенный счетчик в Redis (если доступен)
    r = redis_client
    redis_key = f"ratelimit:subnet:{subnet}"
    redis_acquired = False
    if r is not None:
        try:
            curr = await r.incr(redis_key)
            await r.expire(redis_key, 10)
            redis_acquired = True
            if curr > max_concurrent:
                logger.debug(
                    "Подсеть %s перегружена (%d зондов), ожидание слота...",
                    subnet,
                    curr,
                )
                await asyncio.sleep(0.15)
        except Exception as e:
            logger.debug("Сбой инкремента счетчика подсети в Redis: %s", e)

    try:
        yield
    finally:
        sem.release()
        if redis_acquired and r is not None:
            try:
                await r.decr(redis_key)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CIM WinRM сбор системной информации (WMI/CIM metrics)
# ---------------------------------------------------------------------------


def run_cim_winrm_metrics_sync(
    target_pc: str, timeout_sec: int = 6
) -> dict[str, Any]:
    """
    Синхронный сбор системных метрик через CIM / WinRM (диск C:, Spooler, активный юзер).
    Использует Invoke-Command с таймаутом и безопасным JSON-выводом.
    """
    ps_script = f"""
    $ErrorActionPreference = 'SilentlyContinue'
    try {{
        $res = Invoke-Command -ComputerName "{target_pc}" -ScriptBlock {{
            $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'" | Select-Object Size,FreeSpace
            $spooler = Get-Service -Name Spooler -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Status
            $one_c = Get-Service -Name '*1C*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Status
            $user = (Get-CimInstance Win32_ComputerSystem).UserName

            [PSCustomObject]@{{
                DiskSize = $disk.Size
                DiskFree = $disk.FreeSpace
                SpoolerStatus = if ($spooler) {{ $spooler.ToString() }} else {{ 'NotInstalled' }}
                OneCStatus = if ($one_c) {{ $one_c.ToString() }} else {{ 'None' }}
                ActiveUser = $user
            }}
        }} -ErrorAction Stop

        Write-Output (ConvertTo-Json @{{
            success = $true
            data = @{{
                disk_total = $res.DiskSize
                disk_free = $res.DiskFree
                spooler = $res.SpoolerStatus
                onec = $res.OneCStatus
                user = $res.ActiveUser
            }}
        }})
    }} catch {{
        Write-Output (ConvertTo-Json @{{ success = $false; error = $_.Exception.Message }})
    }}
    """
    cmd = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        ps_script,
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        out = res.stdout.strip()
        if out:
            json_start = out.find("{")
            json_end = out.rfind("}")
            if json_start != -1 and json_end != -1:
                return json.loads(out[json_start : json_end + 1])
        return {"success": False, "error": res.stderr.strip() or "Empty output"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def collect_cim_metrics(
    target_pc: str, timeout_sec: int = 6
) -> dict[str, Any]:
    """
    Асинхронная обертка сбора CIM метрик через пул потоков.
    """
    try:
        raw_res = await asyncio.to_thread(
            run_cim_winrm_metrics_sync, target_pc, timeout_sec
        )
        if not raw_res.get("success"):
            return {
                "ok": False,
                "error": raw_res.get("error", "CIM query failed"),
                "disk_c": None,
                "services": {},
                "logged_in_user": None,
            }

        data = raw_res.get("data") or {}
        disk_total_bytes = data.get("disk_total") or 0
        disk_free_bytes = data.get("disk_free") or 0

        disk_info = None
        if disk_total_bytes > 0:
            total_gb = round(disk_total_bytes / (1024**3), 1)
            free_gb = round(disk_free_bytes / (1024**3), 1)
            free_pct = round((disk_free_bytes / disk_total_bytes) * 100, 1)
            disk_info = {
                "total_gb": total_gb,
                "free_gb": free_gb,
                "free_percent": free_pct,
            }

        services = {}
        if data.get("spooler"):
            services["Spooler"] = data["spooler"]
        if data.get("onec") and data["onec"] != "None":
            services["1C:Enterprise"] = data["onec"]

        user = data.get("user")
        if user and "\\" in user:
            user = user.split("\\")[-1]

        return {
            "ok": True,
            "disk_c": disk_info,
            "services": services,
            "logged_in_user": user,
        }
    except Exception as e:
        logger.debug("Ошибка выполнения CIM метрик для %s: %s", target_pc, e)
        return {
            "ok": False,
            "error": str(e),
            "disk_c": None,
            "services": {},
            "logged_in_user": None,
        }


# ---------------------------------------------------------------------------
# Форматирование компактного бейджа инженера
# ---------------------------------------------------------------------------


def format_telemetry_badge(telemetry: dict[str, Any]) -> str:
    """
    Формирует компактный бейдж технической картины:
    Пример: [NTEMW0144: 🟢 Ping 2ms | 💾 C: 45GB | 🖨️ Spooler: OK | 👤 ivanov.ii]
    """
    if not telemetry:
        return "[Хост: ❓ Не указан]"

    status = telemetry.get("status")
    pc_name = (
        telemetry.get("canonical_name") or telemetry.get("pc_name") or "ПК"
    )

    if status == "HOST_NOT_SPECIFIED":
        return "[Хост: ⚪ Не указан в заявке]"

    if status == "OFFLINE":
        rtt = telemetry.get("avg_rtt")
        rtt_text = f" (Ping timeout)" if not rtt else f" ({rtt})"
        return f"[{pc_name}: 🔴 Оффлайн{rtt_text}]"

    # Онлайн
    rtt_text = telemetry.get("avg_rtt") or "<1ms"
    parts = [f"🟢 Ping {rtt_text}"]

    disk_c = telemetry.get("disk_c")
    if disk_c and isinstance(disk_c, dict):
        free_gb = disk_c.get("free_gb")
        if free_gb is not None:
            parts.append(f"💾 C: {free_gb}GB")

    services = telemetry.get("services") or {}
    spooler = services.get("Spooler")
    if spooler == "Running":
        parts.append("🖨️ Spooler: OK")
    elif spooler and spooler != "Running":
        parts.append(f"🖨️ Spooler: ⚠️ {spooler}")

    user = telemetry.get("logged_in_user")
    if user:
        parts.append(f"👤 {user}")

    return f"[{pc_name}: {' | '.join(parts)}]"


# ---------------------------------------------------------------------------
# Главная функция сбора телеметрии хоста
# ---------------------------------------------------------------------------


async def collect_host_telemetry(
    pc_name: str | None,
    task_id: int | None = None,
    creator_ip: str | None = None,
    use_cache: bool = True,
    redis_client: Any = None,
) -> dict[str, Any]:
    """
    Выполняет комплексный сбор Fail-Fast телеметрии рабочей станции.

    Этапы:
    1. Проверка наличия имени ПК (если нет -> HOST_NOT_SPECIFIED).
    2. Проверка кэша в Redis (diag:<task_id> и diag:host:<pc_name>).
    3. Резолвинг IP и определение подсети /24.
    4. Захват Subnet Rate-Limiter (не более 3 параллельных зондов на подсеть).
    5. Fail-Fast ICMP Ping (400 мс). Если хост оффлайн -> мгновенный возврат OFFLINE.
    6. Экспресс-проверка портов 5985 (WinRM) и 445 (SMB) (300 мс).
    7. Если WinRM доступен -> сбор CIM метрик (диск, службы, юзер) с Host Concurrency Lock.
    8. Сохранение результата в Redis с TTL 10 минут.
    """
    now_utc = datetime.now(UTC).isoformat()
    r = redis_client if redis_client is not None else get_redis_client()

    # 1. Если имя ПК не указано
    if not pc_name or not str(pc_name).strip():
        result = {
            "task_id": task_id,
            "pc_name": None,
            "canonical_name": None,
            "resolved_ip": None,
            "subnet": None,
            "status": "HOST_NOT_SPECIFIED",
            "status_detail": "Имя ПК не указано в заявке",
            "ping_ok": False,
            "avg_rtt": None,
            "winrm_port_5985": False,
            "smb_port_445": False,
            "disk_c": None,
            "services": {},
            "logged_in_user": None,
            "badge": "[Хост: ⚪ Не указан в заявке]",
            "collected_at": now_utc,
            "cached": False,
        }
        if task_id and r:
            try:
                await r.set(
                    f"diag:{task_id}",
                    json_dumps(result),
                    ex=TELEMETRY_CACHE_TTL_SEC,
                )
            except Exception:
                pass
        return result

    raw_pc = str(pc_name).strip()
    norm = normalize_pc_name(raw_pc)
    canonical_pc = norm.upper() if norm else raw_pc.upper()

    # 2. Проверка кэша в Redis
    if use_cache and r:
        try:
            cached_data = None
            if task_id:
                cached_data = await r.get(f"diag:{task_id}")
            if not cached_data:
                cached_data = await r.get(f"diag:host:{canonical_pc}")

            if cached_data:
                parsed = json_loads(cached_data)
                if isinstance(parsed, dict):
                    parsed["cached"] = True
                    return parsed
        except Exception as e:
            logger.debug(
                "Ошибка чтения кэша телеметрии из Redis для %s: %s",
                canonical_pc,
                e,
            )

    async with _GLOBAL_TELEMETRY_SEMAPHORE:
        # 3. DNS Резолвинг
        resolved_ip = await resolve_dns_fast(canonical_pc, timeout_sec=0.4)
        target_to_probe = resolved_ip or canonical_pc
        subnet = get_subnet_from_ip(resolved_ip)

        # 4. Выполнение каскада под защитой Subnet Rate-Limiter
        async with subnet_rate_limit(subnet, redis_client=r):
            # 5. Fail-Fast ICMP Ping (400 мс)
            ping_res = await fast_ping(
                target_to_probe, timeout_sec=PING_TIMEOUT_SEC
            )
            is_ping_ok = ping_res.get("is_online", False)
            avg_rtt = ping_res.get("avg_rtt")

            # 6. Проверка портов 5985 (WinRM) и 445 (SMB) (300 мс)
            smb_task = probe_tcp_port(
                target_to_probe, 445, timeout_sec=TCP_PROBE_TIMEOUT_SEC
            )
            winrm_task = probe_tcp_port(
                target_to_probe, 5985, timeout_sec=TCP_PROBE_TIMEOUT_SEC
            )
            smb_ok, winrm_ok = await asyncio.gather(smb_task, winrm_task)

            # Fallback на creator_ip, если хост не отвечает, но есть IP заявителя
            if not is_ping_ok and not smb_ok and not winrm_ok and creator_ip:
                c_ip = creator_ip.strip()
                if IP_REGEX.match(c_ip) and c_ip != resolved_ip:
                    c_ping = await fast_ping(
                        c_ip, timeout_sec=PING_TIMEOUT_SEC
                    )
                    c_smb = await probe_tcp_port(
                        c_ip, 445, timeout_sec=TCP_PROBE_TIMEOUT_SEC
                    )
                    c_winrm = await probe_tcp_port(
                        c_ip, 5985, timeout_sec=TCP_PROBE_TIMEOUT_SEC
                    )
                    if (
                        c_ping.get("is_online")
                        or c_smb
                        or c_winrm
                    ):
                        is_ping_ok = c_ping.get("is_online", False)
                        avg_rtt = c_ping.get("avg_rtt")
                        smb_ok = smb_ok or c_smb
                        winrm_ok = winrm_ok or c_winrm
                        resolved_ip = c_ip
                        subnet = get_subnet_from_ip(resolved_ip)

            is_online = is_ping_ok or smb_ok or winrm_ok

            # Если хост оффлайн — не запускаем WMI, возвращаем моментальный результат
            if not is_online:
                result = {
                    "task_id": task_id,
                    "pc_name": raw_pc,
                    "canonical_name": canonical_pc,
                    "resolved_ip": resolved_ip,
                    "subnet": subnet,
                    "status": "OFFLINE",
                    "status_detail": "OFFLINE (Ping timeout)",
                    "ping_ok": False,
                    "avg_rtt": None,
                    "winrm_port_5985": False,
                    "smb_port_445": False,
                    "disk_c": None,
                    "services": {},
                    "logged_in_user": None,
                    "badge": f"[{canonical_pc}: 🔴 Оффлайн (Ping timeout)]",
                    "collected_at": now_utc,
                    "cached": False,
                }
                # Сохраняем в Redis
                if r:
                    try:
                        if task_id:
                            await r.set(
                                f"diag:{task_id}",
                                json_dumps(result),
                                ex=TELEMETRY_CACHE_TTL_SEC,
                            )
                        await r.set(
                            f"diag:host:{canonical_pc}",
                            json_dumps(result),
                            ex=TELEMETRY_CACHE_TTL_SEC,
                        )
                    except Exception:
                        pass
                return result

            # 7. Хост онлайн: если порт 5985 открыт, собираем CIM-метрики под Host Lock
            disk_c = None
            services = {}
            logged_in_user = None

            if winrm_ok:
                try:
                    async with host_concurrency_lock(
                        canonical_pc, ttl=15, redis_client=r
                    ):
                        cim_data = await collect_cim_metrics(
                            canonical_pc, timeout_sec=5
                        )
                        if cim_data.get("ok"):
                            disk_c = cim_data.get("disk_c")
                            services = cim_data.get("services") or {}
                            logged_in_user = cim_data.get("logged_in_user")
                except HostConcurrencyLockError:
                    logger.debug(
                        "Хост %s занят другой сессией, сбор CIM пропущен",
                        canonical_pc,
                    )
                except Exception as e_wmi:
                    logger.debug(
                        "Ошибка при получении CIM метрик для %s: %s",
                        canonical_pc,
                        e_wmi,
                    )

            status_detail = (
                f"Ping {avg_rtt or 'OK'}"
                + (" | WinRM:✓" if winrm_ok else "")
                + (" | SMB:✓" if smb_ok else "")
            )

            result = {
                "task_id": task_id,
                "pc_name": raw_pc,
                "canonical_name": canonical_pc,
                "resolved_ip": resolved_ip,
                "subnet": subnet,
                "status": "ONLINE",
                "status_detail": status_detail,
                "ping_ok": is_ping_ok,
                "avg_rtt": avg_rtt,
                "winrm_port_5985": winrm_ok,
                "smb_port_445": smb_ok,
                "disk_c": disk_c,
                "services": services,
                "logged_in_user": logged_in_user,
                "badge": "",
                "collected_at": now_utc,
                "cached": False,
            }
            result["badge"] = format_telemetry_badge(result)

            # 8. Сохранение в Redis кэш
            if r:
                try:
                    if task_id:
                        await r.set(
                            f"diag:{task_id}",
                            json_dumps(result),
                            ex=TELEMETRY_CACHE_TTL_SEC,
                        )
                    await r.set(
                        f"diag:host:{canonical_pc}",
                        json_dumps(result),
                        ex=TELEMETRY_CACHE_TTL_SEC,
                    )
                except Exception as e_save:
                    logger.debug(
                        "Ошибка сохранения телеметрии в Redis для %s: %s",
                        canonical_pc,
                        e_save,
                    )

            return result


# ---------------------------------------------------------------------------
# Pre-fetch и сервисные обертки для Poller / Triage Hub
# ---------------------------------------------------------------------------


async def prefetch_task_telemetry(
    task: dict[str, Any], redis_client: Any = None
) -> dict[str, Any]:
    """
    Фоновый Pre-fetch телеметрии для вновь обнаруженной или обрабатываемой заявки.
    Запускается в фоне (fire-and-forget через asyncio.create_task).
    """
    task_id = task.get("Id") or task.get("task_id")
    pc_name = extract_pc_from_task(task)
    creator_ip = task.get("CreatorIP") or task.get("CreatorIp")

    return await collect_host_telemetry(
        pc_name=pc_name,
        task_id=task_id,
        creator_ip=creator_ip,
        use_cache=True,
        redis_client=redis_client,
    )


async def get_task_telemetry(
    task_id: int, redis_client: Any = None
) -> dict[str, Any] | None:
    """
    Быстрое чтение кэшированной телеметрии по ID задачи из Redis (0ms latency).
    """
    try:
        r = redis_client if redis_client is not None else get_redis_client()
        raw = await r.get(f"diag:{task_id}")
        if raw:
            parsed = json_loads(raw)
            if isinstance(parsed, dict):
                parsed["cached"] = True
                return parsed
    except Exception as e:
        logger.debug(
            "Ошибка чтения кэша телеметрии diag:%d из Redis: %s", task_id, e
        )
    return None
