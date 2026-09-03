"""
Базовый класс исполнителей действий (Action Executors) в среде Windows.
Реализует унифицированный жизненный цикл: Preflight ➔ Execute ➔ Verify,
распределенный мьютекс хоста lock:host:<pc_name> и WMI Bootstrap для WinRM.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import logging
import subprocess
import time
from typing import Any
import uuid

logger = logging.getLogger("execution_worker.executors.base")

RELEASE_HOST_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


@dataclass
class ActionResult:
    """Результат выполнения действия с подробным логом и полезной нагрузкой."""
    success: bool
    message: str
    error: str | None = None
    log: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


class BaseActionExecutor(ABC):
    """
    Абстрактный базовый класс для всех Action Executors:
    - Preflight: проверка доступности, WMI bootstrap WinRM, захват мьютекса хоста.
    - Execute: непосредственное выполнение команды в Windows.
    - Verify: валидация состояния системы после применения изменений.
    """

    def __init__(self, redis_client=None):
        self.redis = redis_client

    def set_redis_client(self, redis_client) -> None:
        self.redis = redis_client

    # -----------------------------------------------------------------------
    # Распределенный Host Concurrency Lock (lock:host:<pc_name>)
    # -----------------------------------------------------------------------

    async def acquire_host_lock(self, pc_name: str, ttl: int = 30) -> str | None:
        """
        Захватывает распределенную блокировку на ПК для предотвращения гонок WinRM (0x80338029).
        Возвращает токен владельца при успехе, иначе None.
        """
        if not self.redis or not pc_name:
            return "no_redis_token"

        lock_key = f"lock:host:{pc_name.upper().strip()}"
        owner_token = f"worker_{uuid.uuid4().hex[:8]}"

        try:
            acquired = await self.redis.set(lock_key, owner_token, nx=True, ex=ttl)
            if acquired:
                return owner_token
            logger.warning("ПК %s уже заблокирован другой операцией WinRM/WMI", pc_name)
            return None
        except Exception as e:
            logger.debug("Ошибка захвата host lock в Redis: %s", e)
            return "fallback_token"

    async def release_host_lock(self, pc_name: str, owner_token: str) -> None:
        """Безопасное атомарное освобождение блокировки через Lua-скрипт."""
        if not self.redis or not pc_name or owner_token in ("no_redis_token", "fallback_token"):
            return

        lock_key = f"lock:host:{pc_name.upper().strip()}"
        try:
            await self.redis.eval(RELEASE_HOST_LOCK_LUA, 1, lock_key, owner_token)
        except Exception as e:
            logger.debug("Ошибка освобождения host lock в Redis: %s", e)

    # -----------------------------------------------------------------------
    # Fail-Fast TCP Probing & WMI Bootstrap
    # -----------------------------------------------------------------------

    @staticmethod
    async def check_tcp_port(host: str, port: int, timeout_sec: float = 1.5) -> bool:
        """Асинхронная проверка доступности TCP порта."""
        if not host:
            return False
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout_sec
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def bootstrap_winrm(self, target_pc: str, log: list[str]) -> bool:
        """
        WMI Bootstrap: если WinRM (5985) закрыт, но открыт WMI/RPC (135),
        пытается удаленно запустить службу WinRM.
        """
        log.append(f"Проверка службы WinRM (порт 5985) на {target_pc}...")
        winrm_ok = await self.check_tcp_port(target_pc, 5985, timeout_sec=1.5)
        if winrm_ok:
            log.append("🟢 Служба WinRM доступна (порт 5985 открыт).")
            return True

        log.append(f"⚠️ Порт 5985 недоступен. Проверка WMI/RPC (порт 135) для Bootstrap...")
        rpc_ok = await self.check_tcp_port(target_pc, 135, timeout_sec=1.5)
        if not rpc_ok:
            log.append(f"❌ Хост {target_pc} недоступен по WMI/RPC (135) и WinRM (5985).")
            return False

        log.append(f"🔄 WMI/RPC (135) доступен. Запуск удаленной инициализации WinRM через sc.exe...")
        cmd = ["sc.exe", f"\\\\{target_pc}", "start", "WinRM"]
        try:
            res = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=10
            )
            log.append(f"Команда sc.exe завершена: {res.stdout.strip()}")
        except Exception as e:
            log.append(f"Ошибка вызова sc.exe: {e}")

        # Ожидание старта службы
        for _ in range(5):
            await asyncio.sleep(1.0)
            if await self.check_tcp_port(target_pc, 5985, timeout_sec=1.0):
                log.append("🟢 WMI Bootstrap успешен: служба WinRM запущена и отвечает на 5985.")
                return True

        log.append("❌ Не удалось запустить службу WinRM на удаленной машине.")
        return False

    # -----------------------------------------------------------------------
    # Неблокирующий запуск PowerShell
    # -----------------------------------------------------------------------

    @staticmethod
    def run_remote_powershell_sync(
        target_pc: str, script: str, timeout: int = 45
    ) -> dict[str, Any]:
        """Синхронно выполняет PowerShell скрипт через WinRM Invoke-Command."""
        ps_wrapper = f"""
        $ErrorActionPreference = 'Stop'
        try {{
            $res = Invoke-Command -ComputerName "{target_pc}" -ScriptBlock {{
                {script}
            }} -ErrorAction Stop
            Write-Output (ConvertTo-Json @{{ success = $true; data = $res }})
        }} catch {{
            Write-Output (ConvertTo-Json @{{ success = $false; error = $_.Exception.Message }})
        }}
        """
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_wrapper]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            out = res.stdout.strip()
            if out:
                json_start = out.find("{")
                json_end = out.rfind("}")
                if json_start != -1 and json_end != -1:
                    return json.loads(out[json_start : json_end + 1])
            if res.returncode != 0:
                return {"success": False, "error": res.stderr.strip() or "Process returned error"}
            return {"success": True, "raw": out}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Превышен таймаут выполнения ({timeout}s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def run_remote_powershell(
        self, target_pc: str, script: str, timeout: int = 45
    ) -> dict[str, Any]:
        """Асинхронная неблокирующая обертка через asyncio.to_thread."""
        return await asyncio.to_thread(
            self.run_remote_powershell_sync, target_pc, script, timeout
        )

    # -----------------------------------------------------------------------
    # Жизненный цикл действия: Preflight ➔ Execute ➔ Verify
    # -----------------------------------------------------------------------

    @abstractmethod
    async def preflight(self, target_pc: str, log: list[str], **kwargs) -> tuple[bool, str]:
        """Фаза Preflight: валидация хоста, параметров и окружения."""
        pass

    @abstractmethod
    async def execute(self, target_pc: str, log: list[str], **kwargs) -> ActionResult:
        """Фаза Execute: выполнение полезной нагрузки."""
        pass

    @abstractmethod
    async def verify(self, target_pc: str, log: list[str], **kwargs) -> tuple[bool, str]:
        """Фаза Verify: валидация примененного состояния."""
        pass

    async def run_action(self, target_pc: str, **kwargs) -> ActionResult:
        """
        Оркестрация полного жизненного цикла действия:
        1. Захват блокировки хоста
        2. Preflight
        3. Execute
        4. Verify
        5. Освобождение блокировки хоста
        """
        log: list[str] = [f"[{time.strftime('%X')}] Начало выполнения операции на {target_pc}"]
        owner_token = await self.acquire_host_lock(target_pc)
        if not owner_token:
            return ActionResult(
                success=False,
                message=f"Рабочая станция {target_pc} занята другой операцией (Host Lock). Повторите позже.",
                error="HostConcurrencyLockError: 0x80338029",
                log=[f"❌ Не удалось захватить мьютекс хоста {target_pc}."],
            )

        try:
            # 1. Preflight
            log.append(f"[{time.strftime('%X')}] Фаза 1: Preflight...")
            pre_ok, pre_msg = await self.preflight(target_pc, log, **kwargs)
            if not pre_ok:
                log.append(f"[{time.strftime('%X')}] ❌ Preflight отклонен: {pre_msg}")
                return ActionResult(success=False, message=pre_msg, error=pre_msg, log=log)

            # 2. Execute
            log.append(f"[{time.strftime('%X')}] Фаза 2: Execute...")
            exec_res = await self.execute(target_pc, log, **kwargs)
            if not exec_res.success:
                log.append(f"[{time.strftime('%X')}] ❌ Execute завершен с ошибкой: {exec_res.error}")
                exec_res.log = log
                return exec_res

            # 3. Verify
            log.append(f"[{time.strftime('%X')}] Фаза 3: Verify...")
            ver_ok, ver_msg = await self.verify(target_pc, log, **kwargs)
            if not ver_ok:
                log.append(f"[{time.strftime('%X')}] ❌ Verify не подтвердил результат: {ver_msg}")
                return ActionResult(
                    success=False,
                    message=f"Действие выполнено, но валидация не прошла: {ver_msg}",
                    error=ver_msg,
                    log=log,
                    payload=exec_res.payload,
                )

            log.append(f"[{time.strftime('%X')}] 🟢 Операция успешно завершена и проверена.")
            return ActionResult(
                success=True,
                message=exec_res.message,
                log=log,
                payload=exec_res.payload,
            )

        except Exception as e:
            logger.exception("Исключение при выполнении действия на %s: %s", target_pc, e)
            log.append(f"[{time.strftime('%X')}] 💥 Критическое исключение: {e}")
            return ActionResult(
                success=False,
                message=f"Ошибка выполнения: {e}",
                error=str(e),
                log=log,
            )
        finally:
            await self.release_host_lock(target_pc, owner_token)
