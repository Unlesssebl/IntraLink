"""PostgreSQL-backed identities, RBAC, sessions and scoped service credentials."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import (
    ApprovalChallenge,
    AuthSession,
    Permission,
    Principal,
    PrincipalRole,
    Role,
    RolePermission,
    SecurityEvent,
    ServiceCredential,
    TelegramLink,
    TelegramLinkCode,
    User,
)

UTC = dt.timezone.utc
JWT_ALGORITHM = "HS256"
PASSWORD_HASHER = PasswordHasher()

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "helpdesk_operator": frozenset({
        "task:read", "task:mutate", "triage:read", "triage:mutate", "ai:use",
        "command:read", "command:create", "command:approve:r1", "command:approve:r2", "command:cancel",
        "diagnostic:run", "events:read",
        "identity:link:self",
    }),
    "system_admin": frozenset({
        "task:read", "task:mutate", "triage:read", "triage:mutate", "ai:use",
        "command:read", "command:create", "command:approve:r1", "command:approve:r2",
        "command:cancel", "command:review", "policy:manage", "identity:manage",
        "credentials:manage", "rules:manage", "diagnostic:run", "events:read", "audit:read",
        "identity:link:self",
    }),
    "security_auditor": frozenset({
        "task:read", "triage:read", "command:read", "events:read", "audit:read", "identity:link:self",
    }),
}


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    principal_id: uuid.UUID | None
    principal_type: str
    subject: str
    display_name: str
    roles: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    grants: frozenset[str] = field(default_factory=frozenset)
    session_id: uuid.UUID | None = None
    auth_method: str = "unknown"

    @property
    def permissions(self) -> frozenset[str]:
        return self.scopes | self.grants

    def has(self, permission: str) -> bool:
        return permission in self.permissions or "*" in self.permissions


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def user_agent_hash(user_agent: str | None) -> str | None:
    return token_hash(user_agent) if user_agent else None


def _utc(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive values for timezone columns; PostgreSQL does not."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def ensure_rbac_catalog(db: AsyncSession, *, commit: bool = True) -> None:
    """Idempotently seed the fixed role and permission catalog for SQLite/dev."""
    for role_name in ROLE_PERMISSIONS:
        if await db.get(Role, role_name) is None:
            db.add(Role(name=role_name, description=role_name.replace("_", " ")))
    permission_names = sorted(set().union(*ROLE_PERMISSIONS.values()))
    for permission_name in permission_names:
        if await db.get(Permission, permission_name) is None:
            db.add(Permission(name=permission_name, description=permission_name))
    await db.flush()
    for role_name, permissions in ROLE_PERMISSIONS.items():
        for permission_name in permissions:
            mapping = await db.get(RolePermission, (role_name, permission_name))
            if mapping is None:
                db.add(RolePermission(role_name=role_name, permission_name=permission_name))
    await db.flush()
    if commit:
        await db.commit()


async def get_roles(db: AsyncSession, principal_id: uuid.UUID) -> frozenset[str]:
    values = await db.scalars(
        select(PrincipalRole.role_name).where(PrincipalRole.principal_id == principal_id)
    )
    return frozenset(values.all())


async def get_grants(db: AsyncSession, principal_id: uuid.UUID) -> frozenset[str]:
    values = await db.scalars(
        select(RolePermission.permission_name)
        .join(PrincipalRole, PrincipalRole.role_name == RolePermission.role_name)
        .where(PrincipalRole.principal_id == principal_id)
    )
    return frozenset(values.all())


async def record_security_event(
    db: AsyncSession,
    *,
    event_type: str,
    outcome: str,
    context: PrincipalContext | None = None,
    principal_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    details: dict | None = None,
    commit: bool = False,
) -> None:
    safe_details = {
        key: value for key, value in (details or {}).items()
        if key.lower() not in {"token", "secret", "password", "authorization"}
    }
    db.add(SecurityEvent(
        event_type=event_type,
        outcome=outcome,
        principal_id=principal_id or (context.principal_id if context else None),
        auth_method=context.auth_method if context else None,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        details_json=safe_details,
    ))
    if commit:
        await db.commit()


async def ensure_human_principal(
    db: AsyncSession,
    *,
    username: str,
    display_name: str | None = None,
    external_user_id: int | None = None,
) -> Principal:
    subject = username.strip().lower()
    await ensure_rbac_catalog(db, commit=False)
    principal = await db.scalar(
        select(Principal).where(Principal.type == "human", Principal.subject == subject)
    )
    if principal is None:
        principal = Principal(
            type="human",
            subject=subject,
            display_name=display_name or username.strip(),
            external_id=str(external_user_id) if external_user_id is not None else None,
            status="active",
        )
        db.add(principal)
        await db.flush()
        has_admin = await db.scalar(
            select(PrincipalRole.principal_id)
            .join(Principal, Principal.id == PrincipalRole.principal_id)
            .where(PrincipalRole.role_name == "system_admin", Principal.status == "active")
            .limit(1)
        )
        bootstrap_logins = {
            item.strip().lower() for item in (settings.ADMIN_LOGINS or "").split(",") if item.strip()
        }
        role = (
            "system_admin"
            if settings.ALLOW_LEGACY_SHARED_KEYS and has_admin is None and subject in bootstrap_logins
            else "helpdesk_operator"
        )
        db.add(PrincipalRole(principal_id=principal.id, role_name=role))
        await record_security_event(
            db,
            event_type="principal.created",
            outcome="success",
            principal_id=principal.id,
            resource_type="principal",
            resource_id=str(principal.id),
            details={"role": role, "external_user_id": external_user_id},
        )
        await db.commit()
        await db.refresh(principal)
    elif external_user_id is not None and principal.external_id != str(external_user_id):
        principal.external_id = str(external_user_id)
        await db.commit()
    return principal


def _encode_access_token(
    principal: Principal,
    roles: frozenset[str],
    session_id: uuid.UUID,
) -> tuple[str, int]:
    now = dt.datetime.now(UTC)
    ttl_seconds = settings.ACCESS_TOKEN_TTL_MINUTES * 60
    payload = {
        "sub": principal.subject,
        "principal_id": str(principal.id),
        "external_id": principal.external_id,
        "roles": sorted(roles),
        "session_id": str(session_id),
        "jti": str(uuid.uuid4()),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + dt.timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, settings.JWT_SECRET or "", algorithm=JWT_ALGORITHM), ttl_seconds


async def issue_session(
    db: AsyncSession,
    *,
    principal: Principal,
    ip_address: str | None,
    user_agent: str | None,
    rotated_from_id: uuid.UUID | None = None,
) -> tuple[str, str, int]:
    roles = await get_roles(db, principal.id)
    refresh_token = secrets.token_urlsafe(48)
    session = AuthSession(
        principal_id=principal.id,
        refresh_token_hash=token_hash(refresh_token),
        rotated_from_id=rotated_from_id,
        expires_at=dt.datetime.now(UTC) + dt.timedelta(hours=settings.REFRESH_SESSION_TTL_HOURS),
        ip_address=ip_address,
        user_agent_hash=user_agent_hash(user_agent),
    )
    db.add(session)
    await db.flush()
    access_token, expires_in = _encode_access_token(principal, roles, session.id)
    await record_security_event(
        db,
        event_type="auth.login",
        outcome="success",
        principal_id=principal.id,
        resource_type="session",
        resource_id=str(session.id),
        ip_address=ip_address,
    )
    await db.commit()
    return access_token, refresh_token, expires_in


async def rotate_refresh_session(
    db: AsyncSession,
    *,
    refresh_token: str,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[str, str, int]:
    now = dt.datetime.now(UTC)
    session = await db.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == token_hash(refresh_token)).with_for_update()
    )
    if session is None or session.revoked_at is not None or _utc(session.expires_at) <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh session is invalid or expired")
    principal = await db.get(Principal, session.principal_id)
    if principal is None or principal.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Principal is inactive")
    session.revoked_at = now
    return await issue_session(
        db,
        principal=principal,
        ip_address=ip_address,
        user_agent=user_agent,
        rotated_from_id=session.id,
    )


async def revoke_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    session = await db.get(AuthSession, session_id)
    if session and session.revoked_at is None:
        session.revoked_at = dt.datetime.now(UTC)
        await db.commit()


async def revoke_refresh_token(db: AsyncSession, refresh_token: str) -> None:
    session = await db.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == token_hash(refresh_token))
    )
    if session and session.revoked_at is None:
        session.revoked_at = dt.datetime.now(UTC)
        await record_security_event(
            db,
            event_type="auth.logout",
            outcome="success",
            principal_id=session.principal_id,
            resource_type="session",
            resource_id=str(session.id),
        )
        await db.commit()


async def authenticate_human_token(db: AsyncSession, token: str) -> PrincipalContext:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET or "",
            algorithms=[JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            options={"require": ["sub", "principal_id", "session_id", "jti", "iat", "nbf", "exp"]},
        )
        principal_id = uuid.UUID(payload["principal_id"])
        session_id = uuid.UUID(payload["session_id"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token") from exc

    now = dt.datetime.now(UTC)
    session = await db.get(AuthSession, session_id)
    principal = await db.get(Principal, principal_id)
    if (
        session is None
        or session.principal_id != principal_id
        or session.revoked_at is not None
        or _utc(session.expires_at) <= now
        or principal is None
        or principal.status != "active"
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session or principal is inactive")
    roles = await get_roles(db, principal_id)
    grants = await get_grants(db, principal_id)
    return PrincipalContext(
        principal_id=principal.id,
        principal_type="human",
        subject=principal.subject,
        display_name=principal.display_name,
        roles=roles,
        grants=grants,
        session_id=session.id,
        auth_method="jwt",
    )


async def create_service_credential(
    db: AsyncSession,
    *,
    subject: str,
    display_name: str,
    scopes: set[str],
    expires_at: dt.datetime | None = None,
    creator_context: PrincipalContext | None = None,
    reason: str | None = None,
) -> tuple[Principal, ServiceCredential, str]:
    principal = await db.scalar(
        select(Principal).where(Principal.type == "service", Principal.subject == subject)
    )
    if principal is None:
        principal = Principal(type="service", subject=subject, display_name=display_name, status="active")
        db.add(principal)
        await db.flush()
    secret = secrets.token_urlsafe(48)
    credential = ServiceCredential(
        principal_id=principal.id,
        key_id=f"svc_{secrets.token_urlsafe(12)}",
        secret_hash=PASSWORD_HASHER.hash(secret),
        scopes_json=sorted(scopes),
        expires_at=expires_at,
    )
    db.add(credential)
    await record_security_event(
        db,
        event_type="service_credential.created",
        outcome="success",
        context=creator_context,
        principal_id=creator_context.principal_id if creator_context else principal.id,
        resource_type="service_credential",
        resource_id=credential.key_id,
        details={"scopes": sorted(scopes), "service_principal_id": str(principal.id), "reason": reason},
    )
    await db.commit()
    await db.refresh(credential)
    return principal, credential, secret


async def authenticate_service(
    db: AsyncSession,
    *,
    key_id: str,
    secret: str,
) -> PrincipalContext:
    credential = await db.scalar(
        select(ServiceCredential).where(ServiceCredential.key_id == key_id)
    )
    now = dt.datetime.now(UTC)
    invalid = (
        credential is None
        or credential.revoked_at is not None
        or (credential.expires_at is not None and _utc(credential.expires_at) <= now)
    )
    if invalid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid service credential")
    try:
        PASSWORD_HASHER.verify(credential.secret_hash, secret)
    except (VerifyMismatchError, InvalidHashError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid service credential") from exc
    principal = await db.get(Principal, credential.principal_id)
    if principal is None or principal.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Service principal is inactive")
    credential.last_used_at = now
    await db.commit()
    return PrincipalContext(
        principal_id=principal.id,
        principal_type="service",
        subject=principal.subject,
        display_name=principal.display_name,
        scopes=frozenset(credential.scopes_json or []),
        auth_method="service_key",
    )


def require_context_permission(context: PrincipalContext, permission: str) -> None:
    if not context.has(permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")


async def create_approval_challenge(
    db: AsyncSession,
    *,
    command_id: uuid.UUID,
    request_hash: str,
    tg_user_id: int,
    ttl_seconds: int = 600,
) -> str:
    link = await db.get(TelegramLink, tg_user_id)
    if link is None or link.status != "verified" or link.revoked_at is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Telegram identity is not verified")
    principal = await db.get(Principal, link.principal_id)
    if principal is None or principal.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Linked principal is inactive")
    token = secrets.token_urlsafe(32)
    db.add(ApprovalChallenge(
        command_id=command_id,
        principal_id=principal.id,
        request_hash=request_hash,
        token_hash=token_hash(token),
        allowed_decisions_json=["approve", "reject"],
        expires_at=dt.datetime.now(UTC) + dt.timedelta(seconds=ttl_seconds),
    ))
    await record_security_event(
        db,
        event_type="approval_challenge.created",
        outcome="success",
        principal_id=principal.id,
        resource_type="command",
        resource_id=str(command_id),
    )
    await db.commit()
    return token


async def consume_approval_challenge(
    db: AsyncSession,
    *,
    command_id: uuid.UUID,
    request_hash: str,
    token: str,
    decision: str,
) -> PrincipalContext:
    now = dt.datetime.now(UTC)
    challenge = await db.scalar(
        select(ApprovalChallenge)
        .where(ApprovalChallenge.token_hash == token_hash(token))
        .with_for_update()
    )
    if (
        challenge is None
        or challenge.command_id != command_id
        or challenge.request_hash != request_hash
        or challenge.used_at is not None
        or _utc(challenge.expires_at) <= now
        or decision not in (challenge.allowed_decisions_json or [])
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Approval challenge is invalid or expired")
    principal = await db.get(Principal, challenge.principal_id)
    if principal is None or principal.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Approver principal is inactive")
    challenge.used_at = now
    challenge.used_decision = decision
    roles = await get_roles(db, principal.id)
    grants = await get_grants(db, principal.id)
    await db.flush()
    return PrincipalContext(
        principal_id=principal.id,
        principal_type="human",
        subject=principal.subject,
        display_name=principal.display_name,
        roles=roles,
        grants=grants,
        auth_method="telegram_challenge",
    )


async def create_telegram_link_code(db: AsyncSession, principal_id: uuid.UUID) -> str:
    code = secrets.token_urlsafe(9)
    db.add(TelegramLinkCode(
        principal_id=principal_id,
        code_hash=token_hash(code),
        expires_at=dt.datetime.now(UTC) + dt.timedelta(minutes=10),
    ))
    await record_security_event(
        db,
        event_type="telegram_link.challenge_created",
        outcome="success",
        principal_id=principal_id,
        resource_type="principal",
        resource_id=str(principal_id),
    )
    await db.commit()
    return code


async def consume_telegram_link_code(
    db: AsyncSession, *, code: str, tg_user_id: int
) -> Principal:
    now = dt.datetime.now(UTC)
    item = await db.scalar(
        select(TelegramLinkCode)
        .where(TelegramLinkCode.code_hash == token_hash(code))
        .with_for_update()
    )
    if item is None or item.used_at is not None or _utc(item.expires_at) <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Telegram link code is invalid or expired")
    principal = await db.get(Principal, item.principal_id)
    if principal is None or principal.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Principal is inactive")
    existing_for_principal = await db.scalar(
        select(TelegramLink).where(TelegramLink.principal_id == principal.id)
    )
    if existing_for_principal and existing_for_principal.tg_user_id != tg_user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Principal already has a Telegram identity")
    existing = await db.get(TelegramLink, tg_user_id)
    if existing and existing.principal_id != principal.id:
        await db.delete(existing)
        await db.flush()
    link = existing_for_principal or TelegramLink(tg_user_id=tg_user_id, principal_id=principal.id)
    link.status = "verified"
    link.verified_at = now
    link.revoked_at = None
    if existing_for_principal is None:
        db.add(link)
    item.used_at = now
    legacy_user = await db.get(User, tg_user_id)
    try:
        external_user_id = int(principal.external_id) if principal.external_id else None
    except ValueError:
        external_user_id = None
    if legacy_user is None:
        db.add(User(
            tg_user_id=tg_user_id,
            is_login=principal.subject,
            is_password_b64="",
            is_user_id=external_user_id,
        ))
    else:
        legacy_user.is_login = principal.subject
        legacy_user.is_password_b64 = ""
        legacy_user.is_user_id = external_user_id
    await record_security_event(
        db,
        event_type="telegram_link.verified",
        outcome="success",
        principal_id=principal.id,
        resource_type="telegram_identity",
        resource_id=str(tg_user_id),
    )
    await db.commit()
    return principal
