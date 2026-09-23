"""Reports feature router."""

from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Response

from api.src.core.redis import get_redis

from .schemas import MonthlyLoadReportDTO
from .service import ReportsService

router = APIRouter(prefix="/reports", tags=["Reports"])


def get_reports_service() -> ReportsService:
    return ReportsService()


@router.get("/load", response_model=MonthlyLoadReportDTO)
async def get_load_report(
    year: int = Query(default=datetime.now(timezone.utc).year, ge=2020, le=2100),
    month: int = Query(default=datetime.now(timezone.utc).month, ge=1, le=12),
    service: ReportsService = Depends(get_reports_service),
    redis: aioredis.Redis = Depends(get_redis),
) -> MonthlyLoadReportDTO:
    """Retrieve support team workload analytics with Redis tier-caching (GEMINI.md)."""
    return await service.get_monthly_load(year=year, month=month, redis_client=redis)


@router.get("/export")
async def export_load_report(
    year: int = Query(default=datetime.now(timezone.utc).year, ge=2020, le=2100),
    month: int = Query(default=datetime.now(timezone.utc).month, ge=1, le=12),
    service: ReportsService = Depends(get_reports_service),
    redis: aioredis.Redis = Depends(get_redis),
) -> Response:
    """Export monthly workload report in CSV format."""
    csv_content = await service.export_csv(year=year, month=month, redis_client=redis)
    filename = f"report_load_{year}_{month:02d}.csv"
    return Response(
        content=csv_content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
