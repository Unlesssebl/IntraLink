"""Semantic vector search and hybrid fusion in pgvector / PostgreSQL FTS knowledge base."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import TaskKnowledgeBase
from core.rag.hybrid import (
    DEFAULT_RRF_K,
    DEFAULT_WEIGHT_DENSE,
    DEFAULT_WEIGHT_SPARSE,
    is_valid_solution_source,
    reciprocal_rank_fusion,
)

logger = logging.getLogger("core.rag.search")


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

    bind = session.bind or (session.get_bind() if hasattr(session, "get_bind") else None)
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
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
    else:
        # SQLite / Mock in-memory cosine fallback for tests
        stmt = (
            select(TaskKnowledgeBase)
            .where(TaskKnowledgeBase.is_blacklisted.is_(False))
            .where(TaskKnowledgeBase.embedding.isnot(None))
        )
        if service_id is not None:
            stmt = stmt.where(TaskKnowledgeBase.service_id == service_id)

        res = await session.execute(stmt)
        items = res.scalars().all()

        scored: List[KnowledgeSolutionDTO] = []
        norm_q = sum(a * a for a in query_vector) ** 0.5
        if norm_q <= 0:
            return []

        for item in items:
            emb = item.embedding
            if not emb or len(emb) != len(query_vector):
                continue
            dot = sum(a * b for a, b in zip(query_vector, emb, strict=False))
            norm_e = sum(b * b for b in emb) ** 0.5
            if norm_e > 0:
                sim = dot / (norm_q * norm_e)
                if sim >= min_similarity:
                    scored.append(
                        KnowledgeSolutionDTO(
                            task_id=item.task_id,
                            original_name=item.original_name,
                            problem=item.problem,
                            solution=item.solution,
                            service_id=item.service_id,
                            service_name=item.service_name,
                            status_name=item.status_name,
                            quality_score=item.quality_score,
                            similarity=sim,
                            classification_data=item.classification_data or {},
                        )
                    )
        scored.sort(key=lambda x: x.similarity, reverse=True)
        return scored[:limit]


async def search_lexical_solutions(
    session: AsyncSession,
    query_text: str,
    limit: int = 5,
    service_id: Optional[int] = None,
) -> List[KnowledgeSolutionDTO]:
    """Execute Full-Text Search query against task_knowledge_base."""
    if not query_text or not query_text.strip():
        return []

    bind = session.bind or (session.get_bind() if hasattr(session, "get_bind") else None)
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        ts_query = func.plainto_tsquery("russian", query_text)
        rank_col = func.ts_rank_cd(TaskKnowledgeBase.search_vector, ts_query).label("rank")

        stmt = (
            select(TaskKnowledgeBase, rank_col)
            .where(TaskKnowledgeBase.is_blacklisted.is_(False))
            .where(TaskKnowledgeBase.search_vector.isnot(None))
            .where(TaskKnowledgeBase.search_vector.op("@@")(ts_query))
        )
        if service_id is not None:
            stmt = stmt.where(TaskKnowledgeBase.service_id == service_id)

        stmt = stmt.order_by(rank_col.desc()).limit(limit)
        result = await session.execute(stmt)
        rows = result.all()

        solutions: List[KnowledgeSolutionDTO] = []
        for item, rank in rows:
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
                    similarity=min(float(rank), 1.0),
                    classification_data=item.classification_data or {},
                )
            )
        return solutions
    else:
        # SQLite / Mock lexical token-matching fallback for unit tests
        tokens = [t.lower() for t in re.findall(r"\w+", query_text) if len(t) >= 2]
        if not tokens:
            return []

        stmt = select(TaskKnowledgeBase).where(TaskKnowledgeBase.is_blacklisted.is_(False))
        if service_id is not None:
            stmt = stmt.where(TaskKnowledgeBase.service_id == service_id)

        res = await session.execute(stmt)
        items = res.scalars().all()

        scored: List[KnowledgeSolutionDTO] = []
        for item in items:
            corpus = f"{item.original_name} {item.problem} {item.solution}".lower()
            hit_count = sum(1 for token in tokens if token in corpus)
            if hit_count > 0:
                sim = min(hit_count / len(tokens), 1.0)
                scored.append(
                    KnowledgeSolutionDTO(
                        task_id=item.task_id,
                        original_name=item.original_name,
                        problem=item.problem,
                        solution=item.solution,
                        service_id=item.service_id,
                        service_name=item.service_name,
                        status_name=item.status_name,
                        quality_score=item.quality_score,
                        similarity=sim,
                        classification_data=item.classification_data or {},
                    )
                )
        scored.sort(key=lambda x: x.similarity, reverse=True)
        return scored[:limit]


async def search_hybrid_solutions(
    session: AsyncSession,
    query_text: str,
    query_vector: Optional[List[float]],
    limit: int = 5,
    min_similarity: float = 0.65,
    service_id: Optional[int] = None,
    weight_dense: float = DEFAULT_WEIGHT_DENSE,
    weight_sparse: float = DEFAULT_WEIGHT_SPARSE,
    k: int = DEFAULT_RRF_K,
    validate_quality: bool = True,
) -> List[KnowledgeSolutionDTO]:
    """Execute Hybrid (Dense + Sparse) search fused with Reciprocal Rank Fusion (RRF)."""
    # 1. Fetch dense candidates if query_vector provided
    dense_results: List[KnowledgeSolutionDTO] = []
    if query_vector:
        dense_results = await search_similar_solutions(
            session=session,
            query_vector=query_vector,
            limit=limit * 2,
            min_similarity=min_similarity,
            service_id=service_id,
        )

    # 2. Fetch sparse (FTS) candidates
    sparse_results: List[KnowledgeSolutionDTO] = []
    if query_text and query_text.strip():
        sparse_results = await search_lexical_solutions(
            session=session,
            query_text=query_text,
            limit=limit * 2,
            service_id=service_id,
        )

    # If only one channel returned results, return it directly
    if not sparse_results and dense_results:
        return [
            d for d in dense_results
            if not validate_quality or is_valid_solution_source(d.solution)
        ][:limit]

    if not dense_results and sparse_results:
        return [
            s for s in sparse_results
            if not validate_quality or is_valid_solution_source(s.solution)
        ][:limit]

    if not dense_results and not sparse_results:
        return []

    # 3. Reciprocal Rank Fusion
    dto_map: Dict[int, KnowledgeSolutionDTO] = {}
    dense_ids: List[int] = []
    sparse_ids: List[int] = []

    for d in dense_results:
        dto_map[d.task_id] = d
        dense_ids.append(d.task_id)

    for s in sparse_results:
        if s.task_id not in dto_map:
            dto_map[s.task_id] = s
        sparse_ids.append(s.task_id)

    fused_ranks = reciprocal_rank_fusion(
        dense_ids=dense_ids,
        sparse_ids=sparse_ids,
        k=k,
        weight_dense=weight_dense,
        weight_sparse=weight_sparse,
    )

    # 4. Construct final sorted results with quality check
    final_solutions: List[KnowledgeSolutionDTO] = []
    for task_id, rrf_score in fused_ranks:
        dto = dto_map[task_id]
        if validate_quality and not is_valid_solution_source(dto.solution):
            logger.debug("Ticket #%d skipped by solution quality validator", task_id)
            continue

        # Preserve original cosine similarity if available, else derive from RRF
        effective_sim = dto.similarity if dto.similarity > 0 else min(rrf_score * 20.0, 1.0)
        final_solutions.append(
            KnowledgeSolutionDTO(
                task_id=dto.task_id,
                original_name=dto.original_name,
                problem=dto.problem,
                solution=dto.solution,
                service_id=dto.service_id,
                service_name=dto.service_name,
                status_name=dto.status_name,
                quality_score=dto.quality_score,
                similarity=effective_sim,
                classification_data=dto.classification_data,
            )
        )

        if len(final_solutions) >= limit:
            break

    return final_solutions


async def check_semantic_duplicate(
    session: AsyncSession,
    query_vector: List[float],
    service_id: Optional[int] = None,
    threshold: float = 0.90,
) -> bool:
    """Check if a semantically similar solution already exists in KB (Cosine Gate > threshold)."""
    if not query_vector:
        return False

    bind = session.bind or (session.get_bind() if hasattr(session, "get_bind") else None)
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        distance_col = TaskKnowledgeBase.embedding.cosine_distance(query_vector).label("distance")
        stmt = (
            select(distance_col)
            .where(TaskKnowledgeBase.is_blacklisted.is_(False))
            .where(TaskKnowledgeBase.embedding.isnot(None))
        )
        if service_id is not None:
            stmt = stmt.where(TaskKnowledgeBase.service_id == service_id)

        stmt = stmt.order_by(distance_col).limit(1)
        res = await session.execute(stmt)
        row = res.first()
        if row is not None:
            dist = row[0]
            if (1.0 - float(dist)) >= threshold:
                return True
        return False
    else:
        # SQLite / Mock fallback for tests without native pgvector <=> operator
        stmt = (
            select(TaskKnowledgeBase.embedding)
            .where(TaskKnowledgeBase.is_blacklisted.is_(False))
            .where(TaskKnowledgeBase.embedding.isnot(None))
        )
        if service_id is not None:
            stmt = stmt.where(TaskKnowledgeBase.service_id == service_id)

        res = await session.execute(stmt)
        rows = res.all()
        for (emb,) in rows:
            if emb is None or len(emb) == 0:
                continue
            dot = sum(a * b for a, b in zip(query_vector, emb, strict=False))
            norm_q = sum(a * a for a in query_vector) ** 0.5
            norm_e = sum(b * b for b in emb) ** 0.5
            if norm_q > 0 and norm_e > 0:
                sim = dot / (norm_q * norm_e)
                if sim >= threshold:
                    return True
        return False
