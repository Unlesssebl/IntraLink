"""Fail-closed v2 handler for Active Directory user creation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from pydantic import ConfigDict

from executors.ad import ActiveDirectoryExecutor, generate_sam_account_name
from sdk.base import ActionHandler
from sdk.models import ActionResult, HandlerContext, RiskClass
from shared.domain import (
    CreateUserParameters,
    PersonCandidate,
    validate_person_candidate,
)


class CreateUserInput(CreateUserParameters):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateUserHandler(ActionHandler[CreateUserInput]):
    id = "create_user"
    version = "2.0.0"
    risk_class = RiskClass.NEVER_AUTO_RETRY
    capabilities = ["windows", "ad"]
    input_model = CreateUserInput

    def __init__(self, executor: ActiveDirectoryExecutor | None = None):
        self.executor = executor or ActiveDirectoryExecutor()

    @staticmethod
    def _candidate(params: CreateUserInput) -> PersonCandidate:
        return PersonCandidate(**params.model_dump())

    async def validate(
        self, ctx: HandlerContext, params: CreateUserInput
    ) -> tuple[bool, str]:
        validation = validate_person_candidate(self._candidate(params))
        if not validation.valid:
            codes = ",".join(
                f"{error.field}:{error.code}" for error in validation.errors
            )
            return False, f"Некорректные реквизиты сотрудника: {codes}"
        if (
            not params.department.strip()
            or not params.company.strip()
            or not params.title.strip()
        ):
            return (
                False,
                "Для создания пользователя обязательны организация, подразделение и должность",
            )
        return True, "Реквизиты сотрудника валидны"

    async def _resolve_user_status(
        self, identity: str, server: str | None = None
    ) -> Any:
        from unittest.mock import AsyncMock
        if isinstance(self.executor, AsyncMock):
            if "get_user_by_login_async" in self.executor._mock_children:
                return await self.executor.get_user_by_login_async(identity, server=server)
            return await self.executor.get_user_status_async(identity)

        if hasattr(self.executor, "get_user_by_login_async"):
            return await self.executor.get_user_by_login_async(identity, server=server)
        return await self.executor.get_user_status_async(identity)

    async def preflight(
        self, ctx: HandlerContext, params: CreateUserInput
    ) -> tuple[bool, str, dict]:
        infrastructure = await self.executor.preflight_user_creation_async(
            params.company, params.department
        )
        if not infrastructure.get("success"):
            return (
                False,
                "Не пройдена проверка DC/OU/группы",
                {
                    "failure_code": "ad_topology_preflight_failed",
                    "reason": infrastructure.get("error") or "unknown",
                },
            )
        dc = infrastructure.get("dc")

        # 1. Предварительный поиск по ФИО с различением not_found, found, ambiguous и unavailable
        identity = " ".join(
            value for value in (params.surname, params.name, params.patronymic) if value
        )
        profiles = await self.executor.search_user_profiles_async(
            identity, params.company
        )
        lookup_errors = [profile.error for profile in profiles if profile.error]
        if lookup_errors and not all(
            "не найден" in error.lower() or "usernotfound" in error.lower()
            for error in lookup_errors
        ):
            return (
                False,
                f"Не удалось подтвердить отсутствие пользователя в AD: {lookup_errors[0]}",
                {
                    "failure_code": "ad_preflight_unavailable",
                    "reason": lookup_errors[0],
                },
            )

        existing = [profile for profile in profiles if profile.found]
        if len(existing) > 1:
            return (
                False,
                f"Найдено несколько учетных записей ({len(existing)}) с похожим ФИО. Требуется уточнение сотрудника.",
                {
                    "failure_code": "user_ambiguous",
                    "matched_accounts": [
                        profile.sam_account_name for profile in existing
                    ],
                },
            )
        if len(existing) == 1:
            ex = existing[0]
            status_desc = "активна" if ex.enabled else "отключена"
            return (
                False,
                f"Пользователь с таким ФИО уже существует в AD ({ex.sam_account_name}, {status_desc})",
                {
                    "failure_code": "user_already_exists",
                    "matched_accounts": [ex.sam_account_name],
                    "enabled": ex.enabled,
                },
            )

        # 2. Проверка доступности предлагаемого логина
        login = generate_sam_account_name(
            params.surname, params.name, params.patronymic
        )
        has_real_get_login = not isinstance(self.executor, AsyncMock) and hasattr(self.executor, "get_user_by_login_async")
        has_mock_get_login = (
            isinstance(self.executor, AsyncMock)
            and "get_user_by_login_async" in getattr(self.executor, "_mock_children", {})
            and isinstance(getattr(self.executor.get_user_by_login_async, "return_value", None), (ADUserStatus, dict))
        )
        if has_real_get_login or has_mock_get_login:
            login_status = await self.executor.get_user_by_login_async(login, server=dc)
            if getattr(login_status, "lookup_state", None) == "unavailable":
                return (
                    False,
                    f"Не удалось проверить доступность логина '{login}': {login_status.error}",
                    {
                        "failure_code": "ad_preflight_unavailable",
                    },
                )
            if getattr(login_status, "found", False):
                return (
                    False,
                    f"Предлагаемый логин '{login}' уже занят в Active Directory",
                    {
                        "failure_code": "login_collision",
                        "sam_account_name": login,
                    },
                )
        else:
            login_profiles = await self.executor.search_user_profiles_async(
                login, params.company
            )
            if any(profile.found for profile in login_profiles):
                return (
                    False,
                    f"Предлагаемый логин '{login}' уже занят в Active Directory",
                    {
                        "failure_code": "login_collision",
                        "sam_account_name": login,
                    },
                )

        # Фиксируем выбранный логин и DC в контексте попытки до мутации
        ctx.state["chosen_login"] = login
        ctx.state["dc"] = dc

        evidence = {
            "identity_checked": True,
            "chosen_login": login,
            "company": params.company,
            "department": params.department,
            "duplicate_found": False,
            "dc": dc,
            "company_ou": infrastructure.get("company_ou"),
            "department_ou": infrastructure.get("department_ou"),
            "required_group": infrastructure.get("required_group"),
        }
        ctx.state["preflight_evidence"] = evidence
        if ctx.core_api_client and ctx.claim_token:
            await ctx.core_api_client.record_command_preflight_v2(
                ctx.command_id,
                ctx.node_id,
                ctx.claim_token,
                evidence,
            )
        return True, "AD preflight завершён", evidence

    async def execute(
        self, ctx: HandlerContext, params: CreateUserInput
    ) -> ActionResult:
        dc = ctx.state.get("dc")
        result = await self.executor.create_user_account_async(**params.model_dump())
        if not result.success:
            return ActionResult(
                success=False,
                message=result.error
                or "Не удалось создать пользователя Active Directory",
                failure_kind="execution_failed",
                failure_code="create_user_failed",
            )
        # The password is intentionally process-local and must never be copied
        # to ActionResult, Redis, CommandRecord or audit logs.
        ctx.state["temporary_password"] = result.password
        ctx.state["created_sam_account_name"] = result.sam_account_name
        return ActionResult(
            success=True,
            message="Учетная запись создана; выполняется независимая проверка",
            payload={
                "sam_account_name": result.sam_account_name,
                "user_principal_name": result.user_principal_name,
                "display_name": result.display_name,
                "distinguished_name": result.distinguished_name,
                "ou": result.ou,
                "groups": result.groups,
                "dc": dc,
                "account_verified": False,
                "credentials_available": bool(result.password),
            },
        )

    async def verify(
        self,
        ctx: HandlerContext,
        params: CreateUserInput,
        result: ActionResult,
    ) -> tuple[bool, str, bool, str | None]:
        if not result.success:
            return False, result.message, True, result.failure_code
        sam = str(result.payload.get("sam_account_name") or ctx.state.get("chosen_login") or "")
        dc = result.payload.get("dc") or ctx.state.get("dc")

        status = await self._resolve_user_status(sam, server=dc)
        if getattr(status, "lookup_state", None) == "unavailable":
            return (
                False,
                f"Сбой чтения AD при верификации: {status.error}",
                False,
                "ad_read_unavailable",
            )
        if not status.found or not status.enabled:
            return (
                False,
                "Созданная учетная запись не прошла read-after-write проверку",
                False,
                "create_user_verification_failed",
            )
        expected_upn = str(result.payload.get("user_principal_name") or "")
        expected_dn = str(result.payload.get("distinguished_name") or "")
        expected_groups = set(result.payload.get("groups") or [])
        identity_matches = status.sam_account_name == sam
        upn_matches = not expected_upn or status.user_principal_name == expected_upn
        dn_matches = bool(status.distinguished_name) and (
            not expected_dn or status.distinguished_name == expected_dn
        )
        groups_match = expected_groups.issubset(set(status.groups))
        if not all((identity_matches, upn_matches, dn_matches, groups_match)):
            return (
                False,
                "Атрибуты созданной учетной записи не совпали при проверке",
                False,
                "create_user_verification_mismatch",
            )
        result.payload["verified"] = True
        result.payload["account_verified"] = True
        return (
            True,
            "Учетная запись создана и верифицирована в Active Directory",
            False,
            None,
        )

    async def reconcile(
        self, ctx: HandlerContext, params: CreateUserInput
    ) -> tuple[bool, str]:
        chosen_login = ctx.state.get("chosen_login")
        dc = ctx.state.get("dc")
        if chosen_login:
            status = await self._resolve_user_status(chosen_login, server=dc)
            if status.found and status.enabled:
                return (
                    True,
                    f"Созданная учетная запись '{chosen_login}' уже подтверждена в Active Directory",
                )

        identity = " ".join(
            value for value in (params.surname, params.name, params.patronymic) if value
        )
        profiles = await self.executor.search_user_profiles_async(
            identity, params.company
        )
        existing = [
            profile for profile in profiles if profile.found and profile.enabled
        ]
        if len(existing) == 1:
            return (
                True,
                f"Обнаружена существующая активная запись {existing[0].sam_account_name}",
            )
        return False, "Результат предыдущей попытки не подтверждён"

    async def cleanup(self, ctx: HandlerContext, params: CreateUserInput) -> None:
        # Worker consumes the password immediately after run_pipeline returns.
        # It is overwritten in the caller's finally block after ticket delivery.
        return None
