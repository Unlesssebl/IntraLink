"""
Роутер администрирования шаблонов ответов, правил триажа и аудита изменений (SSOT).
"""
import logging
from typing import Any, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import (
    RuleAuditLog,
    ResolutionPolicy,
    ResponseTemplate,
    TriageTemplate,
    get_db,
)
from app.routers.deps import principal_subject, require_permission
from app.services.template_engine import invalidate_templates_cache
from app.services.resolution_service import template_variables
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.routers.rules_admin")

router = APIRouter(
    prefix="/api/v1/rules-admin",
    tags=["Triage Rules & Templates SSOT Admin"],
    dependencies=[Depends(require_permission("rules:manage"))],
)

router_v2 = APIRouter(
    prefix="/api/v2/rules-admin",
    tags=["Versioned Decisioning SSOT Admin"],
    dependencies=[Depends(require_permission("rules:manage"))],
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


class ResponseTemplateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    template_text: str = Field(min_length=1, max_length=8_000)


class ResolutionPolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome_key: str = Field(min_length=1, max_length=64)
    outcome_kind: Literal["clarification", "action", "manual_review", "resolution"]
    template_key: str = Field(min_length=1, max_length=64)
    target_status_id: Literal[27, 29, 30, 35, 48] | None = None
    status_name: str | None = Field(default=None, max_length=100)
    expenses: int = Field(default=10, ge=0, le=1440)
    action_id: str | None = Field(default=None, max_length=64)
    risk_level: int = Field(default=0, ge=0, le=3)
    requires_approval: bool = True


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


@router.get("/templates-catalog")
async def get_templates_catalog_endpoint(
    db: AsyncSession = Depends(get_db),
):
    """Возвращает каталог шаблонов в формате словаря для быстрого выбора в UI."""
    query = select(TriageTemplate).where(TriageTemplate.is_active == True).order_by(TriageTemplate.id.asc())  # noqa: E712
    res = await db.execute(query)
    items = res.scalars().all()
    templates = [
        {
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
        for t in items
    ]
    templates_map = {t["key"]: t for t in templates}
    return {
        "total": len(templates),
        "templates": templates,
        "map": templates_map,
    }


@router.post("/templates", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateCreateUpdate,
    operator: str = Depends(principal_subject),
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
    await _publish_compat_ssot(db, payload, operator)

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
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """Обновляет существующий шаблон ответов."""
    tmpl = await db.get(TriageTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    if payload.key != tmpl.key:
        raise HTTPException(status_code=422, detail="template_key_is_immutable")

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
    await _publish_compat_ssot(db, payload, operator)

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
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """Деактивирует шаблон (мягкое удаление)."""
    tmpl = await db.get(TriageTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    tmpl.is_active = False
    active_template = await db.scalar(
        select(ResponseTemplate).where(
            ResponseTemplate.key == tmpl.key,
            ResponseTemplate.is_active.is_(True),
        )
    )
    active_policy = await db.scalar(
        select(ResolutionPolicy).where(
            ResolutionPolicy.outcome_key == tmpl.key,
            ResolutionPolicy.is_active.is_(True),
        )
    )
    if active_template:
        active_template.is_active = False
    if active_policy:
        active_policy.is_active = False
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


async def _next_version(db: AsyncSession, model, key_column, key: str) -> int:
    current = await db.scalar(select(func.max(model.version)).where(key_column == key))
    return int(current or 0) + 1


async def _publish_compat_ssot(
    db: AsyncSession,
    payload: TemplateCreateUpdate,
    operator: str,
) -> None:
    old_template = await db.scalar(
        select(ResponseTemplate).where(
            ResponseTemplate.key == payload.key,
            ResponseTemplate.is_active.is_(True),
        )
    )
    if old_template:
        old_template.is_active = False
    old_policy = await db.scalar(
        select(ResolutionPolicy).where(
            ResolutionPolicy.outcome_key == payload.key,
            ResolutionPolicy.is_active.is_(True),
        )
    )
    if old_policy:
        old_policy.is_active = False
    template = ResponseTemplate(
        key=payload.key,
        version=await _next_version(db, ResponseTemplate, ResponseTemplate.key, payload.key),
        name=payload.name,
        template_text=payload.template_text,
        required_variables=template_variables(payload.template_text),
        is_active=payload.is_active,
        created_by=operator,
    )
    db.add(template)
    await db.flush()
    db.add(
        ResolutionPolicy(
            outcome_key=payload.key,
            version=await _next_version(db, ResolutionPolicy, ResolutionPolicy.outcome_key, payload.key),
            outcome_kind="resolution",
            template_id=template.id,
            target_status_id=payload.status_id,
            status_name=payload.status_name,
            expenses=payload.expenses,
            risk_level=0,
            requires_approval=True,
            is_active=payload.is_active,
            created_by=operator,
        )
    )


@router_v2.get("/response-templates")
async def list_response_templates(
    only_active: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ResponseTemplate)
    if only_active:
        stmt = stmt.where(ResponseTemplate.is_active.is_(True))
    templates = (await db.scalars(stmt.order_by(ResponseTemplate.key, ResponseTemplate.version.desc()))).all()
    return [
        {
            "id": item.id,
            "key": item.key,
            "version": item.version,
            "name": item.name,
            "template_text": item.template_text,
            "required_variables": item.required_variables,
            "is_active": item.is_active,
        }
        for item in templates
    ]


@router_v2.post("/response-templates", status_code=status.HTTP_201_CREATED)
async def publish_response_template(
    payload: ResponseTemplateWrite,
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    variables = template_variables(payload.template_text)
    active = await db.scalar(
        select(ResponseTemplate).where(
            ResponseTemplate.key == payload.key,
            ResponseTemplate.is_active.is_(True),
        )
    )
    if active is not None:
        active.is_active = False
    item = ResponseTemplate(
        key=payload.key,
        version=await _next_version(db, ResponseTemplate, ResponseTemplate.key, payload.key),
        name=payload.name,
        template_text=payload.template_text,
        required_variables=variables,
        is_active=True,
        created_by=operator,
    )
    db.add(item)
    await db.flush()
    db.add(
        RuleAuditLog(
            entity_type="response_template",
            entity_id=payload.key,
            changed_by=operator,
            change_type="publish",
            diff_json={"version": item.version, "required_variables": variables},
        )
    )
    await db.commit()
    await _notify_cache_invalidation()
    return {"id": item.id, "key": item.key, "version": item.version, "required_variables": variables}


@router_v2.get("/resolution-policies")
async def list_resolution_policies(
    only_active: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ResolutionPolicy, ResponseTemplate).join(
        ResponseTemplate, ResolutionPolicy.template_id == ResponseTemplate.id, isouter=True
    )
    if only_active:
        stmt = stmt.where(ResolutionPolicy.is_active.is_(True))
    rows = (await db.execute(stmt.order_by(ResolutionPolicy.outcome_key, ResolutionPolicy.version.desc()))).all()
    return [
        {
            "id": policy.id,
            "outcome_key": policy.outcome_key,
            "version": policy.version,
            "outcome_kind": policy.outcome_kind,
            "template_key": template.key if template else None,
            "template_version": template.version if template else None,
            "target_status_id": policy.target_status_id,
            "status_name": policy.status_name,
            "expenses": policy.expenses,
            "action_id": policy.action_id,
            "risk_level": policy.risk_level,
            "requires_approval": policy.requires_approval,
            "is_active": policy.is_active,
        }
        for policy, template in rows
    ]


@router_v2.post("/resolution-policies", status_code=status.HTTP_201_CREATED)
async def publish_resolution_policy(
    payload: ResolutionPolicyWrite,
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    template = await db.scalar(
        select(ResponseTemplate).where(
            ResponseTemplate.key == payload.template_key,
            ResponseTemplate.is_active.is_(True),
        )
    )
    if template is None:
        raise HTTPException(status_code=422, detail="active_template_required")
    if payload.outcome_kind == "action" and not payload.action_id:
        raise HTTPException(status_code=422, detail="action_id_required")
    if payload.outcome_kind != "action" and payload.target_status_id is None:
        raise HTTPException(status_code=422, detail="target_status_id_required")
    active = await db.scalar(
        select(ResolutionPolicy).where(
            ResolutionPolicy.outcome_key == payload.outcome_key,
            ResolutionPolicy.is_active.is_(True),
        )
    )
    if active is not None:
        active.is_active = False
    item = ResolutionPolicy(
        outcome_key=payload.outcome_key,
        version=await _next_version(db, ResolutionPolicy, ResolutionPolicy.outcome_key, payload.outcome_key),
        outcome_kind=payload.outcome_kind,
        template_id=template.id,
        target_status_id=payload.target_status_id,
        status_name=payload.status_name,
        expenses=payload.expenses,
        action_id=payload.action_id,
        risk_level=payload.risk_level,
        requires_approval=payload.requires_approval,
        is_active=True,
        created_by=operator,
    )
    db.add(item)
    await db.flush()
    db.add(
        RuleAuditLog(
            entity_type="resolution_policy",
            entity_id=payload.outcome_key,
            changed_by=operator,
            change_type="publish",
            diff_json={"version": item.version, **payload.model_dump()},
        )
    )
    await db.commit()
    return {"id": item.id, "outcome_key": item.outcome_key, "version": item.version}


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
