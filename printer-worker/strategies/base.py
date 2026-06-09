from abc import ABC, abstractmethod
from orchestrator.schemas import PrintJob

class PrinterStrategy(ABC):
    @abstractmethod
    async def probe(self, job: PrintJob) -> PrintJob:
        """
        Проводит опрос удаленной системы перед началом установки.
        Определяет необходимость копирования драйверов и проверяет подключенные устройства.
        """
        pass

    @abstractmethod
    async def execute(self, job: PrintJob) -> PrintJob:
        """
        Запускает полный процесс установки принтера на удаленном хосте.
        """
        pass
