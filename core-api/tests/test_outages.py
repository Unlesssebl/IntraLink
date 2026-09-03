import pytest
from unittest.mock import AsyncMock, patch
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.services.outage_detector import (
    OutageDetector,
    cosine_similarity,
    has_outage_triggers,
)

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(v1, v2) - 1.0) < 1e-5

    v3 = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(v1, v3) - 0.0) < 1e-5

    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity(None, None) == 0.0


def test_has_outage_triggers():
    assert has_outage_triggers("У всех упал интернет на 3 этаже") is True
    assert has_outage_triggers("1с вылетает при запуске отчета") is True
    assert has_outage_triggers("Прошу выдать картридж для принтера") is False


@pytest.mark.asyncio
async def test_detect_outages_clustering():
    mock_tickets = [
        {
            "Id": 101,
            "Name": "1С Бухгалтерия не открывается",
            "Description": "Ошибка подключения к серверу у всех сотрудников",
            "ServiceId": 12,
            "ServiceName": "1С:Предприятие",
        },
        {
            "Id": 102,
            "Name": "Сбой 1С",
            "Description": "База 1С зависла и вылетает",
            "ServiceId": 12,
            "ServiceName": "1С:Предприятие",
        },
        {
            "Id": 103,
            "Name": "Не работает 1С:Бухгалтерия",
            "Description": "Массово пропал доступ",
            "ServiceId": 12,
            "ServiceName": "1С:Предприятие",
        },
        {
            "Id": 104,
            "Name": "Замена клавиатуры",
            "Description": "Залипает клавиша пробел",
            "ServiceId": 5,
            "ServiceName": "Оборудование",
        },
    ]

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.sadd = AsyncMock(return_value=1)
    mock_redis.publish = AsyncMock(return_value=1)
    mock_redis.smembers = AsyncMock(return_value=[b"outage:12:101"])

    stored_data = {
        "id": "outage:12:101",
        "title": "Массовый инцидент: 1С:Предприятие (3 заявок)",
        "service_id": 12,
        "service_name": "1С:Предприятие",
        "severity": "critical",
        "status": "active",
        "master_ticket_id": 101,
        "ticket_ids": [101, 102, 103],
    }
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps(stored_data).encode("utf-8"))

    with patch("app.services.outage_detector.get_redis_client", return_value=mock_redis):
        with patch.object(
            OutageDetector,
            "get_ticket_embedding",
            side_effect=[
                [0.9, 0.1, 0.1],
                [0.88, 0.12, 0.09],
                [0.89, 0.11, 0.1],
                [0.1, 0.9, 0.0],
            ],
        ):
            outages = await OutageDetector.detect_outages(mock_tickets)
            assert len(outages) >= 1
            assert outages[0]["master_ticket_id"] == 101
            assert 101 in outages[0]["ticket_ids"]
            assert 102 in outages[0]["ticket_ids"]
            assert 103 in outages[0]["ticket_ids"]
            assert 104 not in outages[0]["ticket_ids"]


@pytest.mark.asyncio
async def test_outages_api_endpoints():
    mock_redis = AsyncMock()
    stored_data = {
        "id": "outage:12:101",
        "title": "Массовый инцидент: 1С:Предприятие (3 заявок)",
        "service_id": 12,
        "service_name": "1С:Предприятие",
        "severity": "critical",
        "status": "active",
        "master_ticket_id": 101,
        "ticket_ids": [101, 102, 103],
    }
    import json
    mock_redis.smembers = AsyncMock(return_value=[b"outage:12:101"])
    mock_redis.get = AsyncMock(return_value=json.dumps(stored_data).encode("utf-8"))
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.srem = AsyncMock(return_value=1)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.publish = AsyncMock(return_value=1)

    with patch("app.services.outage_detector.get_redis_client", return_value=mock_redis):
        with patch("app.routers.outages.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # 1. GET /api/v1/outages/active
                res = await client.get("/api/v1/outages/active", headers=HEADERS)
                assert res.status_code == 200
                data = res.json()
                assert data["total"] == 1
                assert data["outages"][0]["id"] == "outage:12:101"

                # 2. GET /api/v1/outages/outage:12:101
                res_detail = await client.get("/api/v1/outages/outage:12:101", headers=HEADERS)
                assert res_detail.status_code == 200
                assert res_detail.json()["master_ticket_id"] == 101

                # 3. POST /api/v1/outages/outage:12:101/resolve
                res_resolve = await client.post("/api/v1/outages/outage:12:101/resolve", headers=HEADERS)
                assert res_resolve.status_code == 200
                assert res_resolve.json()["status"] == "success"
