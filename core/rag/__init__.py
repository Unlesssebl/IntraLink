"""RAG and semantic vector search core."""

from core.rag.embedder import EMBEDDING_DIM, EMBEDDING_MODEL, get_embedding_vector
from core.rag.search import KnowledgeSolutionDTO, search_similar_solutions

__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "get_embedding_vector",
    "KnowledgeSolutionDTO",
    "search_similar_solutions",
]
