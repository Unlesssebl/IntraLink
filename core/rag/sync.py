"""Incremental Knowledge Base synchronization service with IntraService API."""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import TaskKnowledgeBase
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO
from core.rag.embedder import get_embedding_vector
from core.rag.sanitizer import sanitize_text
from core.rag.search import check_semantic_duplicate

logger = logging.getLogger("core.rag.sync")

# L2 LLM-as-a-Judge prompt for technical value evaluation
LLM_JUDGE_SYSTEM_PROMPT = """Ты строгий технический аудитор базы знаний IT-поддержки (Helpdesk).
Оцени, содержит ли решение конкретные полезные технические действия, команды, инструкции или регламентные шаги для указанной проблемы.

Критерии:
1 (Полезно): Решение содержит воспроизводимые технические действия (команды, перезапуск сервисов, правка реестра, замена комплектующих, настройка прав, чистка кэша, регламентный путь и т.д.).
0 (Отписка): Текст является отпиской без технических действий (закрыто без действий, "не дозвонился", "прикрепил акт", "дубликат", просьба перезвонить, пустая отписка).

Отвечай строго одной цифрой: 1 или 0."""

# Terminal status IDs for resolved/closed tickets in IntraService
# 3: Выполнена, 4: Закрыта, 30: Отменена
TERMINAL_STATUS_IDS: List[int] = [3, 4, 30]

# Non-informative single-word or short boilerplate phrases
_NON_INFORMATIVE_SOLUTIONS: Set[str] = {
    "ок",
    "готово",
    "сделано",
    "выполнено",
    "выполнена",
    "закрыто",
    "закрыта",
    "решено",
    "устранено",
    "проверено",
    "все работает",
    "всё работает",
    "спасибо",
    "принято в работу",
    "заявка закрыта",
    "согласовано",
    "согласована",
    "попросили перекинуть",
    "отмена",
    "отменено",
    "выполнено в штатном режиме",
    "заявка выполнена в штатном режиме",
}

_NON_INFORMATIVE_WORDS: Set[str] = {
    "все",
    "всё",
    "работает",
    "спасибо",
    "ок",
    "хорошо",
    "готово",
    "сделано",
    "решено",
    "закрыто",
    "закрыта",
    "проверено",
    "принято",
    "в",
    "работу",
    "выполнено",
    "выполнена",
    "устранено",
    "штатном",
    "режиме",
    "заявка",
    "нормально",
}

# Stop-patterns indicating administrative closures without actionable technical content
_UNINFORMATIVE_PATTERNS = [
    re.compile(r"(?i)\b(?:не\s+дозвонил\w*|не\s+смог\w*\s+дозвониться|перезвоните|не\s+отвечает|не\s+берет\s+трубку|нет\s+связи\s+с\s+заявител\w*)"),
    re.compile(r"(?i)\b(?:акт\s+прикрепил\w*|прикрепил\w*\s+акт|скан\s+прикрепил\w*|акт\s+во\s+вложении|акт\s+к\s+заявк\w*)"),
    re.compile(r"(?i)\b(?:дубликат\w*|повторн\w*\s+(?:заявк\w*|инцидент\w*)|работы\s+ведутся\s+в\s+основн\w*|заявк\w*\s+продублирован\w*)"),
    re.compile(r"(?i)\b(?:заявк\w*\s+не\s*актуальн\w*|не\s*актуальн\w*|потерял\w*\s+актуальность)"),
    re.compile(r"(?i)\b(?:неправильн\w*\s+раздел|оставьте\s+заявк\w*\s+в\s+раздел\w*|ошибочно\s+создан\w*)"),
]

# System logs / notifications regex
_SYSTEM_LOG_PATTERNS = [
    re.compile(r"(?i)автоматически переведена в статус"),
    re.compile(r"(?i)автоматически закрыта"),
    re.compile(r"(?i)по истечении \d+ часов"),
    re.compile(r"(?i)статус изменен"),
    re.compile(r"(?i)назначен исполнитель"),
    re.compile(r"(?i)исполнитель изменен"),
    re.compile(r"(?i)оцените качество"),
    re.compile(r"(?i)оповещение отправлено"),
    re.compile(r"(?i)уведомление отправлено"),
    re.compile(r"(?i)отменил[ао]? делегирование"),
    re.compile(r"(?i)делегировал[ао]? заявку"),
    re.compile(r"(?i)служебная записка №?\d* была согласована"),
]

_SYSTEM_EDITORS: Set[str] = {
    "intraservice",
    "system",
    "система",
    "администратор",
    "administrator",
    "служба рассылки",
    "робот",
}


def is_system_or_noise_comment(text: str, editor_name: str = "") -> bool:
    """Check if comment is an automated system event or administrative noise."""
    if not text:
        return True
    if editor_name and editor_name.strip().lower() in _SYSTEM_EDITORS:
        return True
    clean = text.strip()
    for pat in _SYSTEM_LOG_PATTERNS:
        if pat.search(clean):
            return True
    return False


def evaluate_solution_quality(solution: str, status_id: Optional[int] = None) -> Tuple[bool, str]:
    """Validate solution quality and filter uninformative replies."""
    if not solution or not isinstance(solution, str):
        return False, "пустой текст решения"

    s_clean = solution.strip()
    if not s_clean:
        return False, "пустой текст решения"

    if is_system_or_noise_comment(s_clean):
        return False, "системное уведомление или служебный лог"

    s_lower = s_clean.lower()
    if s_lower in _NON_INFORMATIVE_SOLUTIONS:
        return False, f"односложная отписка: '{s_clean}'"

    s_alpha = re.sub(r"[^\w\s]", "", s_lower).strip()
    if s_alpha in _NON_INFORMATIVE_SOLUTIONS:
        return False, f"односложная отписка: '{s_clean}'"

    words = {w for w in re.split(r"\W+", s_lower) if w}
    if words and words.issubset(_NON_INFORMATIVE_WORDS):
        return False, "отписка без технических деталей"

    # Stop patterns for administrative closures
    for pat in _UNINFORMATIVE_PATTERNS:
        m = pat.search(s_clean)
        if m:
            return False, f"отписка инженера: '{m.group(0).strip()}'"

    # Minimal length gate
    if status_id == 30:  # Отменена
        if len(s_clean) < 60:
            return False, "слишком короткое решение для статуса Отменена (<60 символов)"
    elif len(s_clean) < 20:
        return False, "слишком короткое решение (<20 символов)"

    return True, "ok"


class KBSyncStatsDTO(BaseModel):
    status: str = "succeeded"
    processed: int = 0
    indexed: int = 0
    skipped_existing: int = 0
    skipped_low_quality: int = 0
    skipped_duplicates: int = 0
    skipped_quota: int = 0
    errors: int = 0
    details: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseSyncService:
    """Service synchronizing closed tickets from IntraService to pgvector Knowledge Base."""

    def __init__(
        self,
        intraservice_client: Optional[IntraServiceClient] = None,
        ai_client: Optional[AsyncOpenAI] = None,
        max_concurrent_requests: int = 3,
        model_fast: str = "fast",
    ) -> None:
        intraservice_url = os.getenv("INTRASERVICE_URL", "https://servicedesk-pub.corporate.loc/api")
        ssl_verify = os.getenv("SSL_VERIFY", "false").lower() in ("true", "1")
        self.intraservice = intraservice_client or IntraServiceClient(
            base_url=intraservice_url,
            verify_ssl=ssl_verify,
        )
        self.ai_client = ai_client or AsyncOpenAI(
            base_url=os.getenv("LITELLM_BASE_URL", "http://localhost:4000"),
            api_key=os.getenv("LITELLM_API_KEY", "sk-mock-key"),
        )
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.model_fast = os.getenv("LITELLM_MODEL_FAST", model_fast)

    def extract_solution_from_lifetime(
        self,
        task: TaskDTO,
        lifetime: List[TaskLifetimeEventDTO],
    ) -> Optional[str]:
        """Extract authoritative resolution from ticket history, filtering out creator & bots."""
        creator_id = str(task.creator_id or "") if task.creator_id is not None else ""
        executor_ids = {
            str(x).strip()
            for x in str(task.executor_ids or "").split(",")
            if str(x).strip()
        }

        # Sort lifetime events descending by date/id
        sorted_events = sorted(
            lifetime,
            key=lambda e: str(e.created or ""),
            reverse=True,
        )

        candidates_closing: List[str] = []
        candidates_executor: List[str] = []
        candidates_staff: List[str] = []

        for ev in sorted_events:
            comment = ev.comment or ""
            if not comment or len(comment.strip()) < 5:
                continue

            editor_id = str(ev.editor_id or "") if ev.editor_id is not None else ""
            editor_name = ev.editor or ev.user_name or ""

            # Exclude creator comments (creator cannot provide solution)
            if creator_id and editor_id == creator_id:
                continue

            # Exclude system bots & noise
            if is_system_or_noise_comment(comment, editor_name):
                continue

            ev_status_id = ev.status_id
            if ev_status_id in (3, 4, 30, 28, 29):
                candidates_closing.append(comment)
            elif editor_id and editor_id in executor_ids:
                candidates_executor.append(comment)
            else:
                candidates_staff.append(comment)

        # Select most authoritative candidate
        if candidates_closing:
            return candidates_closing[0]
        if candidates_executor:
            return candidates_executor[0]
        if candidates_staff:
            return candidates_staff[0]

        # Fallback to ticket custom attributes if present
        for field_name in ("Solution", "Resolution", "CloseReason"):
            if field_name in task.custom_fields:
                val = str(task.custom_fields[field_name] or "").strip()
                if len(val) >= 10 and not is_system_or_noise_comment(val):
                    return val

        return None

    async def get_service_quota_count(
        self,
        session: AsyncSession,
        service_id: int,
    ) -> int:
        """Count existing active solutions in KB for a specific service."""
        stmt = (
            select(func.count(TaskKnowledgeBase.task_id))
            .where(TaskKnowledgeBase.service_id == service_id)
            .where(TaskKnowledgeBase.is_blacklisted.is_(False))
        )
        return (await session.execute(stmt)).scalar() or 0

    async def evaluate_solution_quality_llm(
        self,
        problem: str,
        solution: str,
        service_name: Optional[str] = None,
        task_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """L2: LLM-as-a-Judge evaluating actionable technical value with fast fallback."""
        ctx = (
            f"Сервис: {service_name or 'Общий'}\n"
            f"Тема: {task_name or ''}\n"
            f"Проблема: {problem[:300]}\n"
            f"Решение: {solution[:500]}"
        )
        prompt = f"Контекст тикета:\n{ctx}\n\nОцени качество решения (1 - полезно, 0 - отписка):"

        try:
            res = await asyncio.wait_for(
                self.ai_client.chat.completions.create(
                    model=self.model_fast,
                    messages=[
                        {"role": "system", "content": LLM_JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=4,
                ),
                timeout=5.0,
            )
            out = (res.choices[0].message.content or "").strip().lower()
            if "0" in out or "нет" in out or "false" in out:
                return False, "отклонено LLM Judge (отписка без технической ценности)"
            return True, "одобрено LLM Judge"
        except Exception as exc:
            logger.debug("LLM Judge timeout or unavailable, falling back to L1: %s", exc)
            return True, "fallback to L1"

    async def sync_incremental(
        self,
        session: AsyncSession,
        hours: int = 48,
        status_ids: Optional[List[int]] = None,
        quota_per_service: int = 30,
        auth_b64: Optional[str] = None,
        throttle_delay_sec: float = 0.05,
        ai_eval: bool = True,
    ) -> KBSyncStatsDTO:
        """Perform incremental synchronization of closed tickets into Knowledge Base."""
        target_statuses = status_ids or TERMINAL_STATUS_IDS
        status_ids_str = ",".join(str(s) for s in target_statuses)

        cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
        changed_more_than = cutoff_dt.strftime("%Y-%m-%d %H:%M")

        stats = KBSyncStatsDTO()
        service_counts_cache: Dict[int, int] = {}

        logger.info(
            "Starting KB incremental sync: hours=%d, cutoff='%s', statuses=[%s], quota_per_service=%d",
            hours,
            changed_more_than,
            status_ids_str,
            quota_per_service,
        )

        page = 1
        page_size = 100

        while True:
            # 1. Fetch closed tickets page with concurrency throttling
            try:
                async with self.semaphore:
                    tasks = await self.intraservice.get_tasks(
                        filters={
                            "StatusIds": status_ids_str,
                            "ChangedMoreThan": changed_more_than,
                        },
                        page=page,
                        page_size=page_size,
                        auth_b64=auth_b64,
                    )
            except Exception as exc:
                logger.error("Failed to fetch tickets from IntraService on page %d: %s", page, exc)
                stats.errors += 1
                break

            if not tasks:
                break

            for task in tasks:
                stats.processed += 1
                task_id = task.id
                service_id = task.service_id or 0

                # Check if ticket already exists in KB
                check_stmt = select(TaskKnowledgeBase.task_id).where(TaskKnowledgeBase.task_id == task_id)
                exists = (await session.execute(check_stmt)).scalar_one_or_none()
                if exists:
                    stats.skipped_existing += 1
                    continue

                # Quota check per leaf service
                if service_id not in service_counts_cache:
                    service_counts_cache[service_id] = await self.get_service_quota_count(session, service_id)

                if service_counts_cache[service_id] >= quota_per_service:
                    stats.skipped_quota += 1
                    continue

                # 2. Fetch ticket lifetime with throttling
                try:
                    async with self.semaphore:
                        if throttle_delay_sec > 0:
                            await asyncio.sleep(throttle_delay_sec)
                        lifetime = await self.intraservice.get_task_lifetime(task_id, auth_b64=auth_b64)
                except Exception as lte:
                    logger.warning("Failed to fetch lifetime for ticket #%d: %s", task_id, lte)
                    lifetime = []

                # Extract solution text
                raw_solution = self.extract_solution_from_lifetime(task, lifetime)
                if not raw_solution:
                    stats.skipped_low_quality += 1
                    continue

                # Clean & PII-sanitize solution
                clean_solution = sanitize_text(raw_solution)

                # Quality filter: L1 fast heuristic & boilerplate gate
                is_quality, quality_reason = evaluate_solution_quality(clean_solution, status_id=task.status_id)
                if not is_quality:
                    logger.debug("Ticket #%d skipped by L1 quality gate: %s", task_id, quality_reason)
                    stats.skipped_low_quality += 1
                    continue

                # Problem text
                raw_problem = f"{task.name or ''}\n{task.description or ''}"
                clean_problem = sanitize_text(raw_problem)

                # Quality filter: L2 LLM-as-a-Judge gate
                if ai_eval:
                    is_llm_quality, llm_reason = await self.evaluate_solution_quality_llm(
                        problem=clean_problem,
                        solution=clean_solution,
                        service_name=task.service_name,
                        task_name=task.name,
                    )
                    if not is_llm_quality:
                        logger.debug("Ticket #%d skipped by L2 LLM Judge: %s", task_id, llm_reason)
                        stats.skipped_low_quality += 1
                        continue

                # 3. Vectorization (BGE-M3 1024-dim)
                embed_text = (
                    f"Сервис: {task.service_name or ''}\n"
                    f"Тема: {task.name or ''}\n"
                    f"Проблема: {clean_problem}\n"
                    f"Решение: {clean_solution}"
                )
                try:
                    vec = await get_embedding_vector(embed_text, self.ai_client)
                except Exception as emb_exc:
                    logger.warning("Embedding generation failed for ticket #%d: %s", task_id, emb_exc)
                    vec = None

                if not vec:
                    stats.errors += 1
                    continue

                # 4. Cosine Gate > 0.90 semantic duplicate check
                is_duplicate = await check_semantic_duplicate(
                    session=session,
                    query_vector=vec,
                    service_id=service_id,
                    threshold=0.90,
                )
                if is_duplicate:
                    logger.debug("Ticket #%d skipped by Cosine Gate (> 0.90 duplicate)", task_id)
                    stats.skipped_duplicates += 1
                    continue

                # 5. Insert into task_knowledge_base
                kb_item = TaskKnowledgeBase(
                    task_id=task_id,
                    original_name=task.name or f"Заявка #{task_id}",
                    problem=clean_problem,
                    solution=clean_solution,
                    service_id=service_id,
                    service_name=task.service_name or "Общий сервис",
                    service_path=task.service_name,
                    status_name=task.status_name or "Закрыта",
                    classification_data={
                        "synced_at": datetime.now(timezone.utc).isoformat(),
                        "status_id": task.status_id,
                        "source": "sync_kb_task",
                    },
                    embedding=vec,
                    is_blacklisted=False,
                    quality_score=1.0,
                )
                session.add(kb_item)
                await session.commit()

                service_counts_cache[service_id] += 1
                stats.indexed += 1
                logger.info(
                    "Indexed ticket #%d into Knowledge Base (service %d: %d/%d)",
                    task_id,
                    service_id,
                    service_counts_cache[service_id],
                    quota_per_service,
                )

            if len(tasks) < page_size:
                break
            page += 1

        stats.details = {
            "hours": hours,
            "quota_per_service": quota_per_service,
            "services_tracked": len(service_counts_cache),
        }
        logger.info("KB sync finished: %s", stats.model_dump())
        return stats
