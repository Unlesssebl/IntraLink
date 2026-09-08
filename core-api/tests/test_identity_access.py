import datetime as dt

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.database.db import (
    ApprovalChallenge,
    AsyncSessionLocal,
    AuthSession,
    CommandApproval,
    CommandEvent,
    CommandOutbox,
    CommandRecord,
    Principal,
    PrincipalRole,
    SecurityEvent,
    ServiceCredential,
    TelegramLink,
    TelegramLinkCode,
    User,
)
from app.services.command_service import CommandService
from app.services.identity import (
    ROLE_PERMISSIONS,
    authenticate_human_token,
    authenticate_service,
    consume_telegram_link_code,
    create_service_credential,
    create_telegram_link_code,
    ensure_rbac_catalog,
    get_grants,
    issue_session,
    revoke_session,
)
from app.main import app


@pytest.fixture(autouse=True)
async def clean_identity_state():
    async with AsyncSessionLocal() as db:
        for model in (
            ApprovalChallenge, TelegramLinkCode, TelegramLink, AuthSession,
            ServiceCredential, CommandApproval, CommandEvent, CommandOutbox,
            CommandRecord, SecurityEvent, PrincipalRole, Principal,
        ):
            await db.execute(delete(model))
        await db.commit()
    yield


async def _principal(db, subject: str, role: str) -> Principal:
    await ensure_rbac_catalog(db, commit=False)
    item = Principal(type="human", subject=subject, display_name=subject, status="active")
    db.add(item)
    await db.flush()
    db.add(PrincipalRole(principal_id=item.id, role_name=role))
    await db.commit()
    return item


@pytest.mark.asyncio
async def test_session_revocation_and_database_grants_take_effect_immediately():
    async with AsyncSessionLocal() as db:
        principal = await _principal(db, "session.operator", "helpdesk_operator")
        access, _refresh, _expires = await issue_session(
            db, principal=principal, ip_address="127.0.0.1", user_agent="pytest"
        )
        context = await authenticate_human_token(db, access)
        assert "command:create" in context.permissions

        mapping = await db.get(PrincipalRole, (principal.id, "helpdesk_operator"))
        await db.delete(mapping)
        await db.commit()
        context = await authenticate_human_token(db, access)
        assert context.permissions == frozenset()

        await revoke_session(db, context.session_id)
        with pytest.raises(HTTPException) as exc:
            await authenticate_human_token(db, access)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_service_credentials_are_scoped_and_revocable():
    async with AsyncSessionLocal() as db:
        _principal_row, credential, secret = await create_service_credential(
            db,
            subject="pytest-worker",
            display_name="Pytest worker",
            scopes={"command:claim:windows", "command:finish:windows"},
        )
        context = await authenticate_service(db, key_id=credential.key_id, secret=secret)
        assert context.has("command:claim:windows")
        assert not context.has("command:claim:backend")

        credential.revoked_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await authenticate_service(db, key_id=credential.key_id, secret=secret)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_r2_requires_a_different_operator_but_admin_may_self_approve():
    async with AsyncSessionLocal() as db:
        operator = await _principal(db, "r2.operator", "helpdesk_operator")
        command, _ = await CommandService(db).create(
            action="grant_wlan",
            target={"identity": "person.test"},
            parameters={},
            idempotency_key="r2-separation-test",
            initiator=operator.subject,
            initiator_principal_id=operator.id,
            source="test",
            priority=5,
        )
        command_id = command.id
        grants = await get_grants(db, operator.id)
        with pytest.raises(HTTPException) as exc:
            await CommandService(db).approve(
                command_id,
                decision="approve",
                reason=None,
                operator=operator.subject,
                approver_principal_id=operator.id,
                approver_roles=frozenset({"helpdesk_operator"}),
                approver_permissions=grants,
            )
        assert exc.value.status_code == 403
        await db.rollback()

        admin = await _principal(db, "r2.admin", "system_admin")
        approved = await CommandService(db).approve(
            command_id,
            decision="approve",
            reason=None,
            operator=admin.subject,
            approver_principal_id=admin.id,
            approver_roles=frozenset({"system_admin"}),
            approver_permissions=ROLE_PERMISSIONS["system_admin"],
        )
        assert approved.status == "queued"


@pytest.mark.asyncio
async def test_telegram_link_code_is_single_use_and_stores_no_user_password():
    async with AsyncSessionLocal() as db:
        principal = await _principal(db, "telegram.operator", "helpdesk_operator")
        principal.external_id = "4242"
        await db.commit()
        code = await create_telegram_link_code(db, principal.id)
        linked = await consume_telegram_link_code(db, code=code, tg_user_id=991122)
        assert linked.id == principal.id
        link = await db.get(TelegramLink, 991122)
        assert link.status == "verified"
        user = await db.get(User, 991122)
        assert user.is_password_b64 == ""

        with pytest.raises(HTTPException) as exc:
            await consume_telegram_link_code(db, code=code, tg_user_id=991122)
        assert exc.value.status_code == 401


def test_every_mutation_route_has_a_concrete_permission_or_token_protocol():
    token_protocol_routes = {
        "/admin/api/login",
        "/admin/api/refresh",
        "/admin/api/logout",
        "/api/v1/admin/auth/login",
        "/api/v1/run/printer/token",
        "/api/v1/run/printer/complete",
        "/api/v1/desktop/launches/claim",
        "/api/v1/desktop/launches/result",
        "/api/v1/run/{token}/complete",
    }

    def permissions(dependant) -> set[str]:
        result: set[str] = set()
        for child in dependant.dependencies:
            permission = getattr(child.call, "required_permission", None)
            if permission:
                result.add(permission)
            result.update(permissions(child))
        return result

    uncovered = []
    for route in app.routes:
        methods = set(getattr(route, "methods", set())) & {"POST", "PUT", "PATCH", "DELETE"}
        if methods and route.path not in token_protocol_routes and not permissions(route.dependant):
            uncovered.append(f"{','.join(sorted(methods))} {route.path}")
    assert not uncovered, "\n".join(uncovered)
