"""Triage service orchestrating queue analysis and decision audits."""

import logging
import uuid
from typing import List, Optional

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.ai import MODEL_FAST, get_ai_client
from api.src.core.config import settings
from core.database.models import TriageAudit
from core.intraservice import IntraServiceClient, TaskDTO

from .pipeline import TriagePipeline
from .schemas import (
    ApplyDecisionRequest,
    BatchTriageResponse,
    TriageAnalysisResponse,
)

logger = logging.getLogger("api.features.triage")


class TriageService:
    """Service managing Helpdesk queue triage, duplicate filtering, and decision execution."""

    def __init__(
        self,
        ai_client: Optional[AsyncOpenAI] = None,
        intraservice_client: Optional[IntraServiceClient] = None,
    ) -> None:
        self.ai_client = ai_client or get_ai_client()
        self.client = intraservice_client or IntraServiceClient(
            base_url=settings.INTRASERVICE_URL,
            verify_ssl=settings.SSL_VERIFY,
        )
        self.pipeline = TriagePipeline(self.ai_client)

    async def analyze_ticket(
        self,
        ticket_id: int,
        session: AsyncSession,
        recent_tasks: Optional[List[TaskDTO]] = None,
        auth_b64: Optional[str] = None,
    ) -> TriageAnalysisResponse:
        task = await self.client.get_task(task_id=ticket_id, auth_b64=auth_b64)

        # 1. Check deterministic business rules
        rule_res = self.pipeline.run_deterministic_rules(task)
        if rule_res:
            decision, rule_name = rule_res
        else:
            # 2. Check duplicates against recent tickets
            dup_res = self.pipeline.detect_duplicate(task, recent_tasks or []) if recent_tasks else None
            if dup_res:
                decision, rule_name = dup_res
            else:
                # 3. LLM classification
                decision, rule_name = await self.pipeline.run_llm_classification(task)

        # 4. Record decision in triage_audit
        audit = TriageAudit(
            id=uuid.uuid4(),
            task_id=task.id,
            action=decision.action,
            model_used=MODEL_FAST if "LLM" in rule_name else "deterministic_rules",
            confidence=decision.confidence,
            prompt_tokens=0,
            completion_tokens=0,
            context_snapshot={"title": task.name, "service_id": task.service_id},
            decision_json=decision.model_dump(),
            applied=False,
            applied_by="system",
        )
        session.add(audit)
        await session.commit()
        await session.refresh(audit)

        return TriageAnalysisResponse(
            audit_id=audit.id,
            ticket_id=task.id,
            decision=decision,
            model_used=audit.model_used or "rules",
            rule_matched=rule_name,
        )

    async def batch_analyze(
        self,
        filter_id: int,
        limit: int,
        session: AsyncSession,
        auth_b64: Optional[str] = None,
    ) -> BatchTriageResponse:
        tasks = await self.client.get_tasks_by_filter(filter_id=filter_id, page_size=limit, auth_b64=auth_b64)
        decisions: List[TriageAnalysisResponse] = []

        for task in tasks:
            analysis = await self.analyze_ticket(
                ticket_id=task.id,
                session=session,
                recent_tasks=tasks,
                auth_b64=auth_b64,
            )
            decisions.append(analysis)

        return BatchTriageResponse(total_analyzed=len(decisions), decisions=decisions)

    async def apply_decision(
        self,
        audit_id: uuid.UUID,
        req: ApplyDecisionRequest,
        session: AsyncSession,
        auth_b64: Optional[str] = None,
    ) -> dict:
        stmt = select(TriageAudit).where(TriageAudit.id == audit_id)
        audit = (await session.execute(stmt)).scalar_one_or_none()
        if not audit:
            raise ValueError(f"Triage audit record #{audit_id} not found")

        decision = audit.decision_json
        status_id = req.override_status_id or decision.get("suggested_status_id")
        comment = req.override_comment or decision.get("suggested_comment")

        success = await self.client.update_task(
            task_id=audit.task_id,
            status_id=status_id,
            comment=comment,
            auth_b64=auth_b64,
        )

        if success:
            audit.applied = True
            await session.commit()

        return {"audit_id": audit_id, "ticket_id": audit.task_id, "applied": success}
