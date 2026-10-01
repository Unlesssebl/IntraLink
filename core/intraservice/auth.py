import logging
import os
from typing import Optional

import redis.asyncio as aioredis
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.crypto import decrypt_token, encrypt_token
from core.database.models import SystemState
from core.intraservice.client import IntraServiceClient

logger = logging.getLogger("core.intraservice.auth")

_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def set_service_auth_session_factory(factory: Optional[async_sessionmaker[AsyncSession]]) -> None:
    """Override session factory for testing environments."""
    global _override_session_factory
    _override_session_factory = factory


def _get_active_session_factory() -> Optional[async_sessionmaker[AsyncSession]]:
    if _override_session_factory is not None:
        return _override_session_factory
    try:
        from core.database.session import get_engine, get_session_factory
        return get_session_factory(get_engine())
    except Exception:
        return None


class ServiceAuthError(Exception):
    """Raised when service bot authentication fails or credentials are missing."""

    pass


class ServiceAuthCredentials(BaseModel):
    """Authenticated service bot credentials representation."""

    auth_b64: str
    bot_user_id: int
    login: str
    display_name: str = ""


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
    KEY_SERVICE_DISPLAY_NAME: str = "worker:service_display_name"
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
        return _get_active_session_factory()

    async def _backfill_display_name(
        self,
        credentials: ServiceAuthCredentials,
        *,
        client: Optional[IntraServiceClient],
        redis_client: Optional[aioredis.Redis],
        session_factory: Optional[async_sessionmaker[AsyncSession]],
    ) -> ServiceAuthCredentials:
        """Enrich legacy cached credentials without re-authenticating the bot."""
        if credentials.display_name or client is None:
            return credentials
        try:
            current_user = await client.get_current_user_info(credentials.auth_b64)
            if not isinstance(current_user, dict):
                return credentials
            current_user_id = current_user.get("Id", current_user.get("id"))
            if current_user_id is None or int(current_user_id) != credentials.bot_user_id:
                logger.warning(
                    "Ignored service bot display name from mismatched current user (expected ID %d)",
                    credentials.bot_user_id,
                )
                return credentials
            display_name = str(current_user.get("Name") or current_user.get("name") or "").strip()
            if not display_name:
                return credentials
            logger.info("Backfilled service bot display name for user ID %d", credentials.bot_user_id)
            return await self.save_credentials(
                auth_b64=credentials.auth_b64,
                bot_user_id=credentials.bot_user_id,
                login=credentials.login,
                display_name=display_name,
                redis_client=redis_client,
                session_factory=session_factory,
            )
        except Exception:
            logger.debug("Could not backfill service bot display name", exc_info=True)
            return credentials

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

        r_client = redis_client
        if not force_refresh and r_client is None:
            try:
                from core.redis_client import get_redis_client
                r_client = get_redis_client()
            except Exception:
                r_client = None
        factory = session_factory or self._get_factory()

        # 1. Tier 1: Fast in-memory cache
        if not force_refresh and self._cached_credentials is not None:
            if not env_login or self._cached_credentials.login == env_login:
                return await self._backfill_display_name(
                    self._cached_credentials,
                    client=client,
                    redis_client=r_client,
                    session_factory=factory,
                )

        # 2. Tier 2: Redis cached credentials
        if not force_refresh and r_client is not None:
            try:
                cached_enc_token = await r_client.get(self.KEY_SERVICE_AUTH_B64)
                cached_user_id = await r_client.get(self.KEY_SERVICE_USER_ID)
                cached_login = await r_client.get(self.KEY_SERVICE_LOGIN) or ""
                cached_display_name = await r_client.get(self.KEY_SERVICE_DISPLAY_NAME) or ""

                if cached_enc_token and cached_user_id:
                    if not env_login or cached_login == env_login:
                        plain_token = decrypt_token(cached_enc_token)
                        if not plain_token or plain_token.startswith("gAAAAA"):
                            logger.warning("Decrypted Redis service token is invalid or corrupted. Evicting from Redis.")
                            await r_client.delete(
                                self.KEY_SERVICE_AUTH_B64,
                                self.KEY_SERVICE_USER_ID,
                                self.KEY_SERVICE_LOGIN,
                                self.KEY_SERVICE_DISPLAY_NAME,
                            )
                        else:
                            user_id_int = int(cached_user_id)
                            creds = ServiceAuthCredentials(
                                auth_b64=plain_token,
                                bot_user_id=user_id_int,
                                login=cached_login,
                                display_name=str(cached_display_name),
                            )
                            self._cached_credentials = creds
                            logger.info(
                                "Restored service bot credentials from Redis vault (User ID: %d, Login: '%s')",
                                user_id_int,
                                cached_login,
                            )
                            return await self._backfill_display_name(
                                creds,
                                client=client,
                                redis_client=r_client,
                                session_factory=factory,
                            )
            except Exception as exc:
                logger.warning("Failed to restore credentials from Redis: %s. Falling back to L3 DB.", exc)

        # 3. Tier 3: PostgreSQL SystemState Durable Vault
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
                                if not plain_token or plain_token.startswith("gAAAAA"):
                                    logger.warning(
                                        "PostgreSQL L3 credentials contain corrupted token (key mismatch). Falling back."
                                    )
                                else:
                                    user_id_int = int(bot_user_id)
                                    creds = ServiceAuthCredentials(
                                        auth_b64=plain_token,
                                        bot_user_id=user_id_int,
                                        login=login,
                                        display_name=str(row.state_data.get("display_name") or ""),
                                    )
                                    self._cached_credentials = creds
                                    # Warm up L2 (Redis)
                                    if r_client is not None:
                                        try:
                                            await r_client.set(self.KEY_SERVICE_AUTH_B64, enc_token)
                                            await r_client.set(self.KEY_SERVICE_USER_ID, str(user_id_int))
                                            await r_client.set(self.KEY_SERVICE_LOGIN, login)
                                            await r_client.set(
                                                self.KEY_SERVICE_DISPLAY_NAME,
                                                str(row.state_data.get("display_name") or ""),
                                            )
                                        except Exception as exc:
                                            logger.debug("Failed to warm up Redis from L3: %s", exc)
                                    logger.info(
                                        "Restored service bot credentials from PostgreSQL L3 Vault (User ID: %d, Login: '%s')",
                                        user_id_int,
                                        login,
                                    )
                                    return await self._backfill_display_name(
                                        creds,
                                        client=client,
                                        redis_client=r_client,
                                        session_factory=factory,
                                    )
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
        display_name = ""
        try:
            current_user = await intraservice.get_current_user_info(auth_b64)
            display_name = str(current_user.get("Name") or "") if isinstance(current_user, dict) else ""
        except Exception:
            logger.debug("Could not resolve service bot display name", exc_info=True)
        return await self.save_credentials(
            auth_b64=auth_b64,
            bot_user_id=bot_user_id,
            login=login,
            display_name=display_name,
            redis_client=r_client,
            session_factory=factory,
        )

    async def save_credentials(
        self,
        auth_b64: str,
        bot_user_id: int,
        login: str,
        display_name: str = "",
        redis_client: Optional[aioredis.Redis] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    ) -> ServiceAuthCredentials:
        """Atomically encrypt and persist service bot credentials across L1, L2 (Redis) and L3 (PostgreSQL)."""
        encrypted_token = encrypt_token(auth_b64)
        creds = ServiceAuthCredentials(
            auth_b64=auth_b64,
            bot_user_id=bot_user_id,
            login=login,
            display_name=display_name,
        )
        self._cached_credentials = creds

        # 1. Persist to Redis (L2)
        if redis_client is not None:
            try:
                await redis_client.set(self.KEY_SERVICE_AUTH_B64, encrypted_token)
                await redis_client.set(self.KEY_SERVICE_USER_ID, str(bot_user_id))
                await redis_client.set(self.KEY_SERVICE_LOGIN, login)
                await redis_client.set(self.KEY_SERVICE_DISPLAY_NAME, display_name)
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
                                "display_name": display_name,
                            },
                        )
                        db_session.add(row)
                    else:
                        row.state_data = {
                            "encrypted_token": encrypted_token,
                            "bot_user_id": bot_user_id,
                            "login": login,
                            "display_name": display_name,
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
