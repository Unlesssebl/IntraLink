"""RAG embedding generation via LiteLLM Gateway (BGE-M3, 1024 dim)."""

import logging
from typing import List, Optional

from openai import AsyncOpenAI

logger = logging.getLogger("core.rag.embedder")
EMBEDDING_MODEL = "bge-m3"
EMBEDDING_DIM = 1024


async def get_embedding_vector(
    text: str,
    ai_client: AsyncOpenAI,
    model_name: str = EMBEDDING_MODEL,
) -> Optional[List[float]]:
    """Generate normalized 1024-dimensional embedding vector using LiteLLM Gateway."""
    if not text or not text.strip():
        return None

    cleaned_text = text.strip()[:4000]

    try:
        response = await ai_client.embeddings.create(
            input=cleaned_text,
            model=model_name,
            encoding_format="float",
        )
        if response.data and len(response.data) > 0:
            vec = response.data[0].embedding
            return vec
    except Exception as exc:
        logger.warning(f"Failed to generate embedding via LiteLLM ({model_name}): {exc}")

    return None
