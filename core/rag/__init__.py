"""RAG and semantic vector search core."""

from core.rag.embed_cache import EmbeddingCache, InMemoryLRUCache, get_embedding_cache, set_embedding_cache
from core.rag.embedder import EMBEDDING_DIM, EMBEDDING_MODEL, get_embedding_vector
from core.rag.hybrid import is_valid_solution_source, reciprocal_rank_fusion
from core.rag.sanitizer import PIISanitizer, SanitizationResult, sanitize_text, truncate_log_dump
from core.rag.search import (
    KnowledgeSolutionDTO,
    check_semantic_duplicate,
    search_hybrid_solutions,
    search_lexical_solutions,
    search_similar_solutions,
)
from core.rag.sync import KBSyncStatsDTO, KnowledgeBaseSyncService, evaluate_solution_quality

__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "get_embedding_vector",
    "EmbeddingCache",
    "InMemoryLRUCache",
    "get_embedding_cache",
    "set_embedding_cache",
    "KnowledgeSolutionDTO",
    "search_similar_solutions",
    "search_lexical_solutions",
    "search_hybrid_solutions",
    "check_semantic_duplicate",
    "reciprocal_rank_fusion",
    "is_valid_solution_source",
    "PIISanitizer",
    "SanitizationResult",
    "sanitize_text",
    "truncate_log_dump",
    "KnowledgeBaseSyncService",
    "KBSyncStatsDTO",
    "evaluate_solution_quality",
]
