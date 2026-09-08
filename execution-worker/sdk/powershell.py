"""
Двухконтурный безопасный PowerShell Runner для Worker SDK.
Исключает любую строковую интерполяцию пользовательских данных:
- Контур 1 (Python -> Local): параметры передаются строго через stdin в формате JSON.
- Контур 2 (Local -> WinRM): аргументы в Invoke-Command передаются строго через -ArgumentList.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("execution_worker.sdk.powershell")


@dataclass
class PowerShellResult:
    """Результат выполнения скрипта PowerShell."""
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    parsed_json: Any | None = None
    success: bool | None = None
    data: Any | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.success is not None:
            self.return_code = 0 if self.success else (self.return_code or 1)
        else:
            self.success = (self.return_code == 0)
        if self.data is not None and self.parsed_json is None:
            self.parsed_json = self.data
        elif self.parsed_json is not None and self.data is None:
            self.data = self.parsed_json
        if self.error is not None and not self.stderr:
            self.stderr = self.error
        elif self.return_code != 0 and self.error is None:
            self.error = self.stderr or self.stdout or f"PowerShell process failed with code {self.return_code}"


def find_powershell_executable() -> str:
    """Определяет доступный исполняемый файл PowerShell (powershell.exe или pwsh)."""
    for candidate in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
        path = shutil.which(candidate)
        if path:
            return path
    return "powershell.exe"


def encode_powershell_script(script: str) -> str:
    """Кодирует фиксированный текст скрипта в UTF-16LE Base64 для флага -EncodedCommand."""
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


async def run_powershell_safe(
    script: str,
    payload: dict[str, Any] | None = None,
    *,
    cancellation_token: asyncio.Event | None = None,
    timeout_seconds: float = 180.0,
    parse_json_output: bool = True,
    powershell_exe: str | None = None,
) -> PowerShellResult:
    """
    Безопасно выполняет фиксированный скрипт PowerShell с передачей параметров через stdin.

    Параметры payload сериализуются в JSON и передаются через поток stdin подпроцесса.
    Скрипт должен считывать stdin, например:
        $raw = [Console]::In.ReadToEnd()
        $data = $raw | ConvertFrom-Json
    """
    executable = powershell_exe or find_powershell_executable()
    encoded_cmd = encode_powershell_script(script)

    stdin_data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")

    cmd_args = [
        executable,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded_cmd,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    communicate_task = asyncio.create_task(proc.communicate(input=stdin_data))
    tasks_to_wait: list[asyncio.Task[Any]] = [communicate_task]

    cancel_task: asyncio.Task[Any] | None = None
    if cancellation_token is not None:
        async def _wait_for_cancel():
            await cancellation_token.wait()
        cancel_task = asyncio.create_task(_wait_for_cancel())
        tasks_to_wait.append(cancel_task)

    try:
        done, pending = await asyncio.wait(
            tasks_to_wait,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if not done:
            # Превышен таймаут
            logger.warning("PowerShell process timed out after %s seconds, killing", timeout_seconds)
            _kill_proc(proc)
            await proc.wait()
            raise TimeoutError(f"PowerShell command timed out after {timeout_seconds}s")

        if cancel_task and cancel_task in done:
            # Активирован токен отмены
            logger.info("PowerShell execution cancelled via cancellation_token, killing process")
            _kill_proc(proc)
            await proc.wait()
            raise asyncio.CancelledError("PowerShell process terminated by cancellation token")

        stdout_bytes, stderr_bytes = await communicate_task
        stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()
        return_code = proc.returncode or 0

        parsed_json = None
        if parse_json_output and stdout_str:
            try:
                parsed_json = json.loads(stdout_str)
            except Exception:
                # Вывод не является чистым JSON (возможно, смешанный текст или предупреждения)
                pass

        return PowerShellResult(
            return_code=return_code,
            stdout=stdout_str,
            stderr=stderr_str,
            parsed_json=parsed_json,
        )

    finally:
        for t in (communicate_task, cancel_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass


def _kill_proc(proc: asyncio.subprocess.Process) -> None:
    """Принудительно убивает процесс и подавляет ProcessLookupError."""
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass
