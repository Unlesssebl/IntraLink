import logging
import os
from typing import Optional

import redis.asyncio as aioredis
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.crypto import decrypt_token, encrypt_token
from core.database.models import SystemState
from core.database.system_state import _get_active_session_factory
from core.intraservice.client import IntraServiceClient

logger = logging.getLogger("core.intraservice.auth")

_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def set_service_auth_session_factory(factory: Optional[async_sessionmaker[AsyncSession]]) -> None:
    """Override session factory for testing environments."""
    global _override_session_factory
    _override_session_factory = factory


class ServiceAuthError(Exception):
    """Raised when service bot authentication fails or credentials are missing."""

    pass


class ServiceAuthCredentials(BaseModel):
    """Authenticated service bot credentials representation."""

    auth_b64: str
    bot_user_id: int
    login: str


class ServiceAuthBootstrap:
    """Manages bootstrap, validation and encrypted caching of worker bot credentials across 3 tiers.

    L1: Fast In-Memory process cache (0 ms)
    L2: Redis Encrypted Vault (1 ms)
    L3: PostgreSQL SystemState durable Vault (resilient to Cold Starts and Redis flushing)
    L4: Environment variables bootstrap & verification against IntraService API
    """

    KEY_SERVICE_AUTH_B64: str = "worker:service_auth_b64"
    KEY_SERVICE_USER_ID: str = "worker:service_user_id"
    KEY_SERVICE_LOGIN: str = "worker:service_login"
    STATE_KEY_CREDENTIALS: str = "credentials:intraservice"

    def __init__(self, session_factory: Optional[async_sessionmaker[AsyncSession]] = None) -> None:
        self._cached_credentials: Optional[ServiceAuthCredentials] = None
        self._session_factory = session_factory

    def clear_cache(self) -> None:
        """Clear memory cache of credentials."""
        self._cached_credentials = None

    def _get_factory(self) -> Optional[async_sessionmaker[AsyncSession]]:
        if self._session_factory is not None:
            return self._session_factory
        if _override_session_factory is not None:
            return _override_session_factory
        return None

    async def bootstrap_auth(
        self,
        client: Optional[IntraServiceClient] = None,
        redis_client: Optional[aioredis.Redis] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        force_refresh: bool = False,
    ) -> ServiceAuthCredentials:
        """Bootstrap service bot credentials through L1 -> L2 -> L3 -> L4 fallback chain."""
        env_login = (
            os.getenv("INTRASERVICE_BOT_LOGIN")
            or os.getenv("INTRASERVICE_SERVICE_LOGIN")
            or os.getenv("INTRASERVICE_LOGIN")
            or ""
        ).strip()
        env_password = (
            os.getenv("INTRASERVICE_BOT_PASSWORD")
            or os.getenv("INTRASERVICE_SERVICE_PASSWORD")
            or os.getenv("INTRASERVICE_PASSWORD")
            or ""
        ).strip()

        # 1. Tier 1: Fast in-memory cache
        if not force_refresh and self._cached_credentials is not None:
            if not env_login or self._cached_credentials.login == env_login:
                return self._cached_credentials

        # 2. Tier 2: Redis cached credentials
        if not force_refresh and redis_client is not None:
            try:
                cached_enc_token = await redis_client.get(self.KEY_SERVICE_AUTH_B64)
                cached_user_id = await redis_client.get(self.KEY_SERVICE_USER_ID)
                cached_login = await redis_client.get(self.KEY_SERVICE_LOGIN) or ""

                if cached_enc_token and cached_user_id:
                    if not env_login or cached_login == env_login:
                        plain_token = decrypt_token(cached_enc_token)
                        user_id_int = int(cached_user_id)
                        creds = ServiceAuthCredentials(
                            auth_b64=plain_token,
                            bot_user_id=user_id_int,
                            login=cached_login,
                        )
                        self._cached_credentials = creds
                        logger.info(
                            "Restored service bot credentials from Redis vault (User ID: %d, Login: '%s')",
                            user_id_int,
                            cached_login,
                        )
                        return creds
            except Exception as exc:
                logger.warning("Failed to restore credentials from Redis: %s. Falling back to L3 DB.", exc)

        # 3. Tier 3: PostgreSQL SystemState Durable Vault
        factory = session_factory or self._get_factory()
        if not force_refresh and factory is not None:
            try:
                async with factory() as db_session:
                    stmt = select(SystemState).where(SystemState.key == self.STATE_KEY_CREDENTIALS)
                    res = await db_session.execute(stmt)
                    row = res.scalar_one_or_none()
                    if row and row.state_data:
                        enc_token = row.state_data.get("encrypted_token")
                        bot_user_id = row.state_data.get("bot_user_id")
                        login = row.state_data.get("login") or ""
                        if enc_token and bot_user_id:
                            if not env_login or login == env_login:
                                plain_token = decrypt_token(enc_token)
                                user_id_int = int(bot_user_id)
                                creds = ServiceAuthCredentials(
                                    auth_b64=plain_token,
                                    bot_user_id=user_id_int,
                                    login=login,
                                )
                                self._cached_credentials = creds
                                # Warm up L2 (Redis)
                                if redis_client is not None:
                                    try:
                                        await redis_client.set(self.KEY_SERVICE_AUTH_B64, enc_token)
                                        await redis_client.set(self.KEY_SERVICE_USER_ID, str(user_id_int))
                                        await redis_client.set(self.KEY_SERVICE_LOGIN, login)
                                    except Exception as exc:
                                        logger.debug("Failed to warm up Redis from L3: %s", exc)
                                logger.info(
                                    "Restored service bot credentials from PostgreSQL L3 Vault (User ID: %d, Login: '%s')",
                                    user_id_int,
                                    login,
                                )
                                return creds
            except Exception as exc:
                logger.debug("PostgreSQL L3 credentials lookup failed: %s. Falling back to env.", exc)

        # 4. Tier 4: Environment bootstrap & live validation against IntraService API
        login = env_login
        password = env_password

        if not login or not password:
            raise ServiceAuthError(
                "Service bot credentials missing across all vaults (L1 Memory, L2 Redis, L3 PostgreSQL). "
                "Set INTRASERVICE_BOT_LOGIN and INTRASERVICE_BOT_PASSWORD in environment or save via credentials vault."
            )

        intraservice = client or IntraServiceClient()
        try:
            auth_b64, bot_user_id = await intraservice.verify_credentials(
                login=login,
                password=password,
            )
        except Exception as exc:
            raise ServiceAuthError(
                f"Network error while verifying bot credentials for '{login}': {exc}"
            ) from exc

        if not auth_b64 or bot_user_id is None:
            raise ServiceAuthError(
                f"Failed to authenticate service bot user '{login}' against IntraService API."
            )

        # Persist across all tiers (L1, L2, L3)
        return await self.save_credentials(
            auth_b64=auth_b64,
            bot_user_id=bot_user_id,
            login=login,
            redis_client=redis_client,
            session_factory=factory,
        )

    async def save_credentials(
        self,
        auth_b64: str,
        bot_user_id: int,
        login: str,
        redis_client: Optional[aioredis.Redis] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    ) -> ServiceAuthCredentials:
        """Atomically encrypt and persist service bot credentials across L1, L2 (Redis) and L3 (PostgreSQL)."""
        encrypted_token = encrypt_token(auth_b64)
        creds = ServiceAuthCredentials(
            auth_b64=auth_b64,
            bot_user_id=bot_user_id,
            login=login,
        )
        self._cached_credentials = creds

        # 1. Persist to Redis (L2)
        if redis_client is not None:
            try:
                await redis_client.set(self.KEY_SERVICE_AUTH_B64, encrypted_token)
                await redis_client.set(self.KEY_SERVICE_USER_ID, str(bot_user_id))
                await redis_client.set(self.KEY_SERVICE_LOGIN, login)
            except Exception as exc:
                logger.warning("Failed to persist service credentials in Redis: %s", exc)

        # 2. Persist to PostgreSQL SystemState (L3)
        factory = session_factory or self._get_factory()
        if factory is not None:
            try:
                async with factory() as db_session:
                    stmt = select(SystemState).where(SystemState.key == self.STATE_KEY_CREDENTIALS)
                    res = await db_session.execute(stmt)
                    row = res.scalar_one_or_none()
                    if row is None:
                        row = SystemState(
                            key=self.STATE_KEY_CREDENTIALS,
                            state_data={
                                "encrypted_token": encrypted_token,
                                "bot_user_id": bot_user_id,
                                "login": login,
                            },
                        )
                        db_session.add(row)
                    else:
                        row.state_data = {
                            "encrypted_token": encrypted_token,
                            "bot_user_id": bot_user_id,
                            "login": login,
                        }
                    await db_session.commit()
                    logger.info("Persisted service bot credentials to PostgreSQL L3 Vault.")
            except Exception as exc:
                logger.warning("Failed to persist service credentials in PostgreSQL L3: %s", exc)

        logger.info(
            "Service bot credentials saved successfully across all tiers (User ID: %d, Login: '%s')",
            bot_user_id,
            login,
        )
        return creds
