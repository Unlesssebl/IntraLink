from .base import LLMProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider
from worker_config import LLM_PROVIDER

_provider_instance = None


def get_provider() -> LLMProvider:
    global _provider_instance
    if _provider_instance is None:
        provider_name = LLM_PROVIDER.lower().strip()
        if provider_name == "ollama":
            _provider_instance = OllamaProvider()
        elif provider_name == "openai":
            _provider_instance = OpenAIProvider()
        else:
            raise ValueError(f"Unknown LLM provider: {LLM_PROVIDER}")
    return _provider_instance
