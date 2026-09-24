"""Service Auth Bootstrap for IntraLink Autopilot Worker.

Handles credentials retrieval, validation against IntraService API,
Fernet encryption and Redis caching.
"""

import logging
import os
from typing import Optional

import redis.asyncio as aioredis
from pydantic import BaseModel

from core.crypto import decrypt_token, encrypt_token
from core.intraservice.client import IntraServiceClient

logger = logging.getLogger("worker.services.auth")


class ServiceAuthError(Exception):
    """Raised when service bot authentication fails or credentials are missing."""

    pass


class ServiceAuthCredentials(BaseModel):
    """Authenticated service bot credentials representation."""

    auth_b64: str
    bot_user_id: int
    login: str


class ServiceAuthBootstrap:
    """Manages bootstrap, validation and encrypted caching of worker bot credentials."""

    KEY_SERVICE_AUTH_B64: str = "worker:service_auth_b64"
    KEY_SERVICE_USER_ID: str = "worker:service_user_id"
    KEY_SERVICE_LOGIN: str = "worker:service_login"

    def __init__(self) -> None:
        self._cached_credentials: Optional[ServiceAuthCredentials] = None

    def clear_cache(self) -> None:
        """Clear memory cache of credentials."""
        self._cached_credentials = None

    async def bootstrap_auth(
        self,
        client: Optional[IntraServiceClient] = None,
        redis_client: Optional[aioredis.Redis] = None,
        force_refresh: bool = False,
    ) -> ServiceAuthCredentials:
        """Bootstrap service bot credentials.

        Resolution order:
        1. In-memory cache (if not force_refresh).
        2. Encrypted Redis cache (if available and valid).
        3. Environment variables (INTRASERVICE_BOT_LOGIN / INTRASERVICE_BOT_PASSWORD)
           with live validation via IntraService API.
        """
        # 1. Fast in-memory cache
        if not force_refresh and self._cached_credentials is not None:
            return self._cached_credentials

        # 2. Redis cached credentials
        if not force_refresh and redis_client is not None:
            try:
                cached_enc_token = await redis_client.get(self.KEY_SERVICE_AUTH_B64)
                cached_user_id = await redis_client.get(self.KEY_SERVICE_USER_ID)
                cached_login = await redis_client.get(self.KEY_SERVICE_LOGIN) or ""

                if cached_enc_token and cached_user_id:
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
                logger.warning("Failed to restore credentials from Redis: %s. Falling back to env.", exc)

        # 3. Environment bootstrap & live validation
        login = (
            os.getenv("INTRASERVICE_BOT_LOGIN")
            or os.getenv("INTRASERVICE_SERVICE_LOGIN")
            or ""
        ).strip()
        password = (
            os.getenv("INTRASERVICE_BOT_PASSWORD")
            or os.getenv("INTRASERVICE_SERVICE_PASSWORD")
            or ""
        ).strip()

        if not login or not password:
            raise ServiceAuthError(
                "Service bot credentials missing. Set INTRASERVICE_BOT_LOGIN and "
                "INTRASERVICE_BOT_PASSWORD in environment."
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

        creds = ServiceAuthCredentials(
            auth_b64=auth_b64,
            bot_user_id=bot_user_id,
            login=login,
        )
        self._cached_credentials = creds

        # Persist encrypted credentials to Redis vault
        if redis_client is not None:
            try:
                encrypted_token = encrypt_token(auth_b64)
                await redis_client.set(self.KEY_SERVICE_AUTH_B64, encrypted_token)
                await redis_client.set(self.KEY_SERVICE_USER_ID, str(bot_user_id))
                await redis_client.set(self.KEY_SERVICE_LOGIN, login)
            except Exception as exc:
                logger.warning("Failed to persist service credentials in Redis: %s", exc)

        logger.info(
            "Service bot authenticated successfully against IntraService (User ID: %d, Login: '%s')",
            bot_user_id,
            login,
        )
        return creds
