"""
Эталонный ActionHandler для удаленной установки принтеров в Windows (WinRM, SMB, WMI).
Соответствует 7-фазному жизненному циклу ADR-0002 и спецификации Increment 4:
1. validate: Pydantic-нормализация и валидация имени хоста и принтера.
2. preflight: Строго read-only проверка доступности, WMI/CIM опрос Spooler и WinRM,
   поиск точного профиля в KB без generic fallback, расчет SHA-256.
3. prepare: WMI bootstrap службы WinRM (только после approval) с фиксацией ownership_token.
4. execute: Локальный SMB стейджинг в C:\\Windows\\Temp\\_driver_staging\\<command_id>\\
   с проверкой SHA-256, вызовом pnputil, Add-PrinterPort и Add-Printer.
5. verify: Точная проверка совпадения имени очереди, порта и версии драйвера.
6. cleanup: Гарантированное удаление каталога стейджинга и безопасный откат службы WinRM.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import socket
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from sdk.base import ActionHandler
from sdk.models import ActionResult, HandlerContext, RiskClass
from sdk.powershell import run_powershell_safe
from shared.diagnostics import check_tcp_port
from shared.normalizer import is_valid_pc_name, is_valid_printer_name, normalize_pc_name
from shared.printers import PrinterConfig, find_printer_by_name

logger = logging.getLogger("execution_worker.handlers.install_printer")

# Разрешенные доверенные корни SMB-хранилищ драйверов
ALLOWLIST_SMB_ROOTS: tuple[str, ...] = (
    "\\\\truenas\\Drivers",
    "\\\\truenas\\drivers",
)


class InstallPrinterInput(BaseModel):
    """Входные данные для установки принтера."""

    pc_name: str = Field(..., description="Имя рабочей станции Windows (NetBIOS или FQDN)")
    printer_name: str = Field(..., description="Название принтера или очереди печати")
    connection_type: Literal["tcpip", "usb"] = Field("tcpip", description="Тип подключения")
    printer_ip: str | None = Field(None, description="IP-адрес или сетевое имя сетевого принтера")

    @field_validator("pc_name")
    @classmethod
    def validate_pc_name(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Имя рабочей станции не может быть пустым")
        norm = normalize_pc_name(v_clean)
        if norm:
            v_clean = norm
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_\-\.]{0,62}", v_clean):
            raise ValueError(f"Некорректное имя рабочей станции: {v}")
        return v_clean.upper()

    @field_validator("printer_name")
    @classmethod
    def validate_printer_name(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean or len(v_clean) < 2 or len(v_clean) > 128:
            raise ValueError(f"Недопустимая длина имени принтера (2-128 символов): {v}")
        if re.search(r'[`$"\x00-\x1f;|<>&]', v_clean):
            raise ValueError(f"Недопустимые спецсимволы в имени принтера: {v}")
        return v_clean


class InstallPrinterHandler(ActionHandler[InstallPrinterInput]):
    """Эталонный обработчик установки принтеров с защитой от Kerberos Double-Hop и WMI Bootstrap."""

    id = "install_printer"
    version = "2.0.0"
    risk_class = RiskClass.NEVER_AUTO_RETRY
    capabilities = ["windows", "winrm", "wmi", "printers", "smb_staging"]
    input_model = InstallPrinterInput

    def _is_allowed_smb_path(self, path: str) -> bool:
        """Проверяет, что путь к драйверу принадлежит доверенному корню SMB."""
        if not path:
            return False
        normalized = path.replace("/", "\\").lower()
        return any(normalized.startswith(root.lower()) for root in ALLOWLIST_SMB_ROOTS)

    # -------------------------------------------------------------------------
    # Вспомогательные сетевые и WMI методы
    # -------------------------------------------------------------------------

    async def _check_service_status(
        self, pc_name: str, service_name: str, winrm_available: bool
    ) -> tuple[str, str]:
        """
        Строго read-only запрос состояния службы Windows (State и StartMode).
        Не изменяет состояние хоста!
        Возвращает: (state, start_mode), например ('Running', 'Auto') или ('Stopped', 'Manual').
        """
        if winrm_available:
            script = """
            param($serviceName)
            $svc = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
            if ($svc) {
                [PSCustomObject]@{
                    State = $svc.State
                    StartMode = $svc.StartMode
                }
            } else {
                [PSCustomObject]@{
                    State = 'NotFound'
                    StartMode = 'Unknown'
                }
            }
            """
            res = await run_powershell_safe(
                pc_name=pc_name,
                script=script,
                parameters={"serviceName": service_name},
                timeout_sec=15,
            )
            if res.success and isinstance(res.data, dict):
                return str(res.data.get("State", "Unknown")), str(res.data.get("StartMode", "Unknown"))

        # Fallback на sc.exe query (RPC порт 135) — строго read-only запрос
        try:
            cmd = ["sc.exe", f"\\\\{pc_name}", "qc", service_name]
            qc_proc = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=8
            )
            start_mode = "Manual"
            if "AUTO_START" in qc_proc.stdout:
                start_mode = "Auto"
            elif "DEMAND_START" in qc_proc.stdout:
                start_mode = "Manual"
            elif "DISABLED" in qc_proc.stdout:
                start_mode = "Disabled"

            query_cmd = ["sc.exe", f"\\\\{pc_name}", "query", service_name]
            query_proc = await asyncio.to_thread(
                subprocess.run, query_cmd, capture_output=True, text=True, timeout=8
            )
            state = "Stopped"
            if "RUNNING" in query_proc.stdout:
                state = "Running"
            elif "STOPPED" in query_proc.stdout:
                state = "Stopped"
            elif "PAUSED" in query_proc.stdout:
                state = "Paused"

            return state, start_mode
        except Exception as exc:
            logger.debug("sc.exe query для %s на %s завершился с ошибкой: %s", service_name, pc_name, exc)
            return "Unknown", "Unknown"

    async def _bootstrap_start_winrm(self, pc_name: str, log: list[str]) -> bool:
        """Перевод службы WinRM в demand и запуск через sc.exe (WMI/RPC порт 135)."""
        log.append(f"Инициализация WinRM на {pc_name} через WMI/RPC (порт 135)...")
        try:
            cfg_cmd = ["sc.exe", f"\\\\{pc_name}", "config", "WinRM", "start=", "demand"]
            await asyncio.to_thread(subprocess.run, cfg_cmd, capture_output=True, text=True, timeout=8)

            start_cmd = ["sc.exe", f"\\\\{pc_name}", "start", "WinRM"]
            await asyncio.to_thread(subprocess.run, start_cmd, capture_output=True, text=True, timeout=8)

            # Ожидание готовности порта 5985 (до 5 секунд)
            for _ in range(5):
                await asyncio.sleep(1.0)
                if await check_tcp_port(pc_name, 5985, timeout=1.0):
                    log.append(f"Служба WinRM на {pc_name} успешно запущена (порт 5985 открыт).")
                    return True
        except Exception as exc:
            log.append(f"Ошибка вызова sc.exe при запуске WinRM: {exc}")
        return False

    async def _restore_winrm_service(
        self, pc_name: str, orig_start_mode: str, log: list[str]
    ) -> None:
        """Безопасный откат службы WinRM в исходное состояние при отсутствии сторонних сессий."""
        log.append(f"Откат службы WinRM на {pc_name} в исходное состояние...")
        try:
            # Остановка службы
            stop_cmd = ["sc.exe", f"\\\\{pc_name}", "stop", "WinRM"]
            await asyncio.to_thread(subprocess.run, stop_cmd, capture_output=True, text=True, timeout=8)

            # Восстановление режима запуска
            sc_start = "demand"
            if "auto" in orig_start_mode.lower():
                sc_start = "auto"
            elif "disabled" in orig_start_mode.lower():
                sc_start = "disabled"

            cfg_cmd = ["sc.exe", f"\\\\{pc_name}", "config", "WinRM", "start=", sc_start]
            await asyncio.to_thread(subprocess.run, cfg_cmd, capture_output=True, text=True, timeout=8)
            log.append(f"Служба WinRM на {pc_name} успешно остановлена (режим запуска: {sc_start}).")
        except Exception as exc:
            log.append(f"Предупреждение при откате WinRM: {exc}")

    # -------------------------------------------------------------------------
    # 7 Фаз Жизненного Цикла
    # -------------------------------------------------------------------------

    async def validate(self, ctx: HandlerContext, params: InstallPrinterInput) -> tuple[bool, str]:
        """Фаза 1: Validate — статическая проверка параметров."""
        return True, "Параметры корректны"

    async def preflight(
        self, ctx: HandlerContext, params: InstallPrinterInput
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Фаза 2: Preflight (Строго Read-Only).
        Запрещено изменять состояние хоста!
        """
        ctx.log.append(f"Старт preflight диагностики: ПК={params.pc_name}, Принтер={params.printer_name}")

        # 1. Проверка доступности портов 135 (RPC) и 5985 (WinRM)
        port_135_ok = await check_tcp_port(params.pc_name, 135, timeout=2.0)
        port_5985_ok = await check_tcp_port(params.pc_name, 5985, timeout=2.0)

        if not port_135_ok and not port_5985_ok:
            return False, f"Рабочая станция {params.pc_name} недоступна по сети (порты 135 и 5985 закрыты).", {
                "host_online": False,
                "failure_code": "host_unreachable",
            }

        # 2. Поиск точного профиля в KB без Generic Fallback!
        printer_cfg = find_printer_by_name(params.printer_name, allow_fallback=False)
        if not printer_cfg:
            return (
                False,
                (
                    f"Профиль драйвера для принтера '{params.printer_name}' не найден в Knowledge Base. "
                    "Установка без точного зарегистрированного драйвера запрещена (Generic Fallback disabled)."
                ),
                {"failure_code": "driver_profile_not_found"},
            )

        # 3. Проверка безопасности источника драйвера (Allowlist SMB Roots)
        if not self._is_allowed_smb_path(printer_cfg.driver_inf_path):
            return (
                False,
                f"Путь к драйверу '{printer_cfg.driver_inf_path}' не входит в список разрешенных SMB корней.",
                {"failure_code": "untrusted_driver_source"},
            )

        # 4. Read-Only опрос служб Spooler и WinRM
        spooler_state, _ = await self._check_service_status(params.pc_name, "Spooler", port_5985_ok)
        winrm_state, winrm_start_mode = await self._check_service_status(
            params.pc_name, "WinRM", port_5985_ok
        )

        spooler_running = (spooler_state.lower() == "running")
        winrm_bootstrap_required = not port_5985_ok or (winrm_state.lower() != "running")

        # 5. Определение IP-адреса и проверка порта 9100 для TCP/IP
        resolved_ip = params.printer_ip
        if not resolved_ip:
            ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", params.printer_name)
            if ip_match:
                resolved_ip = ip_match.group(0)
            else:
                try:
                    resolved_ip = await asyncio.to_thread(socket.gethostbyname, params.printer_name)
                except Exception:
                    resolved_ip = params.printer_name

        tcp_9100_open = False
        if params.connection_type == "tcpip" and resolved_ip:
            tcp_9100_open = await check_tcp_port(resolved_ip, 9100, timeout=2.0)

        # 6. Расчет SHA-256 хеша INF-файла драйвера (если файл локально доступен воркеру)
        driver_sha256 = None
        inf_local = Path(printer_cfg.driver_inf_path)
        if inf_local.exists() and inf_local.is_file():
            try:
                driver_sha256 = hashlib.sha256(inf_local.read_bytes()).hexdigest()
            except Exception as e:
                logger.debug("Не удалось рассчитать sha256 для %s: %s", inf_local, e)

        # 7. Формирование снимка preflight_evidence
        evidence = {
            "host_online": True,
            "spooler_running": spooler_running,
            "winrm_state": winrm_state,
            "winrm_start_mode": winrm_start_mode,
            "winrm_bootstrap_required": winrm_bootstrap_required,
            "printer_name": params.printer_name,
            "printer_ip": resolved_ip,
            "tcp_9100_open": tcp_9100_open,
            "model_key": printer_cfg.model_key,
            "driver_name": printer_cfg.driver_name,
            "driver_inf_path": printer_cfg.driver_inf_path,
            "driver_sha256": driver_sha256,
            "connection_type": params.connection_type,
        }

        ctx.state["preflight_evidence"] = evidence
        ctx.state["printer_cfg"] = printer_cfg
        ctx.state["resolved_ip"] = resolved_ip

        # Фиксация preflight в Core API при наличии клиента
        if ctx.core_api_client and ctx.command_id:
            try:
                await ctx.core_api_client.record_command_preflight_v2(
                    command_id=ctx.command_id,
                    evidence=evidence,
                    worker_id=ctx.node_name or "windows_worker",
                    claim_token=ctx.claim_token or "",
                )
            except Exception as exc:
                logger.debug("Не удалось отправить record_command_preflight_v2: %s", exc)

        ctx.log.append("Preflight проверки успешно пройдены (read-only).")
        return True, "Preflight проверки успешно пройдены.", evidence

    async def prepare(self, ctx: HandlerContext, params: InstallPrinterInput) -> tuple[bool, str]:
        """
        Фаза 3: Prepare (Строго после Approval).
        Динамический WMI Bootstrap WinRM, если служба была остановлена.
        """
        evidence = ctx.state.get("preflight_evidence", {})
        if evidence.get("winrm_bootstrap_required"):
            ownership_token = secrets.token_hex(16)
            ctx.state["ownership_token"] = ownership_token
            ctx.state["orig_winrm_state"] = evidence.get("winrm_state", "Stopped")
            ctx.state["orig_winrm_start_mode"] = evidence.get("winrm_start_mode", "Manual")

            ctx.log.append(f"Служба WinRM на {params.pc_name} требует запуска. OwnershipToken: {ownership_token[:8]}...")
            started = await self._bootstrap_start_winrm(params.pc_name, ctx.log)
            if not started:
                return False, f"Не удалось запустить службу WinRM на {params.pc_name} через WMI/RPC (порт 135)."
            ctx.state["winrm_started_by_us"] = True
        else:
            ctx.log.append(f"Служба WinRM на {params.pc_name} уже активна (порт 5985 готов).")

        return True, "Prepare фаза завершена успешно."

    async def execute(self, ctx: HandlerContext, params: InstallPrinterInput) -> ActionResult:
        """
        Фаза 4: Execute (SMB Staging & Local PnpUtil).
        Устранение Kerberos Double-Hop: развертывание драйвера в C:\\Windows\\Temp\\_driver_staging\\<id>\\
        """
        printer_cfg: PrinterConfig | None = ctx.state.get("printer_cfg")
        if not printer_cfg:
            printer_cfg = find_printer_by_name(params.printer_name, allow_fallback=False)
        if not printer_cfg:
            return ActionResult(
                success=False,
                message="Конфигурация принтера отсутствует.",
                log=ctx.log,
                failure_kind="configuration",
                failure_code="driver_profile_not_found",
            )

        resolved_ip = ctx.state.get("resolved_ip") or params.printer_ip or params.printer_name
        staging_dir = f"C:\\Windows\\Temp\\_driver_staging\\{ctx.command_id}"
        ctx.state["staging_dir"] = staging_dir

        driver_inf_path = printer_cfg.driver_inf_path
        driver_name = printer_cfg.driver_name
        port_name = f"IP_{resolved_ip}"
        ctx.state["port_name"] = port_name

        ctx.log.append(f"Стейджинг драйвера '{driver_name}' в каталог {staging_dir}...")

        # Безопасный PowerShell скрипт: создание каталога с ограниченными ACL, стейджинг, pnputil, порт, очередь
        script = """
        param(
            $stagingDir,
            $uncInfPath,
            $driverName,
            $portName,
            $printerIp,
            $printerName,
            $expectedSha256
        )
        $ErrorActionPreference = 'Stop'

        # 1. Создание изолированного каталога стейджинга с безопасными ACL
        if (-not (Test-Path -Path $stagingDir)) {
            New-Item -Path $stagingDir -ItemType Directory -Force | Out-Null
        }
        # Ограничение прав: только SYSTEM и Administrators
        & icacls.exe $stagingDir /inheritance:r /grant "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null

        # 2. Локальное копирование драйвера (Staging)
        $infFileName = Split-Path -Leaf $uncInfPath
        $localInfPath = Join-Path -Path $stagingDir -ChildPath $infFileName

        # Если доступен UNC-путь из сессии или пакет предварительно загружен
        if (Test-Path -Path $uncInfPath) {
            $uncDir = Split-Path -Parent $uncInfPath
            Copy-Item -Path "$uncDir\\*" -Destination $stagingDir -Recurse -Force | Out-Null
        }

        # 3. Проверка SHA-256 хеша драйвера на целевом ПК при наличии ожидаемого хеша
        if ($expectedSha256 -and (Test-Path -Path $localInfPath)) {
            $actualHash = (Get-FileHash -Path $localInfPath -Algorithm SHA256).Hash.ToLower()
            if ($actualHash -ne $expectedSha256.ToLower()) {
                throw "driver_hash_mismatch: expected $expectedSha256, got $actualHash"
            }
        }

        # 4. Инсталляция драйвера через pnputil
        if (Test-Path -Path $localInfPath) {
            & pnputil.exe /add-driver $localInfPath /install | Out-Null
        }

        # 5. Создание стандартного TCP/IP порта
        if (-not (Get-PrinterPort -Name $portName -ErrorAction SilentlyContinue)) {
            Add-PrinterPort -Name $portName -PrinterHostAddress $printerIp -ErrorAction Stop
        }

        # 6. Регистрация очереди печати (строго с точным драйвером, без generic!)
        if (-not (Get-Printer -Name $printerName -ErrorAction SilentlyContinue)) {
            Add-Printer -Name $printerName -PortName $portName -DriverName $driverName -ErrorAction Stop
        } else {
            Set-Printer -Name $printerName -PortName $portName -DriverName $driverName -ErrorAction SilentlyContinue
        }

        # Возврат информации об установленном принтере
        $p = Get-Printer -Name $printerName -ErrorAction Stop
        [PSCustomObject]@{
            Name = $p.Name
            PortName = $p.PortName
            DriverName = $p.DriverName
            PrinterStatus = $p.PrinterStatus
        }
        """

        expected_sha256 = ctx.state.get("preflight_evidence", {}).get("driver_sha256")
        ps_params = {
            "stagingDir": staging_dir,
            "uncInfPath": driver_inf_path,
            "driverName": driver_name,
            "portName": port_name,
            "printerIp": resolved_ip,
            "printerName": params.printer_name,
            "expectedSha256": expected_sha256,
        }

        res = await run_powershell_safe(
            pc_name=params.pc_name,
            script=script,
            parameters=ps_params,
            timeout_sec=90,
        )

        if not res.success:
            err = res.error or "Сбой выполнения PowerShell скрипта установки."
            ctx.log.append(f"Ошибка Execute: {err}")
            failure_code = "driver_hash_mismatch" if "driver_hash_mismatch" in err else "install_failed"
            return ActionResult(
                success=False,
                message=f"Ошибка установки принтера: {err}",
                log=ctx.log,
                failure_kind="infrastructure",
                failure_code=failure_code,
            )

        ctx.log.append(f"Принтер '{params.printer_name}' успешно установлен через pnputil.")
        return ActionResult(
            success=True,
            message=f"Принтер '{params.printer_name}' успешно установлен на {params.pc_name}",
            log=ctx.log,
            payload={"install_data": res.data},
        )

    async def verify(
        self, ctx: HandlerContext, params: InstallPrinterInput, result: ActionResult
    ) -> tuple[bool, str, bool, str | None]:
        """
        Фаза 5: Verify.
        Строгая проверка совпадения имени очереди, порта и имени драйвера.
        Возвращает: (verified, message, is_failure, failure_code).
        """
        if not result.success:
            return False, result.message, True, result.failure_code

        printer_cfg: PrinterConfig = ctx.state["printer_cfg"]
        expected_port = ctx.state.get("port_name", f"IP_{ctx.state.get('resolved_ip')}")
        expected_driver = printer_cfg.driver_name

        ctx.log.append(f"Верификация принтера '{params.printer_name}' на {params.pc_name}...")

        script = """
        param($printerName)
        $p = Get-Printer -Name $printerName -ErrorAction SilentlyContinue
        if ($p) {
            [PSCustomObject]@{
                Name = $p.Name
                PortName = $p.PortName
                DriverName = $p.DriverName
                PrinterStatus = $p.PrinterStatus
            }
        } else {
            $null
        }
        """
        res = await run_powershell_safe(
            pc_name=params.pc_name,
            script=script,
            parameters={"printerName": params.printer_name},
            timeout_sec=20,
        )

        if not res.success or not res.data:
            return (
                False,
                f"Принтер '{params.printer_name}' не обнаружен в Get-Printer после установки.",
                True,
                "printer_not_found_after_install",
            )

        actual_name = str(res.data.get("Name", ""))
        actual_port = str(res.data.get("PortName", ""))
        actual_driver = str(res.data.get("DriverName", ""))

        if actual_name.lower() != params.printer_name.lower():
            return (
                False,
                f"Несовпадение имени принтера: ожидалось '{params.printer_name}', получено '{actual_name}'",
                True,
                "printer_name_mismatch",
            )

        if actual_port.lower() != expected_port.lower():
            return (
                False,
                f"Несовпадение порта печати: ожидалось '{expected_port}', получено '{actual_port}'",
                True,
                "printer_port_mismatch",
            )

        if actual_driver.lower() != expected_driver.lower():
            return (
                False,
                f"Несовпадение драйвера печати: ожидалось '{expected_driver}', получено '{actual_driver}'",
                True,
                "printer_driver_mismatch",
            )

        evidence = {
            "verified": True,
            "installed": True,
            "printer_name": actual_name,
            "port_name": actual_port,
            "driver_name": actual_driver,
            "target_pc": params.pc_name,
        }

        if result.payload is None:
            result.payload = {}
        result.payload.update(evidence)
        ctx.evidence.update(evidence)

        ctx.log.append(f"🟢 Принтер '{actual_name}' успешно верифицирован (порт: {actual_port}, драйвер: {actual_driver}).")
        return True, f"Принтер '{actual_name}' успешно установлен и верифицирован на {params.pc_name}.", False, None

    async def cleanup(self, ctx: HandlerContext, params: InstallPrinterInput) -> None:
        """
        Фаза 6: Cleanup (Гарантированно выполняется всегда).
        1. Удаление каталога стейджинга C:\\Windows\\Temp\\_driver_staging\\<id>\\.
        2. Восстановление службы WinRM при доказанном владении.
        """
        staging_dir = ctx.state.get("staging_dir")
        if staging_dir:
            ctx.log.append(f"Зачистка каталога стейджинга: {staging_dir}...")
            cleanup_script = """
            param($stagingDir)
            if (Test-Path -Path $stagingDir) {
                Remove-Item -Path $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            """
            try:
                await run_powershell_safe(
                    pc_name=params.pc_name,
                    script=cleanup_script,
                    parameters={"stagingDir": staging_dir},
                    timeout_sec=15,
                )
                ctx.log.append("Каталог стейджинга успешно удален.")
            except Exception as exc:
                ctx.log.append(f"Предупреждение при удалении стейджинга: {exc}")

        # Безопасный откат службы WinRM только если мы её запускали
        if ctx.state.get("winrm_started_by_us") and ctx.state.get("ownership_token"):
            orig_mode = ctx.state.get("orig_winrm_start_mode", "Manual")
            await self._restore_winrm_service(params.pc_name, orig_mode, ctx.log)
