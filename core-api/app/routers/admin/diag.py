import asyncio
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Depends

from app.routers.deps import require_permission
from shared.diagnostics import (
    DOMAIN_SUFFIX,
    check_tcp_port,
    resolve_dns,
    run_single_host_diag,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ─── Сетевая экспресс-диагностика хостов ─────────────────────────────────────
_DIAG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DIAG_CACHE_TTL = 45.0  # 45 секунд


async def _resolve_host_ip(host: str, timeout: float = 0.8) -> str | None:
    """
    Разрешает имя хоста в IP через системный сокет DNS с корпоративным суффиксом corporate.loc.
    (Делегирует в SSOT shared.diagnostics.resolve_dns).
    """
    return await resolve_dns(host, timeout=timeout)


async def _check_tcp_port(target: str, port: int, timeout: float = 0.8) -> bool:
    """
    Проверяет доступность TCP-порта (делегирует в SSOT shared.diagnostics.check_tcp_port).
    """
    return await check_tcp_port(target, port, timeout=timeout)


async def _check_single_host(host: str, include_specs: bool = True) -> dict[str, Any]:
    """
    Отказоустойчивая диагностика одиночного хоста:
    1. Резолвинг через DNS (включая corporate.loc).
    2. Одновременный опрос ICMP Ping + TCP порты SMB (445), WinRM (5985), RPC (135).
    3. Если доступен WinRM и включен include_specs — получение паспорта железа (CPU, RAM, SSD/HDD, Uptime).
    """
    clean_host = host.strip()
    if not clean_host:
        return {
            "host": host,
            "is_online": False,
            "avg_rtt": None,
            "smb_ok": False,
            "winrm_ok": False,
            "status_label": "🔴 Офлайн",
            "specs": None,
        }

    # Делегируем в централизованный диагностический модуль
    diag_res = await run_single_host_diag(clean_host)
    is_online = diag_res.get("is_online", False)
    avg_rtt = diag_res.get("avg_rtt")
    smb_ok = diag_res.get("smb_ok", False)
    winrm_ok = diag_res.get("winrm_ok", False)
    rpc_ok = diag_res.get("rpc_ok", False)
    resolved_ip = diag_res.get("resolved_ip")

    if not is_online:
        status_label = "🔴 Офлайн"
    elif smb_ok and winrm_ok:
        status_label = "🟢 Онлайн (SMB+WinRM)"
    elif smb_ok:
        status_label = "🟡 Онлайн (SMB)"
    elif winrm_ok:
        status_label = "🟡 Онлайн (WinRM)"
    else:
        status_label = "🟡 Онлайн (Ping)"

    specs = None
    if is_online and winrm_ok and include_specs:
        try:
            from app.services.host_telemetry import collect_hardware_specs
            specs = await collect_hardware_specs(clean_host)
        except Exception as e_specs:
            logger.debug("Не удалось получить specs для %s: %s", clean_host, e_specs)

    return {
        "host": clean_host,
        "resolved_ip": resolved_ip,
        "is_online": is_online,
        "avg_rtt": avg_rtt,
        "smb_ok": smb_ok,
        "winrm_ok": winrm_ok,
        "rpc_ok": rpc_ok,
        "status_label": status_label,
        "specs": specs,
    }


async def _check_host_ping_and_ports(host_str: str, include_specs: bool = True) -> dict[str, Any]:
    """
    Поддерживает как одиночные хосты, так и списки хостов через запятую/пробел/точку с запятой.
    Возвращает статус доступности рабочего места оператора в реальном времени.
    """
    if not host_str:
        return {"is_online": False, "status_label": "Хост не указан", "specs": None}

    # Разделяем строку по запятым, точкам с запятой или пробелам
    raw_hosts = re.split(r"[,;\s]+", host_str.strip())
    hosts = [h.strip() for h in raw_hosts if h.strip()]

    if not hosts:
        return {"is_online": False, "status_label": "Хост не указан", "specs": None}

    import app.routers.admin as admin

    # Одиночный хост
    if len(hosts) == 1:
        return await admin._check_single_host(hosts[0], include_specs=include_specs)

    # Несколько хостов: параллельно опрашиваем все хосты
    tasks = [admin._check_single_host(h, include_specs=include_specs) for h in hosts]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid_results = []
    for h, res in zip(hosts, results, strict=False):
        if isinstance(res, dict):
            valid_results.append(res)
        else:
            valid_results.append({
                "host": h,
                "is_online": False,
                "avg_rtt": None,
                "smb_ok": False,
                "winrm_ok": False,
                "status_label": "🔴 Сбой проверки",
                "specs": None,
            })

    online_hosts = [r for r in valid_results if r["is_online"]]
    has_online = len(online_hosts) > 0
    all_online = len(online_hosts) == len(valid_results)

    if all_online:
        summary_label = f"🟢 Все в сети ({len(valid_results)})"
    elif has_online:
        summary_label = f"🟡 Доступно {len(online_hosts)} из {len(valid_results)}"
    else:
        summary_label = "🔴 Все офлайн"

    primary = online_hosts[0] if online_hosts else valid_results[0]

    return {
        "host": host_str,
        "is_online": has_online,
        "avg_rtt": primary.get("avg_rtt"),
        "smb_ok": any(r.get("smb_ok") for r in valid_results),
        "winrm_ok": any(r.get("winrm_ok") for r in valid_results),
        "status_label": summary_label,
        "specs": primary.get("specs"),
        "multiple": True,
        "hosts_results": valid_results,
    }


@router.get("/admin/api/diag/{host}", dependencies=[Depends(require_permission("diagnostic:run"))])
async def get_host_diagnostics(host: str, include_specs: bool = True):
    """
    Возвращает статус доступности рабочего места оператора в реальном времени.
    """
    clean_host = host.strip()
    now = time.monotonic()
    cache_key = f"{clean_host}:specs={include_specs}"
    if cache_key in _DIAG_CACHE:
        ts, cached = _DIAG_CACHE[cache_key]
        if now - ts < _DIAG_CACHE_TTL:
            return cached

    import app.routers.admin as admin

    result = await admin._check_host_ping_and_ports(clean_host, include_specs=include_specs)
    _DIAG_CACHE[cache_key] = (now, result)
    return result
