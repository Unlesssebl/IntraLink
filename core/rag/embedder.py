"""RAG embedding generation via LiteLLM Gateway (BGE-M3, 1024 dim)."""

import logging
from typing import List, Optional

from openai import AsyncOpenAI

from core.rag.embed_cache import get_embedding_cache

logger = logging.getLogger("core.rag.embedder")
EMBEDDING_MODEL = "bge-m3"
EMBEDDING_DIM = 1024


async def get_embedding_vector(
    text: str,
    ai_client: AsyncOpenAI,
    model_name: str = EMBEDDING_MODEL,
    use_cache: bool = True,
) -> Optional[List[float]]:
    """Generate normalized 1024-dimensional embedding vector using LiteLLM Gateway (cached)."""
    if not text or not text.strip():
        return None

    cleaned_text = text.strip()[:4000]

    cache = get_embedding_cache() if use_cache else None
    if cache is not None:
        cached_vec = await cache.get(cleaned_text, model_name)
        if cached_vec is not None:
            return cached_vec

    try:
        response = await ai_client.embeddings.create(
            input=cleaned_text,
            model=model_name,
            encoding_format="float",
        )
        if response.data and len(response.data) > 0:
            vec = response.data[0].embedding
            if cache is not None and vec:
                await cache.set(cleaned_text, vec, model_name)
            return vec
    except Exception as exc:
        logger.warning(f"Failed to generate embedding via LiteLLM ({model_name}): {exc}")

    return None

