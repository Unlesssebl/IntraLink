"""
Роутер централизованного AI Hub для клиентов (Web UI, Helpdesk CLI, Telegram Bot, AGY Skills).
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status

from app.routers.deps import verify_admin_or_api_key
from app.services.ai import (
    AIAnalysisResult,
    AIHealthResponse,
    TaskAnalysisRequest,
    TaskSummaryRequest,
    TicketSummaryResult,
    ai_hub,
)

logger = logging.getLogger("core_api.routers.ai")

router = APIRouter(
    prefix="/api/v1/ai",
    tags=["Centralized AI Hub"],
    dependencies=[Depends(verify_admin_or_api_key)],
)


@router.get("/health", response_model=AIHealthResponse, status_code=status.HTTP_200_OK)
async def check_ai_health():
    """
    Проверяет доступность подключенных AI-бэкендов (Ollama, LiteLLM).
    """
    return await ai_hub.get_health()


@router.post(
    "/summarize",
    response_model=TicketSummaryResult,
    status_code=status.HTTP_200_OK,
)
async def summarize_ticket(payload: TaskSummaryRequest):
    """
    Формирует структурированную выжимку цепочки переписки инцидента через локальную LLM (Ollama).
    Использует кэширование в Redis (Pre-Summarization).
    """
    result = await ai_hub.summarize_task_history(
        task_id=payload.task_id,
        task_name=payload.task_name,
        task_desc=payload.task_desc,
        comments=payload.comments,
        bypass_cache=payload.bypass_cache,
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервис AI суммаризации (Ollama) временно недоступен или произошел сбой инференса.",
        )
    return result


@router.post(
    "/analyze",
    response_model=AIAnalysisResult,
    status_code=status.HTTP_200_OK,
)
async def analyze_ticket(payload: TaskAnalysisRequest):
    """
    Глубокий анализ нетиповой заявки с извлечением сущностей и рекомендацией ответа.
    """
    result = await ai_hub.analyze_complex_task(
        task_id=payload.task_id,
        task_name=payload.task_name,
        task_desc=payload.task_desc,
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервис AI анализа (Ollama) временно недоступен.",
        )
    return result
