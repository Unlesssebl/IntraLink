"""Fail-closed v2 handler for Active Directory user creation."""

from __future__ import annotations

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
        identity = " ".join(
            value for value in (params.surname, params.name, params.patronymic) if value
        )
        profiles = await self.executor.search_user_profiles_async(
            identity, params.company
        )
        existing = [profile for profile in profiles if profile.found]
        if existing:
            return (
                False,
                "Пользователь с таким ФИО уже существует",
                {
                    "failure_code": "user_already_exists",
                    "matched_accounts": [
                        profile.sam_account_name for profile in existing
                    ],
                },
            )
        login = generate_sam_account_name(
            params.surname, params.name, params.patronymic
        )
        login_profiles = await self.executor.search_user_profiles_async(
            login, params.company
        )
        if any(profile.found for profile in login_profiles):
            return (
                False,
                "Предлагаемый логин уже занят",
                {
                    "failure_code": "login_collision",
                    "sam_account_name": login,
                },
            )
        lookup_errors = [profile.error for profile in profiles if profile.error]
        if lookup_errors and not all(
            "не найден" in error.lower() for error in lookup_errors
        ):
            return (
                False,
                "Не удалось подтвердить отсутствие пользователя в AD",
                {
                    "failure_code": "ad_preflight_unavailable",
                },
            )
        evidence = {
            "identity_checked": True,
            "company": params.company,
            "department": params.department,
            "duplicate_found": False,
            "dc": infrastructure.get("dc"),
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
        sam = str(result.payload.get("sam_account_name") or "")
        status = await self.executor.get_user_status_async(sam, params.company)
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
        return (
            True,
            "Учетная запись создана и верифицирована в Active Directory",
            False,
            None,
        )

    async def reconcile(
        self, ctx: HandlerContext, params: CreateUserInput
    ) -> tuple[bool, str]:
        identity = " ".join(
            value for value in (params.surname, params.name, params.patronymic) if value
        )
        profiles = await self.executor.search_user_profiles_async(
            identity, params.company
        )
        existing = [
            profile for profile in profiles if profile.found and profile.enabled
        ]
        if existing:
            return (
                True,
                f"Обнаружена существующая активная запись {existing[0].sam_account_name}",
            )
        return False, "Результат предыдущей попытки не подтверждён"

    async def cleanup(self, ctx: HandlerContext, params: CreateUserInput) -> None:
        # Worker consumes the password immediately after run_pipeline returns.
        # It is overwritten in the caller's finally block after ticket delivery.
        return None
