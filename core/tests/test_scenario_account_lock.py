"""Unit tests for AccountLockScenario (Offboarding in Active Directory)."""

from unittest.mock import MagicMock

import pytest

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.scenarios.adapters.account_lock import AccountLockScenario


@pytest.fixture
def default_policy() -> AutopilotPolicyDTO:
    return AutopilotPolicyDTO(
        scenario_key="account_lock",
        mode="FULL_AUTO",
        min_confidence=0.85,
    )


@pytest.mark.asyncio
async def test_account_lock_can_handle():
    scenario = AccountLockScenario()

    # 1. Match by ServiceId 8 (Offboarding / Revoke access)
    assert await scenario.can_handle(TaskDTO(Id=1, ServiceId=8)) is True

    # 2. Match by phrases
    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=2,
                Name="Увольнение сотрудника",
                Description="Прошу заблокировать учетную запись петров.п в связи с увольнением",
            )
        )
        is True
    )

    # 3. Exclusions (account creation, wifi)
    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=3,
                Name="Создать нового пользователя",
                Description="Выход сотрудника",
            )
        )
        is False
    )


@pytest.mark.asyncio
async def test_account_lock_preconditions_validation():
    scenario = AccountLockScenario()

    # Missing target_user
    task_no_user = TaskDTO(Id=10, Entities=ExtractedEntitiesDTO())
    res_no_user = await scenario.validate_preconditions(task_no_user)
    assert res_no_user.is_valid is False
    assert res_no_user.missing_facts == ["target_user"]
    assert "укажите логин (sAMAccountName) или точное ФИО" in res_no_user.clarification_prompt

    # Self-locking guard: applicant tries to disable themselves
    task_self = TaskDTO(
        Id=11,
        ApplicantName="Петров Петр Петрович",
        Entities=ExtractedEntitiesDTO(target_user="Петров Петр Петрович"),
    )
    res_self = await scenario.validate_preconditions(task_self)
    assert res_self.is_valid is False
    assert "unauthorized_initiator" in res_self.environment_barriers
    assert "руководителя подразделения или службы управления персоналом" in res_self.clarification_prompt

    # Valid request from HR manager
    task_valid = TaskDTO(
        Id=12,
        ApplicantName="Иванова Анна (HR)",
        Entities=ExtractedEntitiesDTO(target_user="petrov.p"),
    )
    res_valid = await scenario.validate_preconditions(task_valid)
    assert res_valid.is_valid is True


@pytest.mark.asyncio
async def test_account_lock_execution(default_policy):
    mock_ad_pool = MagicMock()
    mock_ad_pool.config.domain = "corporate.loc"

    mock_conn = MagicMock()
    user_entry = MagicMock()
    user_entry.sAMAccountName.value = "petrov.p"
    user_entry.distinguishedName.value = "CN=Петров Петр,OU=Users,DC=corporate,DC=loc"
    user_entry.userAccountControl.value = 512  # NORMAL_ACCOUNT
    user_entry.cn.value = "Петров Петр"

    verified_entry = MagicMock()
    verified_entry.userAccountControl.value = 514

    def search_side_effect(*args, **kwargs):
        if kwargs.get("search_scope") == "BASE":
            mock_conn.entries = [verified_entry]
        else:
            mock_conn.entries = [user_entry]

    mock_conn.search.side_effect = search_side_effect
    mock_conn.result = {"result": 0, "description": "success"}

    mock_ad_pool.connection_scope.return_value.__enter__.return_value = mock_conn

    scenario = AccountLockScenario(ad_pool=mock_ad_pool)

    task = TaskDTO(
        Id=502,
        ApplicantName="Сидоров С.С. (Руководитель отдела)",
        Entities=ExtractedEntitiesDTO(target_user="petrov.p"),
    )

    res = await scenario.execute(task, default_policy)

    assert res.success is True
    assert res.target_status_id == 3
    assert res.action_taken == "account_lock"

    # Verify search was performed
    assert mock_conn.search.call_count == 2

    # Verify userAccountControl was updated: 512 | 0x0002 = 514 (ACCOUNTDISABLE)
    mock_conn.modify.assert_called_once()
    modify_dn = mock_conn.modify.call_args.args[0]
    modify_dict = mock_conn.modify.call_args.args[1]
    assert modify_dn == "CN=Петров Петр,OU=Users,DC=corporate,DC=loc"
    assert modify_dict["userAccountControl"][0][1] == [514]

    # Verify notes
    assert "petrov.p" in res.resolution_comment
    assert "заблокирована в Active Directory" in res.resolution_comment
    assert "ACCOUNTDISABLE 0x0002 установлен" in res.technical_note


@pytest.mark.asyncio
async def test_account_lock_escapes_filter_and_rejects_ambiguous_target(default_policy):
    mock_ad_pool = MagicMock()
    mock_ad_pool.config.domain = "corporate.loc"
    mock_conn = MagicMock()
    mock_conn.entries = [MagicMock(), MagicMock()]
    mock_ad_pool.connection_scope.return_value.__enter__.return_value = mock_conn

    scenario = AccountLockScenario(ad_pool=mock_ad_pool)
    task = TaskDTO(Id=503, Entities=ExtractedEntitiesDTO(target_user="*)(cn=*)"))
    result = await scenario.execute(task, default_policy)

    assert result.success is False
    assert "неоднозначно" in (result.error or "")
    search_filter = mock_conn.search.call_args.kwargs["search_filter"]
    assert "\\2a\\29\\28cn=\\2a\\29" in search_filter
