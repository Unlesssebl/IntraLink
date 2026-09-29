"""Encrypted Active Directory connection settings stored in PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.crypto import decrypt_secret, encrypt_secret
from core.database.models import SystemState
from core.database.session import get_engine, get_session_factory

AD_DOMAIN = "corporate.loc"
AD_USERS_ROOT_OU = "OU=CORPORATE_USERS,DC=corporate,DC=loc"
AD_CREDENTIALS_STATE_KEY = "credentials:active_directory"


@dataclass(frozen=True)
class ActiveDirectorySettings:
    servers: tuple[str, ...]
    username: str
    password: str
    domain: str = AD_DOMAIN
    users_root_ou: str = AD_USERS_ROOT_OU
    port: int = 636
    use_ssl: bool = True
    connect_timeout: float = 2.0


class ActiveDirectoryCredentialVault:
    """Durable encrypted AD credential boundary; plaintext exists only in memory."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory

    async def load(self, session: AsyncSession | None = None) -> ActiveDirectorySettings:
        if session is not None:
            return await self._load_from_session(session)
        if self._session_factory is not None:
            async with self._session_factory() as owned_session:
                return await self._load_from_session(owned_session)
        engine = get_engine()
        try:
            factory = get_session_factory(engine)
            async with factory() as owned_session:
                return await self._load_from_session(owned_session)
        finally:
            await engine.dispose()

    async def _load_from_session(self, session: AsyncSession) -> ActiveDirectorySettings:
        record = await session.scalar(select(SystemState).where(SystemState.key == AD_CREDENTIALS_STATE_KEY))
        data = record.state_data if record and record.state_data else {}
        encrypted_password = data.get("encrypted_password")
        servers = tuple(str(item).strip() for item in data.get("servers", []) if str(item).strip())
        username = str(data.get("username") or "").strip()
        if not encrypted_password or not servers or not username:
            raise RuntimeError("ad_credentials_unavailable")
        return ActiveDirectorySettings(
            servers=servers,
            username=username,
            password=decrypt_secret(encrypted_password),
            port=int(data.get("port") or 636),
            use_ssl=bool(data.get("use_ssl", True)),
            connect_timeout=float(data.get("connect_timeout") or 2.0),
        )

    async def save(
        self,
        session: AsyncSession,
        *,
        servers: Sequence[str],
        username: str,
        password: str,
        port: int,
        use_ssl: bool,
        connect_timeout: float = 2.0,
        verified_server: str | None = None,
        ou_count: int | None = None,
        verified_at: datetime | None = None,
    ) -> None:
        normalized_servers = list(dict.fromkeys(item.strip() for item in servers if item.strip()))
        if not normalized_servers or not username.strip() or not password:
            raise ValueError("ad_credentials_incomplete")
        payload = {
            "servers": normalized_servers,
            "username": username.strip(),
            "encrypted_password": encrypt_secret(password),
            "domain": AD_DOMAIN,
            "users_root_ou": AD_USERS_ROOT_OU,
            "port": port,
            "use_ssl": use_ssl,
            "connect_timeout": connect_timeout,
            "verified_server": verified_server,
            "ou_count": ou_count,
            "verified_at": verified_at.isoformat() if verified_at else None,
        }
        record = await session.scalar(
            select(SystemState).where(SystemState.key == AD_CREDENTIALS_STATE_KEY).with_for_update()
        )
        if record is None:
            session.add(SystemState(key=AD_CREDENTIALS_STATE_KEY, state_data=payload))
        else:
            record.state_data = payload
        await session.flush()
