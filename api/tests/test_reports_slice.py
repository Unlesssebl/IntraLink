"""Integration tests for Reports feature slice and Redis tier-caching."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.src.features.reports.service import ReportsService
from api.src.main import app


def test_reports_is_month_closed_logic():
    # 2020 should always be closed
    assert ReportsService.is_month_closed(2020, 1) is True
    # 2099 should always be open
    assert ReportsService.is_month_closed(2099, 12) is False


@pytest.mark.asyncio
async def test_get_load_report_endpoint():
    transport = ASGITransport(app=app)
    with (
        patch("redis.asyncio.Redis.get", new_callable=AsyncMock) as mock_get,
        patch("redis.asyncio.Redis.set", new_callable=AsyncMock),
    ):
        mock_get.return_value = None

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/reports/load?year=2025&month=5")
            assert resp.status_code == 200
            data = resp.json()
            assert data["year"] == 2025
            assert data["month"] == 5
            assert data["is_closed_month"] is True
            assert len(data["engineers"]) >= 2
            assert data["total_tickets"] > 0
            assert data["closed_tickets"] > 0
            assert "Беликов Ален" in data["engineer_load"]
            assert "03. Принтеры и оргтехника" in data["service_load"]
            assert data["avg_resolution_hours"] > 0


@pytest.mark.asyncio
async def test_export_load_report_csv():
    transport = ASGITransport(app=app)
    with (
        patch("redis.asyncio.Redis.get", new_callable=AsyncMock) as mock_get,
        patch("redis.asyncio.Redis.set", new_callable=AsyncMock),
    ):
        mock_get.return_value = None

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/reports/export?year=2025&month=5")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/csv; charset=utf-8"
            assert "Беликов Ален" in resp.text
            assert "ID сотрудника" in resp.text
