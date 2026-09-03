"""
Модуль удаленной установки и диагностики принтеров в среде Windows (WinRM, SMB, WMI).
Наследуется от BaseActionExecutor с полным жизненным циклом Preflight ➔ Execute ➔ Verify.
"""

import asyncio
import logging
import re
import socket
from pathlib import Path
from typing import Any

from executors.base import ActionResult, BaseActionExecutor
from shared.printers import find_printer_by_name, load_printers_kb

logger = logging.getLogger("execution_worker.executors.printers")

# Алиас для обратной совместимости
PrinterExecutionResult = ActionResult


class PrinterExecutor(BaseActionExecutor):
    """
    Исполнитель удаленной установки и диагностики принтеров на рабочих станциях.
    Реализует жизненный цикл BaseActionExecutor:
    1. Preflight: WMI Bootstrap WinRM, валидация хоста, поиск в KB принтеров, резолв IP принтера.
    2. Execute: развертывание драйвера из UNC-шары, создание TCP/IP порта, регистрация очереди печати.
    3. Verify: проверка присутствия принтера в системе и его готовности.
    """

    def __init__(self, redis_client=None):
        super().__init__(redis_client=redis_client)

    # -----------------------------------------------------------------------
    # Preflight
    # -----------------------------------------------------------------------

    async def preflight(
        self, target_pc: str, log: list[str], **kwargs
    ) -> tuple[bool, str]:
        printer_name = kwargs.get("printer_name", "")
        printer_ip = kwargs.get("printer_ip")

        if not target_pc or not printer_name:
            return False, "Параметры target_pc и printer_name обязательны."

        log.append(f"Целевой ПК: {target_pc}, запрашиваемый принтер: {printer_name}")

        # 1. Проверка доступности и Bootstrap WinRM
        winrm_ready = await self.bootstrap_winrm(target_pc, log)
        if not winrm_ready:
            return False, f"Хост {target_pc} недоступен по WinRM (5985) и WMI (135)."

        # 2. Поиск профиля принтера в SSOT базе знаний
        printer_cfg = find_printer_by_name(printer_name)
        if not printer_cfg:
            log.append(f"⚠️ Профиль для принтера '{printer_name}' не найден в KB. Будет использован стандартный драйвер.")
        else:
            log.append(f"Найден профиль KB: {printer_cfg.display_name} (Драйвер: {printer_cfg.driver_name})")

        # 3. Резолвинг IP-адреса принтера
        resolved_ip = printer_ip
        if not resolved_ip:
            # Попытка извлечь IP или хостнейм принтера из названия очереди
            ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", printer_name)
            if ip_match:
                resolved_ip = ip_match.group(0)
            else:
                try:
                    resolved_ip = await asyncio.to_thread(socket.gethostbyname, printer_name)
                except Exception:
                    # Если сетевое имя принтера резолвится в домене
                    pass

        if not resolved_ip:
            log.append(f"ℹ️ Прямой IP принтера не указан. Установка будет выполнена по сетевому имени {printer_name}.")
            resolved_ip = printer_name

        kwargs["_resolved_ip"] = resolved_ip
        kwargs["_printer_cfg"] = printer_cfg
        return True, "Preflight проверки пройдены успешно."

    # -----------------------------------------------------------------------
    # Execute
    # -----------------------------------------------------------------------

    async def execute(
        self, target_pc: str, log: list[str], **kwargs
    ) -> ActionResult:
        printer_name = kwargs.get("printer_name", "")
        printer_ip = kwargs.get("_resolved_ip") or kwargs.get("printer_ip") or printer_name
        printer_cfg = kwargs.get("_printer_cfg") or find_printer_by_name(printer_name)

        driver_name = printer_cfg.driver_name if printer_cfg else "HP Universal Printing PCL 6"
        inf_path = printer_cfg.driver_inf_path if printer_cfg else ""
        port_name = f"IP_{printer_ip}"

        log.append(f"Установка порта {port_name} ({printer_ip}) и драйвера '{driver_name}'...")

        # Формирование безопасного PowerShell скрипта установки
        ps_script = f"""
        # 1. Инсталляция драйвера при наличии INF в сетевой шаре
        $inf = "{inf_path}"
        if ($inf -and (Test-Path $inf)) {{
            Write-Output "INF драйвера найден: $inf"
            pnputil.exe /add-driver $inf /install | Out-Null
        }}

        # 2. Создание стандартного TCP/IP порта
        $portName = "{port_name}"
        $ip = "{printer_ip}"
        if (-not (Get-PrinterPort -Name $portName -ErrorAction SilentlyContinue)) {{
            Add-PrinterPort -Name $portName -PrinterHostAddress $ip -ErrorAction Stop
            Write-Output "Порт $portName успешно создан."
        }}

        # 3. Регистрация очереди печати
        $pName = "{printer_name}"
        $drv = "{driver_name}"
        if (-not (Get-Printer -Name $pName -ErrorAction SilentlyContinue)) {{
            # Если драйвер еще не зарегистрирован, пробуем добавить с доступным или generic
            try {{
                Add-Printer -Name $pName -PortName $portName -DriverName $drv -ErrorAction Stop
            }} catch {{
                # Fallback на Generic / Text Only при отсутствии точного драйвера
                Add-Printer -Name $pName -PortName $portName -DriverName "Generic / Text Only" -ErrorAction SilentlyContinue
            }}
            Write-Output "Очередь печати $pName зарегистрирована."
        }} else {{
            Write-Output "Очередь $pName уже существовала, обновлен порт."
            Set-Printer -Name $pName -PortName $portName -ErrorAction SilentlyContinue
        }}

        Get-Printer -Name $pName | Select-Object Name, PortName, DriverName, PrinterStatus
        """

        res = await self.run_remote_powershell(target_pc, ps_script, timeout=60)
        if not res.get("success"):
            err_msg = res.get("error", "Неизвестная ошибка PowerShell")
            log.append(f"Ошибка выполнения PowerShell: {err_msg}")
            return ActionResult(
                success=False,
                message=f"Ошибка установки принтера: {err_msg}",
                error=err_msg,
                log=log,
            )

        log.append("Скрипт PowerShell выполнен успешно.")
        return ActionResult(
            success=True,
            message=f"Принтер '{printer_name}' успешно установлен на {target_pc}",
            log=log,
            payload={"raw_output": res.get("data") or res.get("raw")},
        )

    # -----------------------------------------------------------------------
    # Verify
    # -----------------------------------------------------------------------

    async def verify(
        self, target_pc: str, log: list[str], **kwargs
    ) -> tuple[bool, str]:
        printer_name = kwargs.get("printer_name", "")
        log.append(f"Проверка наличия принтера '{printer_name}' в системе {target_pc}...")

        script = f"Get-Printer -Name '*{printer_name}*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name"
        res = await self.run_remote_powershell(target_pc, script, timeout=20)

        if res.get("success") and res.get("data"):
            found_name = res["data"]
            log.append(f"🟢 Подтверждено: принтер '{found_name}' обнаружен в списке устройств.")
            return True, f"Принтер {found_name} активен."

        # Если имя отличается по маске, проверим общий список принтеров
        fallback_script = "Get-Printer | Select-Object -ExpandProperty Name"
        fallback_res = await self.run_remote_powershell(target_pc, fallback_script, timeout=20)
        if fallback_res.get("success") and fallback_res.get("data"):
            printers_list = fallback_res["data"]
            if isinstance(printers_list, str):
                printers_list = [printers_list]
            for p in printers_list:
                if printer_name.lower() in p.lower():
                    log.append(f"🟢 Подтверждено по совпадению: принтер '{p}' активен.")
                    return True, f"Принтер {p} активен."

        return False, f"Принтер '{printer_name}' не найден в выводе Get-Printer после установки."

    # -----------------------------------------------------------------------
    # Главная точка входа для Windows Execution Worker
    # -----------------------------------------------------------------------

    async def install_printer(
        self, target_pc: str, printer_name: str, printer_ip: str | None = None
    ) -> ActionResult:
        """
        Удаленная установка принтера с полным циклом Preflight ➔ Execute ➔ Verify.
        Метод вызывается из worker.py.
        """
        return await self.run_action(
            target_pc=target_pc,
            printer_name=printer_name,
            printer_ip=printer_ip,
        )

    # -----------------------------------------------------------------------
    # Обратная совместимость с ранее существовавшими методами
    # -----------------------------------------------------------------------

    async def check_printer_installed(self, target_pc: str, printer_name: str) -> bool:
        script = f"Get-Printer -Name '*{printer_name}*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name"
        res = await self.run_remote_powershell(target_pc, script)
        return bool(res.get("success") and res.get("data"))

    async def install_network_printer(
        self,
        target_pc: str,
        printer_ip: str,
        driver_name: str,
        printer_name: str,
    ) -> ActionResult:
        return await self.install_printer(
            target_pc=target_pc,
            printer_name=printer_name,
            printer_ip=printer_ip,
        )
