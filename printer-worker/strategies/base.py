from abc import ABC, abstractmethod
from orchestrator.schemas import PrintJob


def escape_ps(text: str) -> str:
    """
    Экранирует одинарные кавычки для безопасной подстановки параметров в PowerShell скрипты.
    Вызывается перед подстановкой любой строки в PS-скрипт в одинарных кавычках.
    """
    return text.replace("'", "''")


class PrinterStrategy(ABC):
    @abstractmethod
    async def probe(self, job: PrintJob) -> PrintJob:
        """
        Проводит опрос удаленной системы перед началом установки.
        Определяет необходимость копирования драйверов и проверяет подключенные устройства.
        Результат опроса сохраняется в полях job (driver_installed, driver_info и т.д.),
        а не в error_message — он зарезервирован исключительно для сообщений об ошибках.
        """
        pass

    @abstractmethod
    async def execute(self, job: PrintJob) -> PrintJob:
        """
        Запускает полный процесс установки принтера на удаленном хосте.
        Предполагает, что probe() уже был выполнен оркестратором — повторный вызов не нужен.
        """
        pass
