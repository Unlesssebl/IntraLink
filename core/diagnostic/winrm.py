"""WinRM PowerShell execution client for remote workstation diagnostics and management."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional, Tuple

import requests
import winrm
import winrm.exceptions

logger = logging.getLogger("core.diagnostic.winrm")


class WinRMExecutor:
    """Thread-safe WinRM PowerShell execution engine for Windows workstations.

    All blocking I/O calls to pywinrm are isolated via asyncio.to_thread.
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        transport: Optional[str] = None,
        port: int = 5985,
    ) -> None:
        self.username = username or os.getenv("WINRM_USERNAME", "administrator")
        self.password = password or os.getenv("WINRM_PASSWORD", "")
        self.transport = transport or os.getenv("WINRM_TRANSPORT", "ntlm")
        self.port = port

    def _get_session(self, target_pc: str) -> winrm.Session:
        endpoint = f"http://{target_pc}:{self.port}/wsman"
        return winrm.Session(
            endpoint,
            auth=(self.username, self.password),
            transport=self.transport,
            server_cert_validation="ignore",
            read_timeout_sec=70,
            operation_timeout_sec=60,
        )

    def run_powershell_sync(
        self,
        target_pc: str,
        script: str,
        retries: int = 1,
    ) -> Tuple[int, str, str]:
        """Synchronously execute PowerShell script on target PC with automatic UTF-8 encoding."""
        clean_pc = target_pc.strip()
        utf8_prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        full_script = utf8_prefix + script

        for attempt in range(retries + 1):
            try:
                session = self._get_session(clean_pc)
                rs = session.run_ps(full_script)
                std_out = rs.std_out.decode("utf-8", errors="replace") if rs.std_out else ""
                std_err = rs.std_err.decode("utf-8", errors="replace") if rs.std_err else ""
                return rs.status_code, std_out, std_err
            except (
                winrm.exceptions.WinRMTransportError,
                ConnectionError,
                requests.exceptions.RequestException,
            ) as exc:
                if attempt < retries:
                    logger.warning(
                        "Transient WinRM connection failure to %s (attempt %d/%d): %s",
                        clean_pc,
                        attempt + 1,
                        retries,
                        exc,
                    )
                    time.sleep(1.0)
                else:
                    logger.error(
                        "WinRM connection to %s failed after %d attempts: %s",
                        clean_pc,
                        retries + 1,
                        exc,
                    )
                    return -1, "", str(exc)
            except Exception as exc:
                logger.error("Critical WinRM error on %s: %s", clean_pc, exc, exc_info=True)
                return -1, "", str(exc)

        return -1, "", "Exhausted retries"

    async def run_powershell(
        self,
        target_pc: str,
        script: str,
        timeout_sec: float = 60.0,
    ) -> Tuple[int, str, str]:
        """Asynchronously execute PowerShell script on remote PC wrapped in asyncio.to_thread."""
        logger.info("WinRM executing PowerShell on %s", target_pc)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.run_powershell_sync, target_pc, script),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.error("Timeout (%.1fs) executing WinRM PowerShell on %s", timeout_sec, target_pc)
            return -1, "", f"Таймаут выполнения команды ({timeout_sec} сек)"


default_winrm_executor = WinRMExecutor()
