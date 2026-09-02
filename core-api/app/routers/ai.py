"""
Роутер централизованного AI Hub для клиентов (Web UI, Helpdesk CLI, Telegram Bot, AGY Skills).
Включает роутинг данных по контурам безопасности (Red / Yellow / Green) и десенсибилизацию PII.
"""
import logging
from typing import Dict
from fastapi import APIRouter, Depends, HTTPException, status

from app.routers.deps import verify_admin_or_api_key
from app.services.ai import (
    AIAnalysisResult,
    AIHealthResponse,
    RoutedInferenceRequest,
    RoutedInferenceResponse,
    SanitizePreviewRequest,
    SanitizePreviewResponse,
    TaskAnalysisRequest,
    TaskSummaryRequest,
    TicketSummaryResult,
    ai_hub,
    data_sanitizer,
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
    "/generate",
    response_model=RoutedInferenceResponse,
    status_code=status.HTTP_200_OK,
)
async def generate_routed_completion(payload: RoutedInferenceRequest):
    """
    Универсальная генерация текста с автоматическим определением контура безопасности:
    - RED (Закрытый): Строго локальная LLM (Ollama).
    - YELLOW (Трансформируемый): Авто-маскирование PII в Redis Vault -> Cloud Gemini -> Rehydration.
    - GREEN (Открытый): Прямой инференс через Cloud Gemini API.
    """
    result = await ai_hub.dispatch_routed_inference(payload)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Все сконфигурированные AI-бэкенды (Ollama / LiteLLM) временно недоступны.",
        )
    return result


@router.post(
    "/sanitize-preview",
    response_model=SanitizePreviewResponse,
    status_code=status.HTTP_200_OK,
)
async def sanitize_preview(payload: SanitizePreviewRequest):
    """
    Предварительный просмотр десенсибилизации текста и решения маршрутизатора контуров.
    """
    san_res = data_sanitizer.sanitize(payload.text)
    route_dec = data_sanitizer.evaluate_circuit(
        prompt=payload.text,
        metadata=payload.metadata,
        sanitization_result=san_res,
    )
    return SanitizePreviewResponse(
        original_text=payload.text,
        sanitized_text=san_res.sanitized_text,
        entity_map=san_res.entity_map,
        detected_types=san_res.detected_types,
        route_decision=route_dec,
    )


@router.post(
    "/deanonymize",
    response_model=dict,
    status_code=status.HTTP_200_OK,
)
async def deanonymize_text(payload: dict):
    """
    Восстанавливает исходные данные в замаскированном тексте по переданной таблице сущностей.
    """
    text = payload.get("text", "")
    entity_map = payload.get("entity_map", {})
    restored = data_sanitizer.deanonymize(text, entity_map)
    return {"restored_text": restored}


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
