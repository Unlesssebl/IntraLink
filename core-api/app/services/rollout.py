"""Fail-closed readiness checks used before routing production traffic."""

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.database.db import AsyncSessionLocal


def build_versions() -> dict[str, str]:
    """Read immutable build metadata; unknown is deliberately not rollout-ready."""
    api_sha = os.getenv("APP_GIT_SHA", "unknown")
    web_sha = "unknown"
    info_path = Path(__file__).resolve().parent.parent / "static" / "admin" / "build-info.json"
    try:
        web_sha = str(json.loads(info_path.read_text(encoding="utf-8")).get("git_sha", "unknown"))
    except (OSError, ValueError):
        pass
    return {"api_sha": api_sha, "web_sha": web_sha}


async def security_audit_table_exists() -> bool:
    """Use portable metadata queries so the check also works in isolated tests."""
    async with AsyncSessionLocal() as session:
        dialect = session.bind.dialect.name if session.bind else ""
        if dialect == "sqlite":
            result = await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='security_audit_log'")
            )
        else:
            result = await session.execute(text("SELECT to_regclass('public.security_audit_log')"))
        return bool(result.scalar_one_or_none())


async def rollout_readiness(expected_sha: str | None = None) -> dict[str, Any]:
    versions = build_versions()
    try:
        audit_ready = await security_audit_table_exists()
    except Exception as exc:
        return {
            "ready": False,
            "versions": versions,
            "security_audit_log": False,
            "checks": ["security_audit_log недоступна"],
            "error": str(exc),
        }

    sha_ready = versions["api_sha"] != "unknown" and versions["api_sha"] == versions["web_sha"]
    if expected_sha:
        sha_ready = sha_ready and versions["api_sha"] == expected_sha
    checks = []
    if not sha_ready:
        checks.append("SHA API/UI не совпадает с ожидаемой версией сборки")
    if not audit_ready:
        checks.append("таблица security_audit_log отсутствует")
    return {
        "ready": not checks,
        "versions": versions,
        "security_audit_log": audit_ready,
        "checks": checks,
    }
