"""
Пакет обработчиков действий (ActionHandlers) для Worker SDK.
"""

from handlers.install_printer import InstallPrinterHandler
from handlers.create_user import CreateUserHandler
from handlers.grant_wlan import GrantWlanHandler

__all__ = ["InstallPrinterHandler", "CreateUserHandler", "GrantWlanHandler"]

