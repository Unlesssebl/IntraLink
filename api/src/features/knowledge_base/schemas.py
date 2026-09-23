"""Pydantic schemas for Knowledge Base and RAG feature slice."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000, description="Search query text")
    limit: int = Field(default=5, ge=1, le=20)
    service_id: Optional[int] = None
    min_similarity: float = Field(default=0.60, ge=0.0, le=1.0)


class SearchResultItemDTO(BaseModel):
    task_id: int
    original_name: str
    problem: str
    solution: str
    service_id: int
    service_name: str
    quality_score: float
    similarity: float
    classification_data: Dict[str, Any] = {}


class SearchResponse(BaseModel):
    query: str
    total_found: int
    items: List[SearchResultItemDTO]


class AskQuery(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000, description="Problem description")
    ticket_id: Optional[int] = None
    service_id: Optional[int] = None
    max_context_items: int = Field(default=3, ge=1, le=10)


class SolutionResponse(BaseModel):
    answer: str
    cited_tasks: List[int]
    confidence: float
    model_used: str
    context_used: List[SearchResultItemDTO]


class CreateKBItemRequest(BaseModel):
    task_id: int
    original_name: str
    problem: str
    solution: str
    service_id: int
    service_name: str
    service_path: Optional[str] = None
    status_name: str = "Закрыта"
    quality_score: float = 1.0
    classification_data: Dict[str, Any] = Field(default_factory=dict)


class KBStatusResponse(BaseModel):
    total_solutions: int
    indexed_vectors: int
    embedding_dimension: int
    model_name: str
    status: str
