"""
Роутер Skills Hub & Action Registry для управления каталогом навыков/действий и политиками безопасности.
Позволяет просматривать доступные действия, настраивать режимы исполнения (Auto / Confirm)
и активировать аварийный Killswitch для каждого действия.
"""

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.routers.deps import verify_admin_jwt, verify_admin_or_api_key, verify_trusted_origin
from app.services.actions import (
    ActionDefinition,
    PolicyMode,
    get_action_registry,
    get_policy_engine,
)

logger = logging.getLogger("core_api.routers.skills_admin")

router = APIRouter(
    prefix="/api/v1/skills",
    tags=["Skills Hub & Action Registry"],
    dependencies=[Depends(verify_admin_or_api_key)],
)


class UpdatePolicyRequest(BaseModel):
    mode: PolicyMode = Field(..., description="Новый режим политики: auto | confirm | disabled")


class ActionItemResponse(BaseModel):
    id: str
    name: str
    category: str
    description: str
    default_mode: PolicyMode
    effective_mode: PolicyMode
    target_type: str
    parameters_schema: dict[str, Any]


@router.get("", response_model=list[ActionItemResponse], status_code=status.HTTP_200_OK)
async def list_skills(
    registry=Depends(get_action_registry),
    policy_engine=Depends(get_policy_engine),
):
    """Возвращает каталог всех зарегистрированных действий с действующими политиками безопасности."""
    actions = registry.list_all()
    result = []
    for a in actions:
        eff_mode = await policy_engine.get_action_policy(a.id)
        result.append(
            ActionItemResponse(
                id=a.id,
                name=a.name,
                category=a.category,
                description=a.description,
                default_mode=a.default_mode,
                effective_mode=eff_mode,
                target_type=a.target_type,
                parameters_schema=a.parameters_schema,
            )
        )
    return result


@router.get("/{action_id}", response_model=ActionItemResponse, status_code=status.HTTP_200_OK)
async def get_skill_details(
    action_id: str,
    registry=Depends(get_action_registry),
    policy_engine=Depends(get_policy_engine),
):
    """Возвращает детальную информацию о конкретном действии."""
    a = registry.get(action_id)
    if not a:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Действие '{action_id}' не найдено в реестре.",
        )
    eff_mode = await policy_engine.get_action_policy(a.id)
    return ActionItemResponse(
        id=a.id,
        name=a.name,
        category=a.category,
        description=a.description,
        default_mode=a.default_mode,
        effective_mode=eff_mode,
        target_type=a.target_type,
        parameters_schema=a.parameters_schema,
    )


@router.patch("/{action_id}/policy", status_code=status.HTTP_200_OK)
async def update_skill_policy(
    action_id: str,
    payload: UpdatePolicyRequest,
    operator: str = Depends(verify_admin_jwt),
    _origin: None = Depends(verify_trusted_origin),
    registry=Depends(get_action_registry),
    policy_engine=Depends(get_policy_engine),
):
    """
    Обновляет политику выполнения действия (Auto / Confirm / Disabled).
    Установка mode='disabled' активирует мгновенный Killswitch.
    """
    a = registry.get(action_id)
    if not a:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Действие '{action_id}' не зарегистрировано.",
        )

    try:
        await policy_engine.set_action_policy(
            action_id=action_id,
            mode=payload.mode,
            actor=operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "status": "success",
        "action_id": action_id,
        "effective_mode": payload.mode.value,
        "message": f"Политика действия '{action_id}' успешно изменена на '{payload.mode.value}'.",
    }


@router.delete("/{action_id}/policy", status_code=status.HTTP_200_OK)
async def reset_skill_policy(
    action_id: str,
    _operator: str = Depends(verify_admin_jwt),
    _origin: None = Depends(verify_trusted_origin),
    registry=Depends(get_action_registry),
    policy_engine=Depends(get_policy_engine),
):
    """Сбрасывает оверрайд политики действия к значению по умолчанию из манифеста."""
    a = registry.get(action_id)
    if not a:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Действие '{action_id}' не найдено.",
        )

    await policy_engine.reset_action_policy(action_id)
    return {
        "status": "success",
        "action_id": action_id,
        "effective_mode": a.default_mode.value,
        "message": f"Политика действия '{action_id}' сброшена к значению по умолчанию ('{a.default_mode.value}').",
    }
