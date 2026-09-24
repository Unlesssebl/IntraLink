"""IntraLink API Application Entrypoint (Vertical Slice Architecture)."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.src.core.config import settings
from api.src.core.db import check_db_health, dispose_db
from api.src.core.redis import check_redis_health, close_redis
from api.src.features.autopilot.router import router as autopilot_router
from api.src.features.diagnostics.router import router as diagnostics_router
from api.src.features.knowledge_base.router import router as kb_router
from api.src.features.reports.router import router as reports_router

# Vertical feature slices
from api.src.features.tickets.router import router as tickets_router
from api.src.features.tickets.router import tasks_router
from api.src.features.triage.router import router as triage_router
from core.intraservice.client import IntraServiceClient
from core.intraservice.exceptions import (
    IntraServiceAuthError,
    IntraServiceError,
    IntraServiceNotFoundError,
)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("intralink-api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle manager."""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION} [{settings.APP_ENV}]")
    try:
        import core.database.models  # noqa: F401
        from api.src.core.db import engine
        from core.database.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database schema verified (Base.metadata.create_all)")
    except Exception as exc:
        logger.warning(f"Database schema auto-creation check: {exc}")
    yield
    logger.info("Shutting down IntraLink API resources...")
    await dispose_db()
    await close_redis()
    logger.info("IntraLink API shutdown complete.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="IntraLink v2 Backend powered by Vertical Slice Architecture & LiteLLM Gateway",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["System"])
@app.get("/api/v2/health", tags=["System"])
async def health_check() -> dict:
    """Liveness probe: verifies the API process is alive."""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready", tags=["System"])
@app.get("/api/v2/ready", tags=["System"])
async def readiness_check() -> JSONResponse:
    """Readiness probe: verifies backing infrastructure (PostgreSQL & Redis)."""
    db_ok = await check_db_health()
    redis_ok = await check_redis_health()

    payload = {
        "status": "ready" if (db_ok and redis_ok) else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "redis": "connected" if redis_ok else "unreachable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    status_code = status.HTTP_200_OK if (db_ok and redis_ok) else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=payload)


@app.exception_handler(IntraServiceError)
async def intraservice_error_handler(_request: Request, exc: IntraServiceError) -> JSONResponse:
    """Handles external IntraService communication errors cleanly."""
    logger.error(f"IntraService communication error: {exc}")
    if isinstance(exc, IntraServiceAuthError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "IntraService authentication failed or token expired."},
            headers={"WWW-Authenticate": "Basic"},
        )
    if isinstance(exc, IntraServiceNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": str(exc)},
    )


class LoginRequest(BaseModel):
    login: str
    password: str


@app.post("/api/v2/auth/login", tags=["Authentication"])
async def login(payload: LoginRequest) -> dict:
    """Authenticates against IntraService API and returns Basic Auth token."""
    client = IntraServiceClient(
        base_url=settings.INTRASERVICE_URL,
        verify_ssl=settings.SSL_VERIFY,
    )
    auth_b64, user_id = await client.verify_credentials(payload.login.strip(), payload.password.strip())
    if not auth_b64:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль в IntraService",
        )
    return {
        "status": "ok",
        "auth_b64": auth_b64,
        "user_id": user_id,
        "login": payload.login.strip(),
    }


# Mount Vertical Feature Slices (/api/v2/...)
app.include_router(tickets_router, prefix="/api/v2")
app.include_router(tasks_router, prefix="/api/v2")
app.include_router(triage_router, prefix="/api/v2")
app.include_router(kb_router, prefix="/api/v2")
app.include_router(diagnostics_router, prefix="/api/v2")
app.include_router(reports_router, prefix="/api/v2")
app.include_router(autopilot_router, prefix="/api/v2")
