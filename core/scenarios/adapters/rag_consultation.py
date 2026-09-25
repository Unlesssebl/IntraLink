import logging
import os
from typing import Dict, Optional

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.autopilot.dto import AutopilotPolicyDTO
from core.database.session import get_engine, get_session_factory
from core.database.system_state import _get_active_session_factory
from core.intraservice.dto import TaskDTO
from core.rag.embedder import get_embedding_vector
from core.rag.search import KnowledgeSolutionDTO, search_hybrid_solutions
from core.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("core.scenarios.adapters.rag_consultation")


class RAGConsultationScenario(BaseScenario):
    """Autonomous consultation scenario resolving common inquiries from TaskKnowledgeBase."""

    scenario_key = "rag_consultation"
    name = "RAG Консультация"
    description = "Автоматические консультации по типовым вопросам на базе базы знаний"
    semantic_prototypes = [
        "не знаю куда обратиться, общий вопрос по работе системы",
        "не работает приложение, непонятная ошибка при запуске",
        "возникла нестандартная ситуация, нужна консультация",
        "медленно работает интернет, теряются пакеты",
        "вопрос по настройке рабочего места, не знаю к кому идти",
    ]

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        ai_client: Optional[AsyncOpenAI] = None,
    ) -> None:
        self.session_factory = session_factory
        self.ai_client = ai_client
        self._solutions_by_task: Dict[int, KnowledgeSolutionDTO] = {}

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
        base_url = os.getenv("LITELLM_BASE_URL", "http://litellm:4000/v1")
        api_key = os.getenv("LITELLM_API_KEY", "sk-intraservice-master-key")
        return AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket can be resolved via knowledge base consultation."""
        text = f"{task.name} {task.description}".lower()

        # Exclude deterministic action domains from RAG fallback
        hardware_and_action_exclusions = (
            "принтер", "мфу", "печать", "печата", "драйвер", "spooler", "спулер",
            "создать учет", "создать учёт", "создать пользователя", "завести сотрудника", "онбординг",
            "увольнен", "уволен", "уволить", "заблокировать", "оффбординг",
            "wlan", "wi-fi", "wifi", "вайфай",
            "не включается", "нет питания", "черный экран", "нет сети"
        )
        if any(ex in text for ex in hardware_and_action_exclusions):
            return False

        # Require genuine consultative / instructional inquiries
        genuine_consulting_tokens = (
            "как ", "где ", "подскажите", "инструкци", "правила",
            "регламент", "консультац", "памятк", "образец", "образца", "порядок действий"
        )
        has_consulting_intent = any(tok in text for tok in genuine_consulting_tokens) or "?" in text
        return has_consulting_intent

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

        try:
            async with factory() as session:
                matches = await search_hybrid_solutions(
                    session=session,
                    query_text=query_text,
                    query_vector=vector,
                    limit=1,
                    min_similarity=0.80,
                    service_id=task.service_id,
                    validate_quality=True,
                )

                if not matches:
                    # Fallback to unrestricted search across all services
                    matches = await search_hybrid_solutions(
                        session=session,
                        query_text=query_text,
                        query_vector=vector,
                        limit=1,
                        min_similarity=0.80,
                        service_id=None,
                        validate_quality=True,
                    )
        except Exception as exc:
            logger.warning("RAG hybrid search failed during precondition check: %s", exc)
            return PreconditionResult(
                is_valid=False,
                missing_facts=["rag_unavailable"],
            )

        if not matches:
            return PreconditionResult(
                is_valid=False,
                missing_facts=["knowledge_base_match"],
            )

        if task.id is not None:
            self._solutions_by_task[task.id] = matches[0]
        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Post verified historical solution from knowledge base."""
        solution_dto = self._solutions_by_task.pop(task.id, None) if task.id is not None else None
        if solution_dto is None:
            precond = await self.validate_preconditions(task)
            solution_dto = self._solutions_by_task.pop(task.id, None) if task.id is not None else None
            if not precond.is_valid or solution_dto is None:
                return ScenarioExecutionResult(
                    success=False,
                    action_taken="rag_consultation",
                    resolution_comment="",
                    technical_note="⚠️ [Автопилот: RAG] Высокоточный прецедент в базе знаний не найден.",
                    target_status_id=2,  # In progress
                    error="no_high_confidence_solution",
                )

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
