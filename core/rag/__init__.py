"""RAG and semantic vector search core."""

from core.rag.embedder import EMBEDDING_DIM, EMBEDDING_MODEL, get_embedding_vector
from core.rag.sanitizer import PIISanitizer, SanitizationResult, sanitize_text, truncate_log_dump
from core.rag.search import KnowledgeSolutionDTO, check_semantic_duplicate, search_similar_solutions
from core.rag.sync import KBSyncStatsDTO, KnowledgeBaseSyncService, evaluate_solution_quality

__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "get_embedding_vector",
    "KnowledgeSolutionDTO",
    "search_similar_solutions",
    "check_semantic_duplicate",
    "PIISanitizer",
    "SanitizationResult",
    "sanitize_text",
    "truncate_log_dump",
    "KnowledgeBaseSyncService",
    "KBSyncStatsDTO",
    "evaluate_solution_quality",
]



