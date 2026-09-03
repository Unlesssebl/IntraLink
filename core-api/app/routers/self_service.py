import json
import logging
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/run", tags=["self-service"])


class RunTokenCompleteRequest(BaseModel):
    status: str = Field(..., description="Статус выполнения (success / error)")
    pc: str = Field(..., description="Имя компьютера, на котором выполнен скрипт")
    details: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


async def get_redis_client() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def create_printer_run_token(
    task_id: int,
    pc_name: str,
    printer_name: str,
    driver_name: str,
    ip_address: str,
    ttl_seconds: int = 7200,
) -> str:
    """Создает защищенный токен самообслуживания для установки принтера при закрытом WinRM."""
    token = str(uuid.uuid4())
    redis_client = await get_redis_client()
    try:
        data = {
            "token": token,
            "task_id": task_id,
            "pc_name": pc_name,
            "printer_name": printer_name,
            "driver_name": driver_name,
            "ip_address": ip_address,
        }
        await redis_client.setex(f"run:token:{token}", ttl_seconds, json.dumps(data))
        return token
    finally:
        await redis_client.aclose()


@router.get("/{token}")
async def get_self_service_script(token: str, request: Request):
    """
    Генерирует и отдает динамический PowerShell-скрипт установки принтера по токену.
    Вызывается клиентом через One-Liner (например, irm http://.../api/v1/run/<token> | iex).
    """
    redis_client = await get_redis_client()
    try:
        raw = await redis_client.get(f"run:token:{token}")
        if not raw:
            return Response(
                content="Write-Error 'Срок действия ссылки истек или токен не найден.'",
                media_type="text/plain; charset=utf-8",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        data = json.loads(raw)
    finally:
        await redis_client.aclose()

    base_url = str(request.base_url).rstrip("/")
    callback_url = f"{base_url}/api/v1/run/{token}/complete"

    printer_name = data.get("printer_name", "Сетевой принтер")
    ip_address = data.get("ip_address", "")
    driver_name = data.get("driver_name", "")

    # Генерируем локальный PowerShell скрипт установки принтера
    script = f"""# IntraLink Self-Service Printer Installer (One-Liner Fallback)
$ErrorActionPreference = 'Stop'
Write-Host ">>> Запуск автоматической установки принтера: {printer_name}..." -ForegroundColor Cyan

$PrinterName = "{printer_name}"
$PrinterIP = "{ip_address}"
$DriverName = "{driver_name}"
$CallbackUrl = "{callback_url}"

try {{
    # 1. Создание TCP/IP порта, если его нет
    $PortName = "IP_$PrinterIP"
    $ExistingPort = Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
    if (-not $ExistingPort) {{
        Write-Host "Создание TCP/IP порта $PortName ($PrinterIP)..."
        Add-PrinterPort -Name $PortName -PrinterHostAddress $PrinterIP
    }}

    # 2. Добавление принтера
    $ExistingPrinter = Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
    if (-not $ExistingPrinter) {{
        Write-Host "Регистрация принтера $PrinterName..."
        if ($DriverName) {{
            Add-Printer -Name $PrinterName -PortName $PortName -DriverName $DriverName -ErrorAction SilentlyContinue
        }}
        if (-not (Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue)) {{
            # Generic / стандартный fallback драйвер
            Add-Printer -Name $PrinterName -PortName $PortName -DriverName "Generic / Text Only" -ErrorAction SilentlyContinue
        }}
    }}

    Write-Host ">>> Принтер успешно установлен и готов к работе!" -ForegroundColor Green

    # 3. Отправка подтверждения об успехе в Core API
    $Payload = @{{
        status = "success"
        pc = $env:COMPUTERNAME
        details = @{{ printer = $PrinterName; port = $PortName }}
    }} | ConvertTo-Json

    Invoke-RestMethod -Uri $CallbackUrl -Method POST -Body $Payload -ContentType "application/json" -TimeoutSec 10 | Out-Null
}} catch {{
    Write-Host ">>> Ошибка при установке: $($_.Exception.Message)" -ForegroundColor Red
    $ErrorPayload = @{{
        status = "error"
        pc = $env:COMPUTERNAME
        error_message = $_.Exception.Message
    }} | ConvertTo-Json
    Invoke-RestMethod -Uri $CallbackUrl -Method POST -Body $ErrorPayload -ContentType "application/json" -ErrorAction SilentlyContinue | Out-Null
}}
"""
    return Response(content=script, media_type="text/plain; charset=utf-8")


@router.post("/{token}/complete")
async def complete_self_service_run(token: str, body: RunTokenCompleteRequest):
    """
    Принимает отчет об успешном или неуспешном исполнении скрипта от клиентского ПК.
    """
    redis_client = await get_redis_client()
    try:
        raw = await redis_client.get(f"run:token:{token}")
        if not raw:
            raise HTTPException(status_code=404, detail="Токен недействителен или истек")
        data = json.loads(raw)
        logger.info(
            "Self-service task %s completed on PC %s with status: %s",
            data.get("task_id"),
            body.pc,
            body.status,
        )
        # Удаляем одноразовый токен после обработки
        await redis_client.delete(f"run:token:{token}")
        return {"received": True, "task_id": data.get("task_id"), "status": body.status}
    finally:
        await redis_client.aclose()
