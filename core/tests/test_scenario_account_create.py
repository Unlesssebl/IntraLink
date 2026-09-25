"""Unit tests for AccountCreateScenario (Onboarding in Active Directory)."""

from unittest.mock import MagicMock, patch

import pytest

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.scenarios.adapters.account_create import AccountCreateScenario


@pytest.fixture
def default_policy() -> AutopilotPolicyDTO:
    return AutopilotPolicyDTO(
        scenario_key="account_create",
        mode="FULL_AUTO",
        min_confidence=0.85,
    )


@pytest.mark.asyncio
async def test_account_create_can_handle():
    scenario = AccountCreateScenario()

    # 1. Match by ServiceId 55 and 232
    assert await scenario.can_handle(TaskDTO(Id=1, ServiceId=55)) is True
    assert await scenario.can_handle(TaskDTO(Id=2, ServiceId=232)) is True

    # 2. Match by TaskTypeId 1018
    assert await scenario.can_handle(TaskDTO(Id=3, TaskTypeId=1018)) is True

    # 3. Match by phrases
    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=4,
                Name="Заявка на создание пользователя",
                Description="Выход нового сотрудника в отдел продаж",
            )
        )
        is True
    )

    # 4. Exclusions (password reset, wlan, offboarding)
    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=5,
                Name="Сбросить пароль пользователя",
                Description="Забыл пароль от учетной записи",
            )
        )
        is False
    )
    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=6,
                Name="Увольнение сотрудника",
                Description="Заблокировать учетную запись в связи с увольнением",
            )
        )
        is False
    )


@pytest.mark.asyncio
async def test_account_create_preconditions_validation():
    scenario = AccountCreateScenario()

    # Missing all facts
    task_empty = TaskDTO(Id=10, Entities=ExtractedEntitiesDTO())
    res_empty = await scenario.validate_preconditions(task_empty)
    assert res_empty.is_valid is False
    assert set(res_empty.missing_facts) == {"last_name", "first_name", "department", "title"}
    assert "укажите обязательные реквизиты" in res_empty.clarification_prompt

    # Missing only title
    task_partial = TaskDTO(
        Id=11,
        Entities=ExtractedEntitiesDTO(
            last_name="Иванов",
            first_name="Иван",
            department="Департамент ИТ",
        ),
    )
    res_partial = await scenario.validate_preconditions(task_partial)
    assert res_partial.is_valid is False
    assert res_partial.missing_facts == ["title"]

    # Valid with all facts
    task_valid = TaskDTO(
        Id=12,
        Entities=ExtractedEntitiesDTO(
            last_name="Иванов",
            first_name="Иван",
            middle_name="Иванович",
            department="Департамент ИТ",
            title="Инженер",
            phone="+7 999 123-45-67",
        ),
    )
    res_valid = await scenario.validate_preconditions(task_valid)
    assert res_valid.is_valid is True
    assert not res_valid.missing_facts


@pytest.mark.asyncio
async def test_account_create_execution_and_zero_plaintext_policy(default_policy):
    mock_ad_pool = MagicMock()
    mock_ad_pool.config.domain = "corporate.loc"

    mock_conn = MagicMock()
    # 1st search returns existing user (collision), 2nd search returns empty (available)
    existing_entry = MagicMock()
    existing_entry.sAMAccountName.value = "ivanov.i"
    mock_conn.entries = [existing_entry]

    def search_side_effect(*args, **kwargs):
        if "ivanov.i)" in kwargs.get("search_filter", ""):
            mock_conn.entries = [existing_entry]
        else:
            mock_conn.entries = []

    mock_conn.search.side_effect = search_side_effect
    mock_conn.result = {"result": 0, "description": "success"}

    mock_ad_pool.connection_scope.return_value.__enter__.return_value = mock_conn

    scenario = AccountCreateScenario(ad_pool=mock_ad_pool)

    task = TaskDTO(
        Id=501,
        Entities=ExtractedEntitiesDTO(
            last_name="Иванов",
            first_name="Иван",
            middle_name="Иванович",
            department="Бухгалтерия",
            title="Ведущий бухгалтер",
            phone="1234",
            company="ПАО Корпорация",
        ),
    )

    res = await scenario.execute(task, default_policy)

    assert res.success is True
    assert res.target_status_id == 3
    assert res.action_taken == "account_create"

    # Collision resolution: ivanov.i collided, so ivanov.i2 must be chosen
    assert res.metadata["sam_account_name"] == "ivanov.i2"
    assert res.metadata["upn"] == "ivanov.i2@corporate.loc"

    # Verify LDAP add was called with user details
    mock_conn.add.assert_called_once()
    add_args = mock_conn.add.call_args.kwargs
    assert add_args["attributes"]["sAMAccountName"] == "ivanov.i2"
    assert add_args["attributes"]["givenName"] == "Иван"
    assert add_args["attributes"]["sn"] == "Иванов"
    assert add_args["attributes"]["displayName"] == "Иванов Иван Иванович"
    assert add_args["attributes"]["department"] == "Бухгалтерия"

    # Verify pwdLastSet = 0 was applied (Force password change on first logon)
    modify_calls = mock_conn.modify.call_args_list
    pwd_last_set_call = [c for c in modify_calls if "pwdLastSet" in c.args[1]]
    assert len(pwd_last_set_call) == 1
    assert pwd_last_set_call[0].args[1]["pwdLastSet"][0][1] == [0]

    # Zero-Plaintext Policy:
    # 1. resolution_comment (public for applicant) NEVER contains password
    assert "пароль" in res.resolution_comment.lower()
    # It must mention that temporary password was sent via private channel
    assert "Временный пароль передан руководителю/HR по защищенному регламентному каналу" in res.resolution_comment
    # No plaintext generated password string should leak into resolution_comment
    assert "Временный пароль: " not in res.resolution_comment

    # 2. technical_note (internal note) has the password and audit trail
    assert "🤖 [Автопилот: Создание учетной записи]" in res.technical_note
    assert "Временный пароль:" in res.technical_note
    assert "ivanov.i2" in res.technical_note
