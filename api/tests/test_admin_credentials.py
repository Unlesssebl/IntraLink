"""Security and persistence checks for the administrator credentials vault."""

import base64
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.core.security import (
    decode_basic_credentials,
    require_admin_intraservice_auth,
)
from api.src.features.admin.schemas import AdCredentialsUpdate, ServiceCredentialsUpdate
from api.src.features.admin.service import AdminCredentialsService
from core.ad.credentials import AD_CREDENTIALS_STATE_KEY
from core.crypto import set_fernet
from core.database.base import Base
from core.database.models import SystemState
from core.intraservice.auth import ServiceAuthBootstrap


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_decode_basic_credentials_preserves_colons_in_password() -> None:
    token = base64.b64encode(b"admin.user:p:a:ss").decode()
    assert decode_basic_credentials(token) == ("admin.user", "p:a:ss")


def test_ad_credentials_require_ldaps() -> None:
    with pytest.raises(ValidationError, match="ldaps_required_for_password_provisioning"):
        AdCredentialsUpdate(
            servers=["dc1.corporate.loc"],
            username="svc.intralink@corporate.loc",
            password="secret",
            port=389,
            use_ssl=False,
        )


@pytest.mark.asyncio
async def test_admin_auth_requires_allowlisted_live_login(monkeypatch: pytest.MonkeyPatch) -> None:
    token = base64.b64encode(b"admin.user:secret").decode()
    monkeypatch.setattr("api.src.core.security.settings.ADMIN_LOGINS", ["admin.user"])
    client = AsyncMock()
    client.verify_credentials.return_value = (token, 17)

    with patch("api.src.core.security.IntraServiceClient", return_value=client):
        identity = await require_admin_intraservice_auth(token)

    assert identity.login == "admin.user"
    assert identity.user_id == 17
    client.verify_credentials.assert_awaited_once_with("admin.user", "secret")


@pytest.mark.asyncio
async def test_admin_auth_rejects_non_admin_before_live_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    token = base64.b64encode(b"operator:secret").decode()
    monkeypatch.setattr("api.src.core.security.settings.ADMIN_LOGINS", ["admin.user"])
    with pytest.raises(HTTPException) as exc_info:
        await require_admin_intraservice_auth(token)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_service_credentials_are_encrypted_and_response_is_secret_free(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cipher = Fernet(Fernet.generate_key())
    set_fernet(cipher)
    monkeypatch.setattr("api.src.features.admin.service.async_session_factory", session_factory)
    redis = AsyncMock()
    redis.exists.return_value = 1
    service = AdminCredentialsService()
    service.client.verify_credentials = AsyncMock(return_value=("c2VydmljZS5ib3Q6c2VjcmV0", 42))

    try:
        async with session_factory() as session:
            response = await service.update(
                session,
                redis,
                ServiceCredentialsUpdate(login="service.bot", password="secret", sync_catalog=False),
            )
        async with session_factory() as session:
            row = await session.scalar(
                select(SystemState).where(SystemState.key == ServiceAuthBootstrap.STATE_KEY_CREDENTIALS)
            )

        assert response.status.configured is True
        assert response.status.login == "service.bot"
        assert response.model_dump().get("password") is None
        assert row is not None
        assert row.state_data["encrypted_token"] != "c2VydmljZS5ib3Q6c2VjcmV0"
        assert cipher.decrypt(row.state_data["encrypted_token"].encode()).decode() == "c2VydmljZS5ib3Q6c2VjcmV0"
    finally:
        set_fernet(None)


@pytest.mark.asyncio
async def test_ad_credentials_are_verified_encrypted_and_response_is_secret_free(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cipher = Fernet(Fernet.generate_key())
    set_fernet(cipher)
    monkeypatch.setattr("api.src.features.admin.service.async_session_factory", session_factory)
    service = AdminCredentialsService()
    verify = AsyncMock(return_value=("dc1.corporate.loc", 372))
    monkeypatch.setattr("api.src.features.admin.service.asyncio.to_thread", verify)

    try:
        async with session_factory() as session:
            response = await service.update_ad_credentials(
                session,
                AdCredentialsUpdate(
                    servers=["dc1.corporate.loc", "dc2.corporate.loc"],
                    username="svc.intralink@corporate.loc",
                    password="domain-secret",
                    port=636,
                    use_ssl=True,
                ),
            )
        async with session_factory() as session:
            row = await session.scalar(select(SystemState).where(SystemState.key == AD_CREDENTIALS_STATE_KEY))

        assert response.status.configured is True
        assert response.status.domain == "corporate.loc"
        assert response.status.ou_count == 372
        assert "domain-secret" not in response.model_dump_json()
        assert row is not None
        assert row.state_data["encrypted_password"] != "domain-secret"
        assert cipher.decrypt(row.state_data["encrypted_password"].encode()).decode() == "domain-secret"
    finally:
        set_fernet(None)
