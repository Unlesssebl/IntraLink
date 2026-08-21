"""
Пакет локального AI инференса через Ollama (микро-модели Qwen2.5).
"""
from .schemas import TicketSummaryResult, AIAnalysisResult
from .ollama_client import OllamaClient

__all__ = ["OllamaClient", "TicketSummaryResult", "AIAnalysisResult"]
