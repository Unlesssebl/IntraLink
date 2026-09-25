"""Backwards-compatibility re-exports for InstallPrinterScenario (canonical: core.scenarios.adapters.install_printer)."""

from core.scenarios.adapters.install_printer import (
    PRINTER_INSTALL_SERVICE_IDS,
    InstallPrinterScenario,
)

__all__ = [
    "InstallPrinterScenario",
    "PRINTER_INSTALL_SERVICE_IDS",
]
