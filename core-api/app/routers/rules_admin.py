"""
Роутер администрирования шаблонов ответов, правил триажа и аудита изменений (SSOT).
"""
import logging
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import (
    RuleAuditLog,
    TriageRule,
    TriageTemplate,
    get_db,
)
from app.routers.deps import verify_admin_or_api_key
from app.services.template_engine import invalidate_templates_cache
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.routers.rules_admin")

router = APIRouter(
    prefix="/api/v1/rules-admin",
    tags=["Triage Rules & Templates SSOT Admin"],
    dependencies=[Depends(verify_admin_or_api_key)],
)


# ---------------------------------------------------------------------------
# Pydantic Модели
# ---------------------------------------------------------------------------


class TemplateCreateUpdate(BaseModel):
    key: str = Field(..., description="Уникальный строковый идентификатор шаблона")
    name: str = Field(..., description="Название шаблона")
    category: str = Field("in_work", description="Категория шаблона (in_work, redirect, etc)")
    status_id: int = Field(27, description="ID целевого статуса")
    status_name: str = Field("В работе", description="Название целевого статуса")
    expenses: int = Field(10, description="Списание трудозатрат в минутах")
    template_text: str = Field(..., description="Текст шаблона с переменными {pc_name}, {room}...")
    is_active: bool = Field(True, description="Флаг активности")


class TemplateResponse(TemplateCreateUpdate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class RuleCreateUpdate(BaseModel):
    name: str = Field(..., description="Название правила")
    priority: int = Field(100, description="Приоритет вычисления (чем выше, тем раньше)")
    conditions_json: dict[str, Any] = Field(default_factory=dict, description="Предикаты сопоставления")
    target_template_key: str = Field(..., description="Ключ целевого шаблона")
    actions_override_json: dict[str, Any] = Field(default_factory=dict, description="Переопределение действий")
    is_active: bool = Field(True, description="Флаг активности")


class RuleResponse(RuleCreateUpdate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class AuditLogResponse(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    changed_by: str
    change_type: str
    diff_json: dict[str, Any]
    created_at: str
    model_config = ConfigDict(from_attributes=True)


async def _notify_cache_invalidation():
    """Сбрасывает локальный кэш и шлет сигнал в Redis Pub/Sub."""
    invalidate_templates_cache()
    try:
        r = get_redis_client()
        await r.publish("channel:rules_invalidated", "reload")
    except Exception as e:
        logger.debug("Не удалось опубликовать сигнал инвалидации в Redis: %s", e)


# ---------------------------------------------------------------------------
# Эндпоинты шаблонов (Templates)
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=List[TemplateResponse])
async def list_templates(
    category: Optional[str] = Query(None, description="Фильтр по категории"),
    only_active: bool = Query(True, description="Только активные шаблоны"),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает список шаблонов ответов из PostgreSQL."""
    query = select(TriageTemplate)
    if only_active:
        query = query.where(TriageTemplate.is_active == True)  # noqa: E712
    if category:
        query = query.where(TriageTemplate.category == category)
    query = query.order_by(TriageTemplate.id.asc())

    res = await db.execute(query)
    return res.scalars().all()


@router.post("/templates", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateCreateUpdate,
    operator: str = Depends(verify_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Создает новый шаблон ответов и регистрирует аудит-лог."""
    # Проверка уникальности ключа
    existing = await db.execute(select(TriageTemplate).where(TriageTemplate.key == payload.key))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Шаблон с ключом '{payload.key}' уже существует",
        )

    tmpl = TriageTemplate(**payload.model_dump())
    db.add(tmpl)
    await db.flush()

    audit = RuleAuditLog(
        entity_type="template",
        entity_id=tmpl.key,
        changed_by=operator,
        change_type="create",
        diff_json=payload.model_dump(),
    )
    db.add(audit)
    await db.commit()
    await db.refresh(tmpl)

    await _notify_cache_invalidation()
    return tmpl


@router.put("/templates/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: int,
    payload: TemplateCreateUpdate,
    operator: str = Depends(verify_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Обновляет существующий шаблон ответов."""
    tmpl = await db.get(TriageTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    old_data = {
        "key": tmpl.key,
        "name": tmpl.name,
        "category": tmpl.category,
        "status_id": tmpl.status_id,
        "status_name": tmpl.status_name,
        "expenses": tmpl.expenses,
        "template_text": tmpl.template_text,
        "is_active": tmpl.is_active,
    }

    for k, v in payload.model_dump().items():
        setattr(tmpl, k, v)

    audit = RuleAuditLog(
        entity_type="template",
        entity_id=tmpl.key,
        changed_by=operator,
        change_type="update",
        diff_json={"old": old_data, "new": payload.model_dump()},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(tmpl)

    await _notify_cache_invalidation()
    return tmpl


@router.delete("/templates/{template_id}", status_code=status.HTTP_200_OK)
async def delete_template(
    template_id: int,
    operator: str = Depends(verify_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Деактивирует шаблон (мягкое удаление)."""
    tmpl = await db.get(TriageTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    tmpl.is_active = False
    audit = RuleAuditLog(
        entity_type="template",
        entity_id=tmpl.key,
        changed_by=operator,
        change_type="delete",
        diff_json={"is_active": False},
    )
    db.add(audit)
    await db.commit()

    await _notify_cache_invalidation()
    return {"status": "success", "message": f"Шаблон {tmpl.key} деактивирован"}


# ---------------------------------------------------------------------------
# Эндпоинты правил триажа (Rules)
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=List[RuleResponse])
async def list_rules(
    only_active: bool = Query(True, description="Только активные правила"),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает список правил сопоставления триажа."""
    query = select(TriageRule)
    if only_active:
        query = query.where(TriageRule.is_active == True)  # noqa: E712
    query = query.order_by(desc(TriageRule.priority), TriageRule.id.asc())

    res = await db.execute(query)
    return res.scalars().all()


@router.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RuleCreateUpdate,
    operator: str = Depends(verify_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Создает новое правило триажа."""
    rule = TriageRule(**payload.model_dump())
    db.add(rule)
    await db.flush()

    audit = RuleAuditLog(
        entity_type="rule",
        entity_id=str(rule.id),
        changed_by=operator,
        change_type="create",
        diff_json=payload.model_dump(),
    )
    db.add(audit)
    await db.commit()
    await db.refresh(rule)

    await _notify_cache_invalidation()
    return rule


@router.get("/audit-log", status_code=status.HTTP_200_OK)
async def get_audit_log(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает историю изменений правил и шаблонов."""
    stmt = select(RuleAuditLog).order_by(desc(RuleAuditLog.created_at)).limit(limit)
    res = await db.execute(stmt)
    logs = res.scalars().all()
    return [
        {
            "id": str(log.id),
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "changed_by": log.changed_by,
            "change_type": log.change_type,
            "diff_json": log.diff_json,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
