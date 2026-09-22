import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status

from app.routers.deps import get_service_auth_b64, require_permission
from app.services.reports import (
    YearLoadReport,
    export_load_report_csv,
    generate_load_report,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/reports", tags=["Reports"])


@router.get(
    "/load",
    response_model=YearLoadReport,
    dependencies=[Depends(require_permission("triage:read"))],
    summary="Получить динамический отчёт по нагрузке и SLA",
)
async def get_load_report(
    months: int = Query(12, ge=1, le=36, description="Количество месяцев анализа"),
    scope: Literal["all", "line1", "line2"] = Query(
        "all", description="Контур анализа: all (1+2 линия), line1 (только 1-я), line2 (только 2-я)"
    ),
    force_refresh: bool = Query(
        False, description="Умный сброс кэша: обновляет данные текущего месяца"
    ),
    full_history_refresh: bool = Query(
        False, description="Полный принудительный пересчёт всех закрытых месяцев"
    ),
    auth_b64: str = Depends(get_service_auth_b64),
) -> YearLoadReport:
    """
    Возвращает сводные данные по нагрузке технической поддержки, SLA и смежных направлений.
    Исторические месяцы отдаются мгновенно из кэша Redis (2-5 мс).
    """
    return await generate_load_report(
        months_count=months,
        scope=scope,
        auth_b64=auth_b64,
        force_refresh=force_refresh,
        full_history_refresh=full_history_refresh,
    )


@router.get(
    "/export",
    dependencies=[Depends(require_permission("triage:read"))],
    summary="Экспорт отчёта по нагрузке и SLA (CSV/JSON)",
)
async def export_load_report(
    format: Literal["csv", "json"] = Query("csv", description="Формат выгрузки"),
    months: int = Query(12, ge=1, le=36, description="Количество месяцев анализа"),
    scope: Literal["all", "line1", "line2"] = Query("all", description="Контур анализа"),
    force_refresh: bool = Query(False, description="Принудительный пересчёт текущего месяца"),
    full_history_refresh: bool = Query(False, description="Полный пересчёт истории"),
    auth_b64: str = Depends(get_service_auth_b64),
):
    """
    Генерирует и скачивает файл отчёта в формате CSV (Excel UTF-8 BOM с разделителем ;) или JSON.
    """
    report = await generate_load_report(
        months_count=months,
        scope=scope,
        auth_b64=auth_b64,
        force_refresh=force_refresh,
        full_history_refresh=full_history_refresh,
    )

    if format == "csv":
        csv_text = export_load_report_csv(report)
        content = csv_text.encode("utf-8-sig")
        filename = f"support_load_report_{scope}_{date.today().isoformat()}.csv"
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return report
