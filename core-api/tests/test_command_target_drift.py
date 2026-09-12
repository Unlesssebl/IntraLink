import datetime as dt
import uuid
from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.database.db import (
    ActionPolicyRecord,
    AsyncSessionLocal,
    CommandApproval,
    CommandAttempt,
    CommandEvent,
    CommandInbox,
    CommandOutbox,
    CommandRecord,
    init_db,
)
from app.services.command_service import CommandService
from sdk.models import ActionResult, HandlerContext
from handlers.install_printer import InstallPrinterHandler, InstallPrinterInput
from shared.printers import PrinterConfig


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    await init_db()
    async with AsyncSessionLocal() as db:
        for model in (
            ActionPolicyRecord,
            CommandApproval,
            CommandAttempt,
            CommandInbox,
            CommandEvent,
            CommandOutbox,
            CommandRecord,
        ):
            await db.execute(model.__table__.delete())
        await db.commit()


@pytest.mark.asyncio
async def test_command_target_drift_detected_on_approval():
    async with AsyncSessionLocal() as db:
        svc = CommandService(db)
        cmd, _ = await svc.create_record(
            action="install_printer",
            target={"pc_name": "ZTE1000", "printer_name": "HP LaserJet"},
            parameters={"printer_ip": "10.244.1.20"},
            idempotency_key=f"drift-test-{uuid.uuid4()}",
            initiator="operator",
            source="test",
            priority=5,
        )
        cmd.status = "awaiting_approval"
        cmd.plan_hash = "hash123"
        cmd.version = 1
        await db.commit()

        # Approval with matching expected target succeeds
        approved = await svc.approve(
            command_id=cmd.id,
            decision="approve",
            reason="approved by admin",
            operator="operator",
            expected_plan_hash="hash123",
            expected_version=1,
            expected_target={"pc_name": "ZTE1000", "printer_name": "HP LaserJet"},
            approver_roles=frozenset(["system_admin"]),
        )
        assert approved.status in ("queued", "running", "succeeded")

        # Create another command for drift failure test
        cmd2, _ = await svc.create_record(
            action="install_printer",
            target={"pc_name": "ZTE1000", "printer_name": "HP LaserJet"},
            parameters={"printer_ip": "10.244.1.20"},
            idempotency_key=f"drift-test-2-{uuid.uuid4()}",
            initiator="operator",
            source="test",
            priority=5,
        )
        cmd2.status = "awaiting_approval"
        cmd2.plan_hash = "hash123"
        cmd2.version = 1
        await db.commit()

        # Approval with drifted target (e.g. pc_name is ZTE2000 instead of ZTE1000) raises 409
        with pytest.raises(HTTPException) as exc_info:
            await svc.approve(
                command_id=cmd2.id,
                decision="approve",
                reason="approved by admin",
                operator="operator",
                expected_plan_hash="hash123",
                expected_version=1,
                expected_target={"pc_name": "ZTE2000", "printer_name": "HP LaserJet"},
                approver_roles=frozenset(["system_admin"]),
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["detail"] == "target_drift_detected"


@pytest.mark.asyncio
async def test_install_printer_verify_postconditions_and_port_check():
    handler = InstallPrinterHandler()
    ctx = HandlerContext(command_id="cmd-1")
    ctx.state["printer_cfg"] = PrinterConfig(
        model_key="kyocera_m2040dn",
        display_name="Kyocera ECOSYS M2040dn",
        vendor="kyocera",
        driver_name="Kyocera ECOSYS M2040dn KX",
        driver_inf_path=r"\\truenas\Drivers\Kyocera\OEMSETUP.INF",
    )
    ctx.state["port_name"] = "IP_10.244.1.20"
    ctx.state["resolved_ip"] = "10.244.1.20"

    params = InstallPrinterInput(
        pc_name="ZTE1000",
        printer_name="Kyocera M2040dn",
        printer_ip="10.244.1.20",
    )
    initial_res = ActionResult(success=True, message="Install done", log=[])

    # 1. Successful verify: port exists, printer matches
    mock_ps_success = AsyncMock(
        return_value=type(
            "PSResult",
            (),
            {
                "success": True,
                "data": {
                    "Name": "Kyocera M2040dn",
                    "PortName": "IP_10.244.1.20",
                    "DriverName": "Kyocera ECOSYS M2040dn KX",
                    "PortExists": True,
                    "PrinterHostAddress": "10.244.1.20",
                },
            },
        )()
    )
    with patch("handlers.install_printer.run_powershell_safe", new=mock_ps_success):
        verified, msg, is_fail, fail_code = await handler.verify(ctx, params, initial_res)

    assert verified is True
    assert is_fail is False
    assert initial_res.payload["queue_configured"] is True
    assert initial_res.payload["test_job_submitted"] is False
    assert initial_res.payload["physical_print_confirmed"] is False

    # 2. Port mismatch or missing port fails verify
    mock_ps_bad_port = AsyncMock(
        return_value=type(
            "PSResult",
            (),
            {
                "success": True,
                "data": {
                    "Name": "Kyocera M2040dn",
                    "PortName": "IP_10.244.1.20",
                    "DriverName": "Kyocera ECOSYS M2040dn KX",
                    "PortExists": False,
                },
            },
        )()
    )
    with patch("handlers.install_printer.run_powershell_safe", new=mock_ps_bad_port):
        verified, msg, is_fail, fail_code = await handler.verify(ctx, params, initial_res)

    assert verified is False
    assert is_fail is True
    assert fail_code == "printer_port_not_found"
