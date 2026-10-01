import pytest
from httpx import ASGITransport, AsyncClient

from api.src.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "2.0.0"
        assert "timestamp" in data


def test_legacy_operator_gates_are_not_exposed_in_openapi():
    paths = app.openapi()["paths"]

    assert "/api/v2/internal/autopilot/runs/{run_id}/advance" in paths
    assert "/api/v2/internal/autopilot/metrics" in paths
    assert not any("execution-policies" in path for path in paths)
    assert not any("automation/workflows" in path for path in paths)
    assert not any(path.endswith("/approve") for path in paths)
    assert not any(path.endswith("/reject") for path in paths)
    assert not any(path.endswith("/manual-takeover") for path in paths)
