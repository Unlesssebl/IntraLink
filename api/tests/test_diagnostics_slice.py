"""Integration tests for Diagnostics feature slice."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.src.main import app


@pytest.mark.asyncio
async def test_diagnose_host_endpoint():
    transport = ASGITransport(app=app)
    with (
        patch("api.src.features.diagnostics.service.resolve_dns_fast", new_callable=AsyncMock) as mock_dns,
        patch("api.src.features.diagnostics.service.fast_ping", new_callable=AsyncMock) as mock_ping,
        patch("api.src.features.diagnostics.service.probe_diagnostic_ports", new_callable=AsyncMock) as mock_ports,
        patch("redis.asyncio.Redis.get", new_callable=AsyncMock) as mock_rget,
        patch("redis.asyncio.Redis.set", new_callable=AsyncMock),
    ):
        mock_dns.return_value = "10.244.1.50"
        mock_ping.return_value = {"host": "10.244.1.50", "is_online": True, "avg_rtt": "2ms"}
        mock_ports.return_value = {"smb_445": True, "winrm_5985": True}
        mock_rget.return_value = None

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/diagnostics/host/PC-FIN-01")
            assert resp.status_code == 200
            data = resp.json()
            assert data["hostname"] == "PC-FIN-01"
            assert data["is_online"] is True
            assert data["ip_address"] == "10.244.1.50"
            assert data["ports"]["smb_445"] is True
            assert data["ports"]["winrm_5985"] is True


@pytest.mark.asyncio
async def test_probe_port_endpoint():
    transport = ASGITransport(app=app)
    with patch("api.src.features.diagnostics.service.probe_tcp_port", new_callable=AsyncMock) as mock_probe:
        mock_probe.return_value = True
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v2/diagnostics/probe", json={"host": "127.0.0.1", "port": 5985})
            assert resp.status_code == 200
            data = resp.json()
            assert data["host"] == "127.0.0.1"
            assert data["port"] == 5985
            assert data["is_open"] is True
