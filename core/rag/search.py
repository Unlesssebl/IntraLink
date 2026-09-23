"""Semantic vector search in pgvector knowledge base."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import TaskKnowledgeBase


class KnowledgeSolutionDTO(BaseModel):
    task_id: int
    original_name: str
    problem: str
    solution: str
    service_id: int
    service_name: str
    status_name: str
    quality_score: float
    similarity: float
    classification_data: Dict[str, Any] = {}


async def search_similar_solutions(
    session: AsyncSession,
    query_vector: List[float],
    limit: int = 5,
    min_similarity: float = 0.65,
    service_id: Optional[int] = None,
) -> List[KnowledgeSolutionDTO]:
    """Execute pgvector cosine distance query against task_knowledge_base using HNSW index."""
    if not query_vector:
        return []

    # Cosine distance operator <=> in pgvector
    distance_col = TaskKnowledgeBase.embedding.cosine_distance(query_vector).label("distance")

    stmt = (
        select(TaskKnowledgeBase, distance_col)
        .where(TaskKnowledgeBase.is_blacklisted.is_(False))
        .where(TaskKnowledgeBase.embedding.isnot(None))
    )

    if service_id is not None:
        stmt = stmt.where(TaskKnowledgeBase.service_id == service_id)

    stmt = stmt.order_by(distance_col).limit(limit)

    result = await session.execute(stmt)
    rows = result.all()

    solutions: List[KnowledgeSolutionDTO] = []
    for item, dist in rows:
        similarity = 1.0 - float(dist)
        if similarity >= min_similarity:
            solutions.append(
                KnowledgeSolutionDTO(
                    task_id=item.task_id,
                    original_name=item.original_name,
                    problem=item.problem,
                    solution=item.solution,
                    service_id=item.service_id,
                    service_name=item.service_name,
                    status_name=item.status_name,
                    quality_score=item.quality_score,
                    similarity=similarity,
                    classification_data=item.classification_data or {},
                )
            )

    return solutions
