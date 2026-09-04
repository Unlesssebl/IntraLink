import json
import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import AsyncSessionLocal, TriageTemplate, RuleAuditLog

try:
    from .rules import (
        ROOT_SERVICES,
        SERVICE_ID_TO_ROOT,
        RuleDecision,
        RuleEngine,
        classify_target_service,
        get_root_name,
        get_root_number_for_service_id,
    )
    from .rules.redirect import ServiceRedirectRule
except (ImportError, ValueError):
    from rules import (
        ROOT_SERVICES,
        SERVICE_ID_TO_ROOT,
        RuleDecision,
        RuleEngine,
        classify_target_service,
        get_root_name,
        get_root_number_for_service_id,
    )
    from rules.redirect import ServiceRedirectRule

logger = logging.getLogger("core_api.template_engine")

TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "rules", "templates.json")

# L1 In-Memory кэш шаблонов
_L1_TEMPLATES_CACHE: dict[str, dict[str, Any]] = {}

# Глобальный инстанс RuleEngine
_default_engine = RuleEngine()
_redirect_rule = ServiceRedirectRule()


def invalidate_templates_cache() -> None:
    """Инвалидирует L1 in-memory кэш шаблонов."""
    global _L1_TEMPLATES_CACHE
    _L1_TEMPLATES_CACHE.clear()
    logger.info("L1 кэш шаблонов триажа сброшен.")


async def start_rules_invalidation_listener(redis_url: str) -> None:
    """
    Фоновый слушатель Redis Pub/Sub для кросс-воркерной инвалидации L1 кэша.
    """
    import asyncio
    import redis.asyncio as aioredis

    while True:
        try:
            r = aioredis.from_url(redis_url, decode_responses=True)
            async with r.pubsub() as pubsub:
                await pubsub.subscribe("channel:rules_invalidated")
                logger.info("Подписка на channel:rules_invalidated активна.")
                async for msg in pubsub.listen():
                    if msg and msg.get("type") == "message":
                        invalidate_templates_cache()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Ошибка слушателя channel:rules_invalidated: %s", e)
            await asyncio.sleep(5.0)


async def seed_templates_if_empty(session: AsyncSession) -> None:
    """
    Выполняет Database Seeding начальных шаблонов из JSON строго при пустой таблице в БД.
    """
    stmt = select(TriageTemplate).limit(1)
    res = await session.execute(stmt)
    if res.scalar_one_or_none() is not None:
        return  # БД уже содержит шаблоны, seed не требуется

    if not os.path.exists(TEMPLATES_FILE):
        return

    try:
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
            seed_data = json.load(f)

        for key, item in seed_data.items():
            tmpl = TriageTemplate(
                key=key,
                name=item.get("name") or key,
                category=item.get("category", "in_work"),
                status_id=item.get("status_id", 27),
                status_name=item.get("status_name", "В работе"),
                expenses=item.get("expenses", 10),
                template_text=item.get("template", ""),
                is_active=True,
            )
            session.add(tmpl)

        await session.commit()
        logger.info("Успешно выполнен Database Seeding шаблонов в PostgreSQL: %s записей", len(seed_data))
    except Exception as e:
        await session.rollback()
        logger.error("Ошибка выполнения Database Seeding шаблонов: %s", e)


async def get_templates_from_db(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """
    Загружает активные шаблоны из PostgreSQL и обновляет L1 кэш.
    """
    global _L1_TEMPLATES_CACHE
    try:
        stmt = select(TriageTemplate).where(TriageTemplate.is_active == True)  # noqa: E712
        res = await session.execute(stmt)
        templates = res.scalars().all()

        cache_dict = {}
        for t in templates:
            cache_dict[t.key] = {
                "id": t.id,
                "key": t.key,
                "name": t.name,
                "category": t.category,
                "status_id": t.status_id,
                "status_name": t.status_name,
                "expenses": t.expenses,
                "template": t.template_text,
                "is_active": t.is_active,
            }

        _L1_TEMPLATES_CACHE = cache_dict
        return cache_dict
    except Exception as e:
        logger.error("Ошибка загрузки шаблонов из PostgreSQL: %s", e)
        return _L1_TEMPLATES_CACHE


def load_templates() -> dict[str, dict[str, Any]]:
    """
    Синхронно возвращает шаблоны из L1 in-memory кэша (или fallback на чтение JSON при холодном старте до БД).
    """
    global _L1_TEMPLATES_CACHE
    if _L1_TEMPLATES_CACHE:
        return _L1_TEMPLATES_CACHE

    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
                _L1_TEMPLATES_CACHE = json.load(f)
                return _L1_TEMPLATES_CACHE
        except Exception as e:
            logger.error("Ошибка загрузки seed templates.json: %s", e)

    return {}


def render_template(template_key: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Подставляет переменные контекста в указанный шаблон.
    """
    templates = load_templates()
    tmpl = templates.get(template_key) or templates.get("in_work_standard", {
        "name": "Стандартное принятие в работу",
        "status_id": 27,
        "status_name": "В работе",
        "expenses": 10,
        "template": "Добрый день! Ваша заявка принята в работу. По вопросам звоните на номер 49-87.",
    })

    raw_text = tmpl.get("template", "")
    pc_name = context.get("pc_name") or "ПК"
    room = context.get("room") or "кабинет"
    phone = context.get("phone") or "49-87"
    target_service = context.get("target_service") or "соответствующем разделе каталога"
    occupied_user = context.get("occupied_user") or "другой сотрудник"
    details = context.get("details") or "удобное время"
    master_task_id = str(context.get("master_task_id") or "")

    rendered_text = raw_text.replace("{pc_name}", pc_name)
    rendered_text = rendered_text.replace("{room}", room)
    rendered_text = rendered_text.replace("{phone}", phone)
    rendered_text = rendered_text.replace("{target_service}", target_service)
    rendered_text = rendered_text.replace("{occupied_user}", occupied_user)
    rendered_text = rendered_text.replace("{details}", details)
    rendered_text = rendered_text.replace("{master_task_id}", master_task_id)

    return {
        "template_key": template_key,
        "name": tmpl.get("name"),
        "status_id": tmpl.get("status_id", 27),
        "status_name": tmpl.get("status_name", "В работе"),
        "expenses": tmpl.get("expenses", 10),
        "comment": rendered_text.strip(),
    }


def detect_service_redirect(task: dict[str, Any]) -> dict[str, Any] | None:
    """
    Проверяет, требует ли заявка отмены и редиректа в другой раздел каталога.
    Если обнаружено несоответствие разделов, возвращает dict с подробным описанием редиректа.
    Если заявка подана корректно, возвращает None.
    """
    decision = _redirect_rule.evaluate(task)
    if decision and decision.is_redirect:
        return decision.to_dict()
    return None


def auto_detect_template(
    task: dict[str, Any],
    diag: dict[str, Any] | None = None,
    kb_matches: list[dict[str, Any]] | None = None,
    redirect_mode: bool = False,
    comments_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Интеллектуальный авто-подбор наиболее точного шаблона на основе контекста инцидента.
    Использует модульный Rule Engine.
    """
    meta = task.get("_field_meta") or {}
    context = {
        "pc_name": meta.get("pc_name") or (diag.get("target") if diag else "") or "ПК",
        "room": meta.get("room") or "",
        "phone": meta.get("phone") or "",
        "target_service": "Общий раздел",
        "comments_history": comments_history or [],
    }

    decision: RuleDecision = _default_engine.evaluate(
        task=task,
        diag=diag,
        kb_matches=kb_matches,
        redirect_mode=redirect_mode,
        context=context,
    )
    return decision.to_dict()
