from abc import ABC, abstractmethod
from orchestrator.schemas import LLMParseResult

class LLMProvider(ABC):
    @abstractmethod
    async def parse_task_text(self, text: str) -> LLMParseResult:
        """
        Принимает текст заявки и извлекает параметры подключения принтера.
        Возвращает объект LLMParseResult.
        """
        pass
