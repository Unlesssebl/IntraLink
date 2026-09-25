"""Tests for FastSocketProbe non-blocking network probe."""

import socket
import pytest

from core.diagnostic.ports import FastSocketProbe, FastProbeResult


@pytest.mark.asyncio
async def test_fast_socket_probe_open_port():
    """Verify probe detects an actively listening local port within 1.5s."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    try:
        probe = FastSocketProbe(default_timeout_sec=1.5)
        result = await probe.probe(host="127.0.0.1", ports=[port])
        assert isinstance(result, FastProbeResult)
        assert result.is_online is True
        assert result.ports.get(port) is True
        assert result.rtt_ms is not None and result.rtt_ms >= 0.0
        assert result.error is None
    finally:
        srv.close()


@pytest.mark.asyncio
async def test_fast_socket_probe_closed_port():
    """Verify probe cleanly detects a closed port without unhandled exceptions."""
    # Find an unused closed port
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    probe = FastSocketProbe(default_timeout_sec=1.0)
    result = await probe.probe(host="127.0.0.1", ports=[port])
    assert result.is_online is False
    assert result.ports.get(port) is False


@pytest.mark.asyncio
async def test_fast_socket_probe_unresolvable_host():
    """Verify probe handles empty or missing host gracefully."""
    probe = FastSocketProbe(default_timeout_sec=1.0)
    result = await probe.probe(host="", ports=[80])
    assert result.is_online is False
    assert result.error is not None
