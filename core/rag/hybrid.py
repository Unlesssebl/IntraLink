"""Hybrid Search Fusion Engine (Dense + Sparse FTS) with Reciprocal Rank Fusion (RRF).

Combines semantic vector search (pgvector HNSW) and lexical full-text search
(PostgreSQL tsvector / ts_rank) with quality filtering.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("core.rag.hybrid")

DEFAULT_RRF_K = 60
DEFAULT_WEIGHT_DENSE = 0.6
DEFAULT_WEIGHT_SPARSE = 0.4

# Default boilerplate phrases that indicate non-actionable or junk solutions
JUNK_SOLUTION_PATTERNS = [
    re.compile(r"^(выполнен[оа]|сделан[оа]|решен[оа]|ок|готово|закрыт[оа]|исправлен[оа])\.?$", re.IGNORECASE),
    re.compile(r"^решено по телефону\.?$", re.IGNORECASE),
    re.compile(r"^передан[оа] на вторую линию\.?$", re.IGNORECASE),
    re.compile(r"^в работе\.?$", re.IGNORECASE),
    re.compile(r"^дубликат\.?$", re.IGNORECASE),
]


def is_valid_solution_source(
    solution: str,
    min_length: int = 25,
    extra_blacklist: Optional[List[str]] = None,
) -> bool:
    """Verify that a resolution text contains meaningful technical instructions.

    Filters out terse tickets like 'Сделано', 'Ок', 'Выполнено' or empty text.
    """
    if not solution or not solution.strip():
        return False

    cleaned = solution.strip()
    if len(cleaned) < min_length:
        return False

    for pattern in JUNK_SOLUTION_PATTERNS:
        if pattern.match(cleaned):
            return False

    if extra_blacklist:
        cleaned_lower = cleaned.lower()
        if any(token.lower() in cleaned_lower for token in extra_blacklist):
            return False

    return True


def reciprocal_rank_fusion(
    dense_ids: List[int],
    sparse_ids: List[int],
    k: int = DEFAULT_RRF_K,
    weight_dense: float = DEFAULT_WEIGHT_DENSE,
    weight_sparse: float = DEFAULT_WEIGHT_SPARSE,
) -> List[Tuple[int, float]]:
    """Compute Reciprocal Rank Fusion (RRF) scores across dense and sparse ranked lists.

    Formula:
        RRF(d) = (weight_dense / (k + rank_dense(d))) + (weight_sparse / (k + rank_sparse(d)))

    Ranks are 1-based index (top match has rank=1).
    Returns list of (task_id, rrf_score) sorted by rrf_score descending.
    """
    scores: Dict[int, float] = {}

    for rank, task_id in enumerate(dense_ids, start=1):
        dense_contrib = weight_dense / (k + rank)
        scores[task_id] = scores.get(task_id, 0.0) + dense_contrib

    for rank, task_id in enumerate(sparse_ids, start=1):
        sparse_contrib = weight_sparse / (k + rank)
        scores[task_id] = scores.get(task_id, 0.0) + sparse_contrib

    sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results
