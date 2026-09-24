"""Semantic RAG consultation autonomous scenario."""

import logging
from typing import Optional

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.autopilot.dto import AutopilotPolicyDTO
from core.database.session import get_engine, get_session_factory
from core.database.system_state import _get_active_session_factory
from core.intraservice.dto import TaskDTO
from core.rag.embedder import get_embedding_vector
from core.rag.search import KnowledgeSolutionDTO, search_similar_solutions
from worker.src.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("worker.scenarios.rag_consultation")


class RAGConsultationScenario(BaseScenario):
    """Autonomous consultation scenario resolving common inquiries from TaskKnowledgeBase."""

    scenario_key = "rag_consultation"
    name = "RAG Консультация"
    description = "Автоматические консультации по типовым вопросам на базе базы знаний"

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        ai_client: Optional[AsyncOpenAI] = None,
    ) -> None:
        self.session_factory = session_factory
        self.ai_client = ai_client
        self._cached_solution: Optional[KnowledgeSolutionDTO] = None

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is not None:
            return self.session_factory
        try:
            return _get_active_session_factory()
        except Exception:
            engine = get_engine()
            return get_session_factory(engine)

    def _get_ai_client(self) -> AsyncOpenAI:
        if self.ai_client is not None:
            return self.ai_client
        return AsyncOpenAI(base_url="http://litellm:4000/v1", api_key="sk-intralink-dummy")

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket can be resolved via knowledge base consultation."""
        text = f"{task.name} {task.description}".lower()

        # Audio and direct printer installations/hardware tasks have specialized scenarios
        if any(w in text for w in ("установить принтер", "подключить принтер", "сброс пароля", "забыл пароль")):
            return False

        # Generic consulting tokens or questions
        consulting_tokens = (
            "как ", "где ", "подскажите", "инструкци", "настройк", "правила",
            "доступ", "заявк", "парол", "почт", "directum", "1с", "outlook",
            "vpn", "wifi", "wlan", "печать", "сканир"
        )
        return any(tok in text for tok in consulting_tokens) or len(text) > 10

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate if a high-confidence semantic match exists in TaskKnowledgeBase."""
        factory = self._get_session_factory()
        ai = self._get_ai_client()

        query_text = f"{task.name}\n{task.description}".strip()
        vector = await get_embedding_vector(query_text, ai_client=ai)

        if not vector:
            return PreconditionResult(
                is_valid=False,
                missing_facts=["embedding_unavailable"],
            )

        async with factory() as session:
            matches = await search_similar_solutions(
                session=session,
                query_vector=vector,
                limit=1,
                min_similarity=0.80,
                service_id=task.service_id,
            )

            if not matches:
                # Fallback to unrestricted search across all services
                matches = await search_similar_solutions(
                    session=session,
                    query_vector=vector,
                    limit=1,
                    min_similarity=0.80,
                    service_id=None,
                )

        if not matches:
            return PreconditionResult(
                is_valid=False,
                missing_facts=["knowledge_base_match"],
            )

        self._cached_solution = matches[0]
        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Post verified historical solution from knowledge base."""
        solution_dto = self._cached_solution
        if solution_dto is None:
            # Re-evaluate if not cached
            precond = await self.validate_preconditions(task)
            if not precond.is_valid or self._cached_solution is None:
                return ScenarioExecutionResult(
                    success=False,
                    action_taken="rag_consultation",
                    resolution_comment="",
                    technical_note="⚠️ [Автопилот: RAG] Высокоточный прецедент в базе знаний не найден.",
                    target_status_id=2,  # In progress
                    error="no_high_confidence_solution",
                )
            solution_dto = self._cached_solution

        logger.info(
            "Executing RAGConsultationScenario for task #%d using historical solution from task #%d (sim=%.2f)",
            task.id,
            solution_dto.task_id,
            solution_dto.similarity,
        )

        res_comment = (
            f"Здравствуйте! По вашему обращению направляем типовую инструкцию решения:\n\n"
            f"{solution_dto.solution}\n\n"
            "Если предложенная инструкция помогла решить вопрос, заявка считается выполненной. "
            "Если вопрос остался открытым, пожалуйста, ответьте на это сообщение."
        )
        tech_note = (
            f"🤖 [Автопилот: RAG Консультация]\n"
            f"Прецедент: Тикет #{solution_dto.task_id} ('{solution_dto.original_name}')\n"
            f"Сервис: {solution_dto.service_name} (ID: {solution_dto.service_id})\n"
            f"Семантическое сходство: {solution_dto.similarity:.1%}\n"
            f"Порог политики: {policy.min_confidence:.1%}\n"
            f"Качество решения: {solution_dto.quality_score:.2f}\n"
            f"Статус: Выполнена (Status 3)"
        )

        return ScenarioExecutionResult(
            success=True,
            action_taken="rag_consultation",
            resolution_comment=res_comment,
            technical_note=tech_note,
            target_status_id=3,
            metadata={
                "matched_task_id": solution_dto.task_id,
                "similarity": solution_dto.similarity,
            },
        )
