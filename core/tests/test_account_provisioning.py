"""Security and postcondition tests for account provisioning."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ad.password import SecretPassword
from core.ad.provisioning import AccountProvisioningError, AccountProvisioningService
from core.intraservice.auth import ServiceAuthCredentials


def _provisioner_with_verified_ldap():
    ad_pool = MagicMock()
    ad_pool.config.domain = "corporate.loc"
    conn = MagicMock()
    verified = MagicMock()
    verified.sAMAccountName.value = "ivanov.i"
    verified.userAccountControl.value = 512
    verified.pwdLastSet.value = 0

    searches = iter([[], [], [verified]])

    def search_side_effect(*args, **kwargs):
        conn.entries = next(searches)

    conn.search.side_effect = search_side_effect
    conn.result = {"result": 0, "description": "success"}
    ad_pool.connection_scope.return_value.__enter__.return_value = conn

    client = AsyncMock()
    auth = AsyncMock()
    auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="safe-auth-token",
        bot_user_id=42,
        login="service.bot",
    )
    return AccountProvisioningService(ad_pool=ad_pool, client=client, auth_bootstrap=auth), conn, client


@pytest.mark.asyncio
async def test_provisioning_writes_credentials_but_returns_secret_free_receipt():
    provisioner, conn, client = _provisioner_with_verified_ldap()
    known_password = "Known-Secret-93!"

    with patch(
        "core.ad.provisioning.generate_secure_password",
        return_value=SecretPassword(known_password),
    ):
        receipt = await provisioner.provision(
            task_id=501,
            last_name="Иванов",
            first_name="Иван",
            middle_name="",
            department="ИТ",
            title="Инженер",
            phone="",
            company="",
        )

    assert receipt.credentials_written is True
    assert known_password not in repr(receipt)
    update = client.update_task.await_args.kwargs
    assert update["custom_fields"] == {1488: "ivanov.i", 1489: known_password}
    assert conn.modify.call_count == 3


@pytest.mark.asyncio
async def test_provisioning_field_write_failure_is_partial_and_not_retryable():
    provisioner, _conn, client = _provisioner_with_verified_ldap()
    client.update_task.side_effect = RuntimeError("IntraService unavailable")

    with pytest.raises(AccountProvisioningError) as exc_info:
        await provisioner.provision(
            task_id=502,
            last_name="Иванов",
            first_name="Иван",
            middle_name="",
            department="ИТ",
            title="Инженер",
            phone="",
            company="",
        )

    assert exc_info.value.code == "credentials_delivery_failed_after_ad_create"
    assert exc_info.value.ad_object_created is True
    assert "IntraService unavailable" not in str(exc_info.value)


def test_provisioning_checks_every_ldap_step():
    provisioner, conn, _client = _provisioner_with_verified_ldap()

    def fail_password_modify(*args, **kwargs):
        conn.result = {"result": 53, "description": "unwillingToPerform"}

    conn.modify.side_effect = fail_password_modify
    with pytest.raises(AccountProvisioningError) as exc_info:
        provisioner._create_and_verify_sync(
            last_name="Иванов",
            first_name="Иван",
            middle_name="",
            department="ИТ",
            title="Инженер",
            phone="",
            company="",
            password="Secret-93!",
        )

    assert exc_info.value.code == "ldap_set_password_failed"
    assert exc_info.value.ad_object_created is True
