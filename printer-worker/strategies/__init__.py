from typing import Dict, Type
from .base import PrinterStrategy
from orchestrator.schemas import ConnectionType

_strategies_registry: Dict[ConnectionType, Type[PrinterStrategy]] = {}


def strategy(connection_type: ConnectionType):
    """
    Декоратор для регистрации стратегий установки принтеров.
    """

    def decorator(cls: Type[PrinterStrategy]):
        _strategies_registry[connection_type] = cls
        return cls

    return decorator


def get_strategy(connection_type: ConnectionType) -> PrinterStrategy:
    """
    Возвращает экземпляр нужной стратегии по типу подключения.
    """
    strategy_cls = _strategies_registry.get(connection_type)
    if not strategy_cls:
        raise NotImplementedError(
            f"Стратегия для типа подключения {connection_type} не зарегистрирована"
        )
    return strategy_cls()


from . import tcpip_strategy as tcpip_strategy  # noqa: E402, F401
from . import usb_strategy as usb_strategy  # noqa: E402, F401
