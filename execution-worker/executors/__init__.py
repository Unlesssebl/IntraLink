"""
Модули реального исполнения и интеграции с инфраструктурой (Active Directory, WinRM, WMI, SMB).
"""
from .ad import (
    ActiveDirectoryExecutor,
    ADExecutionResult,
    ADUserStatus,
    ADUserProfile,
    ADUserCreationResult,
)
from .printers import PrinterExecutor, PrinterExecutionResult

__all__ = [
    "ActiveDirectoryExecutor",
    "ADExecutionResult",
    "ADUserStatus",
    "ADUserProfile",
    "ADUserCreationResult",
    "PrinterExecutor",
    "PrinterExecutionResult",
]
