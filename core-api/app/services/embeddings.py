import logging
from openai import AsyncOpenAI
from app.config import settings

logger = logging.getLogger(__name__)

# Инициализируем асинхронного клиента OpenAI для работы с LiteLLM Proxy
client = AsyncOpenAI(
    api_key=settings.LITELLM_API_KEY,
    base_url=settings.LITELLM_BASE_URL
)

async def get_embedding(text: str, model: str = None) -> list[float]:
    """
    Генерирует вектор эмбеддингов для переданного текста через LiteLLM.
    Повторные попытки при Rate Limit (429) обрабатываются автоматически на стороне LiteLLM Proxy.
    """
    if not text:
        return []
        
    embedding_model = model or settings.EMBEDDING_MODEL
    try:
        response = await client.embeddings.create(
            input=[text],
            model=embedding_model
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(
            "Ошибка при генерации эмбеддингов для текста через LiteLLM: %s. Модель: %s",
            e, embedding_model
        )
        raise e
