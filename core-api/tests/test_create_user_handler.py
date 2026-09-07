import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

WORKER_DIR = Path(__file__).resolve().parent.parent.parent / "execution-worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from executors.ad import ADUserCreationResult, ADUserProfile, ADUserStatus  # noqa: E402
from handlers.create_user import CreateUserHandler  # noqa: E402
from sdk.models import HandlerContext, RiskClass  # noqa: E402


def _params(**overrides):
    result = {
        "surname": "Иванов",
        "name": "Иван",
        "patronymic": "Иванович",
        "company": "Интра",
        "department": "ИТ",
        "title": "Инженер",
    }
    result.update(overrides)
    return result


@pytest.mark.asyncio
async def test_invalid_create_user_never_reaches_ad():
    executor = AsyncMock()
    handler = CreateUserHandler(executor)
    result = await handler.run_pipeline(
        HandlerContext(command_id="1"), _params(surname="test")
    )
    assert result.success is False
    executor.create_user_account_async.assert_not_awaited()
    assert handler.risk_class is RiskClass.NEVER_AUTO_RETRY


@pytest.mark.asyncio
async def test_duplicate_user_stops_in_preflight():
    executor = AsyncMock()
    executor.preflight_user_creation_async.return_value = {
        "success": True,
        "dc": "dc1",
        "company_ou": "OU=Интра",
        "department_ou": "OU=ИТ,OU=Интра",
        "required_group": "HLP_Интра",
    }
    executor.search_user_profiles_async.return_value = [
        ADUserProfile(found=True, sam_account_name="ivanov.i.i", enabled=True)
    ]
    result = await CreateUserHandler(executor).run_pipeline(
        HandlerContext(command_id="2"), _params()
    )
    assert result.success is False
    assert result.failure_code == "user_already_exists"
    executor.create_user_account_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_requires_read_after_write_and_keeps_password_process_local():
    executor = AsyncMock()
    executor.preflight_user_creation_async.return_value = {
        "success": True,
        "dc": "dc1",
        "company_ou": "OU=Интра",
        "department_ou": "OU=ИТ,OU=Интра",
        "required_group": "HLP_Интра",
    }
    executor.search_user_profiles_async.return_value = [
        ADUserProfile(found=False, error="не найден")
    ]
    executor.create_user_account_async.return_value = ADUserCreationResult(
        success=True,
        sam_account_name="ivanov.i.i",
        display_name="Иванов Иван Иванович",
        password="Secret-123",
        user_principal_name="ivanov.i.i@corporate.loc",
        distinguished_name="CN=Иванов Иван Иванович,OU=ИТ,OU=Интра",
        groups=["HLP_Интра"],
    )
    executor.get_user_status_async.return_value = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="ivanov.i.i",
        user_principal_name="ivanov.i.i@corporate.loc",
        distinguished_name="CN=Иванов Иван Иванович,OU=ИТ,OU=Интра",
        groups=["HLP_Интра"],
    )
    context = HandlerContext(command_id="3")
    result = await CreateUserHandler(executor).run_pipeline(context, _params())
    assert result.success is True
    assert result.payload["verified"] is True
    assert "password" not in str(result.model_dump()).lower()
    assert context.state["temporary_password"] == "Secret-123"
