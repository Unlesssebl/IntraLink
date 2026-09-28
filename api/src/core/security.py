"""Authentication, tokens, and authorization helpers."""

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.src.core.config import settings
from core.intraservice.client import IntraServiceClient

security_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AdminIdentity:
    login: str
    user_id: int
    auth_b64: str


def decode_basic_credentials(auth_b64: str) -> tuple[str, str]:
    """Decode a Basic token without ever logging or returning it in an HTTP response."""
    try:
        decoded = base64.b64decode(auth_b64, validate=True).decode("utf-8")
        login, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Некорректные учетные данные IntraService") from exc
    if not login.strip() or not password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Некорректные учетные данные IntraService")
    return login.strip(), password


async def get_intraservice_auth(
    authorization: Optional[str] = Header(None),
    x_intraservice_auth: Optional[str] = Header(None, alias="X-IntraService-Auth"),
    auth_b64: Optional[str] = Query(None),
) -> Optional[str]:
    """Resolves IntraService Basic Auth credentials from headers, query, or fallback."""
    if auth_b64:
        return auth_b64
    if x_intraservice_auth:
        return x_intraservice_auth
    if authorization and authorization.lower().startswith("basic "):
        return authorization[6:].strip()
    if settings.INTRASERVICE_LOGIN and settings.INTRASERVICE_PASSWORD:
        cred = f"{settings.INTRASERVICE_LOGIN}:{settings.INTRASERVICE_PASSWORD}"
        return base64.b64encode(cred.encode()).decode()
    return None


async def require_admin_intraservice_auth(
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
) -> AdminIdentity:
    """Require a live IntraService login that is explicitly allow-listed as an administrator."""
    if not auth_b64:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Требуется авторизация в IntraService",
            headers={"WWW-Authenticate": "Basic"},
        )
    login, password = decode_basic_credentials(auth_b64)
    allowed = {item.casefold() for item in settings.ADMIN_LOGINS}
    if login.casefold() not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для раздела администрирования")

    client = IntraServiceClient(base_url=settings.INTRASERVICE_URL, verify_ssl=settings.SSL_VERIFY)
    verified_auth, user_id = await client.verify_credentials(login, password)
    if not verified_auth or user_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Учетные данные администратора IntraService недействительны",
            headers={"WWW-Authenticate": "Basic"},
        )
    return AdminIdentity(login=login, user_id=user_id, auth_b64=verified_auth)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"iat": now, "exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user_payload(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Dict[str, Any]:
    """FastAPI dependency to authenticate requests via Bearer JWT."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(credentials.credentials)
