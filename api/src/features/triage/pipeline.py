"""Linear, transparent triage analysis pipeline (replaces v1 decision engine sprawl)."""

import json
import logging
from typing import List, Optional, Tuple

from openai import AsyncOpenAI

from api.src.core.ai import MODEL_FAST
from core.intraservice import TaskDTO
from core.intraservice.catalog import (
    SERVICE_DIRECTUM_ACCESS,
    SERVICE_DIRECTUM_INSTALL,
)

from .prompts import TRIAGE_SYSTEM_PROMPT
from .schemas import TriageDecisionDTO

logger = logging.getLogger("api.features.triage.pipeline")


class TriagePipeline:
    """Linear pipeline executing deterministic rules, duplicate checks, and LLM analysis."""

    def __init__(self, ai_client: AsyncOpenAI) -> None:
        self.ai_client = ai_client

    def run_deterministic_rules(self, task: TaskDTO) -> Optional[Tuple[TriageDecisionDTO, str]]:
        """Fast-path heuristics based on strict corporate regulations (GEMINI.md)."""
        text = f"{task.name} {task.description}".lower()

        # Rule 1: Личные ЭЦП на физлиц (запрещены регламентом)
        if any(w in text for w in ["личная эцп", "эцп на физлицо", "эцп физлица", "личную подпись"]):
            return (
                TriageDecisionDTO(
                    action="redirect_service",
                    target_service_id=None,
                    target_service_name=None,
                    confidence=0.99,
                    reason="Регламентное ограничение: ИТ-отдел холдинга не выпускает личные ЭЦП на физлиц.",
                    suggested_comment=(
                        "Добрый день! Согласно регламенту холдинга ТЭМПО, ИТ-служба не производит выпуск "
                        "личных электронных подписей на физических лиц. Рекомендуем обратиться в аккредитованный УЦ."
                    ),
                    suggested_status_id=30,  # Отменена
                ),
                "RULE_PERSONAL_EDS_REJECT",
            )

        # Rule 2: Вопросы по 1С, поданные не в тот раздел
        if (
            "1с" in text
            and task.service_id != 6
            and any(k in text for k in ["бухгалтерия", "зарплата", "зуп", "проводк", "утп"])
        ):
            return (
                TriageDecisionDTO(
                    action="redirect_service",
                    target_service_id=6,
                    target_service_name="06. Вопросы по 1С",
                    confidence=0.95,
                    reason="Вопрос по функционалу 1С перенаправлен в профильный отдел 1С (Раздел 06).",
                    suggested_comment="Заявка перенаправлена профильным специалистам отдела 1С.",
                    suggested_status_id=None,
                ),
                "RULE_REDIRECT_1C",
            )

        # Rule 3: Установка Directum
        if "directum" in text and any(k in text for k in ["установить", "инсталляция", "поставить директум"]):
            return (
                TriageDecisionDTO(
                    action="auto_classify",
                    target_service_id=SERVICE_DIRECTUM_INSTALL,
                    target_service_name="05. DIRECTUM / Установка Directum",
                    confidence=0.95,
                    reason="Запрос на установку толстого клиента Directum.",
                    suggested_comment="Заявка назначена на инженеров поддержки рабочих мест.",
                ),
                "RULE_DIRECTUM_INSTALL",
            )

        # Rule 4: Пароли / доступ Directum
        if "directum" in text and any(
            k in text for k in ["сброс пароля", "сбросить пароль", "разблокировать", "выдать доступ"]
        ):
            return (
                TriageDecisionDTO(
                    action="auto_classify",
                    target_service_id=SERVICE_DIRECTUM_ACCESS,
                    target_service_name="05. DIRECTUM / Выдача доступа, сброс паролей",
                    confidence=0.95,
                    reason="Запрос на сброс пароля или выдачу прав Directum.",
                ),
                "RULE_DIRECTUM_ACCESS",
            )

        return None

    def detect_duplicate(
        self,
        current_task: TaskDTO,
        recent_tasks: List[TaskDTO],
    ) -> Optional[Tuple[TriageDecisionDTO, str]]:
        """Identify potential duplicate tickets from the same applicant."""
        if not current_task.applicant_id:
            return None

        clean_curr_name = current_task.name.strip().lower()

        for t in recent_tasks:
            if t.id == current_task.id:
                continue
            if t.applicant_id == current_task.applicant_id:
                clean_prev_name = t.name.strip().lower()
                if clean_curr_name == clean_prev_name or (
                    len(clean_curr_name) > 10 and clean_curr_name in clean_prev_name
                ):
                    return (
                        TriageDecisionDTO(
                            action="cancel_duplicate",
                            confidence=0.98,
                            reason=f"Обнаружен дубликат заявки #{t.id} от того же заявителя.",
                            suggested_comment=(
                                f"Добрый день! Данная заявка дублирует обращение #{t.id}. "
                                f"Работы ведутся в рамках основной заявки."
                            ),
                            suggested_status_id=30,  # Отменена
                            is_duplicate=True,
                            master_ticket_id=t.id,
                        ),
                        f"DUPLICATE_OF_{t.id}",
                    )
        return None

    async def run_llm_classification(self, task: TaskDTO) -> Tuple[TriageDecisionDTO, str]:
        """Perform semantic LLM classification using LiteLLM Fast model."""
        prompt_user = (
            f"Заявка #{task.id}\n"
            f"Тема: {task.name}\n"
            f"Описание: {task.description}\n"
            f"Текущий сервис: {task.service_name} (ID: {task.service_id})\n"
            f"Заявитель: {task.applicant_name}\n"
            f"ПК: {task.entities.pc_name if task.entities else ''}"
        )

        try:
            response = await self.ai_client.chat.completions.create(
                model=MODEL_FAST,
                messages=[
                    {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_user},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=512,
            )
            raw_content = response.choices[0].message.content or "{}"
            parsed = json.loads(raw_content)
            decision = TriageDecisionDTO.model_validate(parsed)
            return decision, "LLM_FAST"
        except Exception as exc:
            logger.warning(f"LiteLLM triage classification failed: {exc}")
            return (
                TriageDecisionDTO(
                    action="manual_resolve",
                    confidence=0.0,
                    reason=f"Ошибка автоматической классификации: {exc}",
                ),
                "LLM_ERROR_FALLBACK",
            )
