"""Reports business logic and Redis caching strategy according to GEMINI.md."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis

from api.src.core.config import settings
from core.intraservice import IntraServiceClient

from .schemas import (
    EngineerLoadMetricDTO,
    MonthlyLoadReportDTO,
)

logger = logging.getLogger("api.features.reports")

CLOSED_MONTH_TTL = 30 * 86400  # 30 days
CURRENT_MONTH_TTL = 300  # 5 minutes


class ReportsService:
    """Service generating engineer workload and support efficiency reports."""

    def __init__(self, intraservice_client: Optional[IntraServiceClient] = None) -> None:
        self.client = intraservice_client or IntraServiceClient(
            base_url=settings.INTRASERVICE_URL,
            verify_ssl=settings.SSL_VERIFY,
        )

    @staticmethod
    def is_month_closed(year: int, month: int) -> bool:
        now = datetime.now(timezone.utc)
        return (year < now.year) or (year == now.year and month < now.month)

    async def get_monthly_load(
        self,
        year: int,
        month: int,
        redis_client: Optional[aioredis.Redis] = None,
    ) -> MonthlyLoadReportDTO:
        cache_key = f"reports:load:month:{year}_{month:02d}"
        is_closed = self.is_month_closed(year, month)

        # 1. Check Redis cache
        if redis_client:
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    data["cached"] = True
                    if not data.get("engineer_load") and data.get("engineers"):
                        data["engineer_load"] = {e["user_name"]: e["total_closed"] for e in data["engineers"]}
                    if not data.get("closed_tickets") and data.get("engineers"):
                        data["closed_tickets"] = sum(e["total_closed"] for e in data["engineers"])
                    if not data.get("service_load"):
                        data["service_load"] = {
                            "03. Принтеры и оргтехника": 85,
                            "02. Настройка ПО и рабочих мест": 72,
                            "01. Учетные записи и доступ": 60,
                            "09. ЭЦП и банк-клиенты": 45,
                            "04. Сеть и интернет": 40,
                            "05. Directum и B2B": 30,
                            "06. Вопросы по 1С": 23,
                        }
                    return MonthlyLoadReportDTO.model_validate(data)
            except Exception as exc:
                logger.debug(f"Redis report cache read error: {exc}")

        # 2. Build report data (placeholder calculation with IntraService client)
        # In full production, this aggregates tickets by closed status in the given month range
        mock_engineers = [
            EngineerLoadMetricDTO(
                user_id=101,
                user_name="Беликов Ален",
                total_assigned=145,
                total_closed=142,
                avg_resolution_hours=1.8,
                reopened_count=2,
            ),
            EngineerLoadMetricDTO(
                user_id=102,
                user_name="Дежурный инженер 1-й линии",
                total_assigned=210,
                total_closed=198,
                avg_resolution_hours=0.9,
                reopened_count=5,
            ),
        ]

        total_tickets = sum(e.total_assigned for e in mock_engineers)
        closed_tickets = sum(e.total_closed for e in mock_engineers)
        avg_res = (
            round(
                sum(e.avg_resolution_hours * e.total_closed for e in mock_engineers)
                / closed_tickets,
                1,
            )
            if closed_tickets > 0
            else 0.0
        )
        engineer_load = {e.user_name: e.total_closed for e in mock_engineers}
        service_load = {
            "03. Принтеры и оргтехника": 85,
            "02. Настройка ПО и рабочих мест": 72,
            "01. Учетные записи и доступ": 60,
            "09. ЭЦП и банк-клиенты": 45,
            "04. Сеть и интернет": 40,
            "05. Directum и B2B": 30,
            "06. Вопросы по 1С": 23,
        }

        report = MonthlyLoadReportDTO(
            year=year,
            month=month,
            is_closed_month=is_closed,
            total_tickets=total_tickets,
            closed_tickets=closed_tickets,
            avg_resolution_hours=avg_res,
            engineer_load=engineer_load,
            service_load=service_load,
            engineers=mock_engineers,
            cached=False,
        )

        # 3. Cache according to GEMINI.md policy
        if redis_client:
            ttl = CLOSED_MONTH_TTL if is_closed else CURRENT_MONTH_TTL
            try:
                await redis_client.set(cache_key, report.model_dump_json(), ex=ttl)
            except Exception as exc:
                logger.debug(f"Redis report cache write error: {exc}")

        return report

    async def export_csv(
        self,
        year: int,
        month: int,
        redis_client: Optional[aioredis.Redis] = None,
    ) -> str:
        report = await self.get_monthly_load(year=year, month=month, redis_client=redis_client)
        lines = ["ID сотрудника,ФИО инженера,Назначено,Закрыто,Среднее время (ч),Возвраты"]
        for e in report.engineers:
            lines.append(
                f"{e.user_id},{e.user_name},{e.total_assigned},{e.total_closed},{e.avg_resolution_hours},{e.reopened_count}"
            )
        return "\n".join(lines)
