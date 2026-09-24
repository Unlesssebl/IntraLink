"""RAG Knowledge Base business logic and synthesis service."""

import logging
from typing import List, Optional

from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.ai import MODEL_REASONING, get_ai_client
from core.database.models import EMBEDDING_DIM, TaskKnowledgeBase
from core.rag import get_embedding_vector, search_hybrid_solutions

from .prompts import (
    RAG_SYSTEM_PROMPT,
    RAG_USER_PROMPT_TEMPLATE,
)
from .schemas import (
    AskQuery,
    CreateKBItemRequest,
    KBStatusResponse,
    SearchQuery,
    SearchResultItemDTO,
    SolutionResponse,
)

logger = logging.getLogger("api.features.kb")


class KnowledgeBaseService:
    """Service handling semantic search and AI solution synthesis."""

    def __init__(self, ai_client: Optional[AsyncOpenAI] = None) -> None:
        self.ai_client = ai_client or get_ai_client()

    async def search(
        self,
        req: SearchQuery,
        session: AsyncSession,
    ) -> List[SearchResultItemDTO]:
        # 1. Generate query vector using LiteLLM (with transparent caching)
        query_vector = await get_embedding_vector(req.query, self.ai_client)

        # 2. Execute Hybrid Search (Dense + FTS RRF with quality verification)
        results = await search_hybrid_solutions(
            session=session,
            query_text=req.query,
            query_vector=query_vector,
            limit=req.limit,
            min_similarity=req.min_similarity,
            service_id=req.service_id,
            validate_quality=True,
        )


        return [
            SearchResultItemDTO(
                task_id=r.task_id,
                original_name=r.original_name,
                problem=r.problem,
                solution=r.solution,
                service_id=r.service_id,
                service_name=r.service_name,
                quality_score=r.quality_score,
                similarity=round(r.similarity, 3),
                classification_data=r.classification_data,
            )
            for r in results
        ]

    async def synthesize_solution(
        self,
        req: AskQuery,
        session: AsyncSession,
    ) -> SolutionResponse:
        # 1. Retrieve most similar historical solutions
        search_results = await self.search(
            SearchQuery(
                query=req.query,
                limit=req.max_context_items,
                service_id=req.service_id,
                min_similarity=0.60,
            ),
            session=session,
        )

        # 2. Build context
        if search_results:
            context_blocks = []
            for idx, item in enumerate(search_results, 1):
                context_blocks.append(
                    f"{idx}. [Заявка #{item.task_id}] Сервис: {item.service_name}\n"
                    f"   Проблема: {item.problem}\n"
                    f"   Решение: {item.solution}\n"
                    f"   Сходство: {int(item.similarity * 100)}%"
                )
            context_text = "\n\n".join(context_blocks)
            avg_confidence = sum(i.similarity for i in search_results) / len(search_results)
            cited_tasks = [i.task_id for i in search_results]
        else:
            context_text = "В базе знаний нет релевантных совпадений с достаточной уверенностью."
            avg_confidence = 0.3
            cited_tasks = []

        user_content = RAG_USER_PROMPT_TEMPLATE.format(
            query=req.query,
            context_items=context_text,
        )

        # 3. Request LLM completion via LiteLLM
        model_name = MODEL_REASONING
        try:
            response = await self.ai_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": RAG_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                max_tokens=1500,
            )
            answer = response.choices[0].message.content or "Не удалось сформировать ответ."
        except Exception as exc:
            logger.error(f"LiteLLM RAG synthesis failed: {exc}")
            answer = f"Ошибка связи с AI-шлюзом: {exc}. Пожалуйста, воспользуйтесь найденными заявками ниже."

        return SolutionResponse(
            answer=answer,
            cited_tasks=cited_tasks,
            confidence=round(avg_confidence, 2),
            model_used=model_name,
            context_used=search_results,
        )

    async def create_or_update_item(
        self,
        req: CreateKBItemRequest,
        session: AsyncSession,
    ) -> TaskKnowledgeBase:
        # Generate embedding for problem + solution
        embed_text = f"{req.problem}\n{req.solution}"
        vec = await get_embedding_vector(embed_text, self.ai_client)

        stmt = select(TaskKnowledgeBase).where(TaskKnowledgeBase.task_id == req.task_id)
        item = (await session.execute(stmt)).scalar_one_or_none()

        if item:
            item.original_name = req.original_name
            item.problem = req.problem
            item.solution = req.solution
            item.service_id = req.service_id
            item.service_name = req.service_name
            item.service_path = req.service_path
            item.status_name = req.status_name
            item.quality_score = req.quality_score
            item.classification_data = req.classification_data
            item.embedding = vec
        else:
            item = TaskKnowledgeBase(
                task_id=req.task_id,
                original_name=req.original_name,
                problem=req.problem,
                solution=req.solution,
                service_id=req.service_id,
                service_name=req.service_name,
                service_path=req.service_path,
                status_name=req.status_name,
                quality_score=req.quality_score,
                classification_data=req.classification_data,
                embedding=vec,
            )
            session.add(item)

        await session.commit()
        await session.refresh(item)
        return item

    async def get_status(self, session: AsyncSession) -> KBStatusResponse:
        total_stmt = select(func.count(TaskKnowledgeBase.task_id))
        total = (await session.execute(total_stmt)).scalar() or 0

        indexed_stmt = select(func.count(TaskKnowledgeBase.task_id)).where(TaskKnowledgeBase.embedding.isnot(None))
        indexed = (await session.execute(indexed_stmt)).scalar() or 0

        return KBStatusResponse(
            total_solutions=total,
            indexed_vectors=indexed,
            embedding_dimension=EMBEDDING_DIM,
            model_name="bge-m3",
            status="healthy",
        )
