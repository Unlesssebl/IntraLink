"""Knowledge Base feature router."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.db import get_db_session

from .schemas import (
    AskQuery,
    CreateKBItemRequest,
    KBStatusResponse,
    SearchQuery,
    SearchResponse,
    SolutionResponse,
)
from .service import KnowledgeBaseService

router = APIRouter(prefix="/kb", tags=["Knowledge Base"])


def get_kb_service() -> KnowledgeBaseService:
    return KnowledgeBaseService()


@router.post("/search", response_model=SearchResponse)
async def search_kb(
    req: SearchQuery,
    service: KnowledgeBaseService = Depends(get_kb_service),
    db: AsyncSession = Depends(get_db_session),
) -> SearchResponse:
    """Semantic vector search for similar solutions in pgvector."""
    items = await service.search(req=req, session=db)
    return SearchResponse(query=req.query, total_found=len(items), items=items)


@router.post("/ask", response_model=SolutionResponse)
async def ask_kb(
    req: AskQuery,
    service: KnowledgeBaseService = Depends(get_kb_service),
    db: AsyncSession = Depends(get_db_session),
) -> SolutionResponse:
    """RAG solution synthesis via LiteLLM Gateway."""
    return await service.synthesize_solution(req=req, session=db)


@router.post("/items")
async def create_kb_item(
    req: CreateKBItemRequest,
    service: KnowledgeBaseService = Depends(get_kb_service),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Add or update a verified technical solution in the knowledge base."""
    item = await service.create_or_update_item(req=req, session=db)
    return {"task_id": item.task_id, "status": "indexed"}


@router.get("/status", response_model=KBStatusResponse)
async def get_kb_status(
    service: KnowledgeBaseService = Depends(get_kb_service),
    db: AsyncSession = Depends(get_db_session),
) -> KBStatusResponse:
    """Get knowledge base vector index status."""
    return await service.get_status(session=db)
