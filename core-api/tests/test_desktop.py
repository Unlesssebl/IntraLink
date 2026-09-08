import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.routers.desktop import ClaimLaunchRequest, CreateLaunchRequest, DesktopClient, _clean_host, claim_launch, create_launch


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Db:
    def __init__(self):
        self.log = None

    def add(self, log):
        self.log = log

    async def commit(self):
        return None

    async def refresh(self, _log):
        return None

    async def execute(self, _stmt):
        return _Result(self.log)


def test_desktop_client_allowlist():
    assert DesktopClient("litemanager") is DesktopClient.LITEMANAGER
    with pytest.raises(ValueError):
        DesktopClient("powershell")


def test_desktop_host_normalization_rejects_injection():
    assert _clean_host("ntemw0144") == "NTEMW0144"
    assert _clean_host("10.244.1.25") == "10.244.1.25"
    with pytest.raises(HTTPException):
        _clean_host("PC-1 & powershell.exe")


@pytest.mark.asyncio
async def test_launch_token_is_claimed_once():
    db = _Db()
    operator = SimpleNamespace(username="operator", auth_b64="encrypted")
    task = {"_field_meta": {"pc_name": "NTEMW0144"}}
    with patch("app.routers.desktop.intraservice.get_single_task", new=AsyncMock(return_value=task)):
        issued = await create_launch(CreateLaunchRequest(task_id=145001, host="NTEMW0144", client="litemanager"), db, operator)

    token = re.search(r"token=([^&]+)", issued["deep_link"]).group(1)
    claim = await claim_launch(ClaimLaunchRequest(token=token), db)
    assert claim["host"] == "NTEMW0144"
    assert claim["client"] == "litemanager"
    assert db.log.status == "claimed"

    with pytest.raises(HTTPException) as repeated:
        await claim_launch(ClaimLaunchRequest(token=token), db)
    assert repeated.value.status_code == 410


@pytest.mark.asyncio
async def test_launch_rejects_host_not_from_ticket():
    db = _Db()
    operator = SimpleNamespace(username="operator", auth_b64="encrypted")
    task = {"_field_meta": {"pc_name": "NTEMW0144"}}
    with patch("app.routers.desktop.intraservice.get_single_task", new=AsyncMock(return_value=task)):
        with pytest.raises(HTTPException) as mismatch:
            await create_launch(CreateLaunchRequest(task_id=145001, host="KZM0001", client="rdp"), db, operator)
    assert mismatch.value.status_code == 422
