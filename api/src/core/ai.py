"""LiteLLM Gateway Client (AsyncOpenAI).

In IntraLink v2, custom LLM plumbing is replaced by LiteLLM Proxy.
All backend feature slices communicate with LiteLLM purely via official AsyncOpenAI client.
"""

from typing import Optional

from openai import AsyncOpenAI

from api.src.core.config import settings

_ai_client: Optional[AsyncOpenAI] = None


def get_ai_client() -> AsyncOpenAI:
    """Return the shared AsyncOpenAI client connected to LiteLLM Gateway."""
    global _ai_client
    if _ai_client is None:
        _ai_client = AsyncOpenAI(
            base_url=settings.LITELLM_BASE_URL,
            api_key=settings.LITELLM_API_KEY,
            timeout=60.0,
            max_retries=2,
        )
    return _ai_client


# Convenience aliases for model profiles defined in litellm_config.yaml
MODEL_FAST = settings.LITELLM_MODEL_FAST
MODEL_REASONING = settings.LITELLM_MODEL_REASONING
