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

    organization_ou = MagicMock()
    organization_ou.ou.value = "АО Тест"
    organization_ou.distinguishedName.value = "OU=АО Тест,OU=CORPORATE_USERS,DC=corporate,DC=loc"
    target_ou = MagicMock()
    target_ou.ou.value = "ИТ"
    target_ou.distinguishedName.value = "OU=ИТ,OU=АО Тест,OU=CORPORATE_USERS,DC=corporate,DC=loc"

    searches = iter([[organization_ou, target_ou], [], [], [verified]])

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

    task_mock = MagicMock()
    task_mock.custom_fields = {1439: "ivanov.i", 1440: known_password}
    client.get_task.return_value = task_mock

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
    assert receipt.delivery_receipt_id is not None
    assert known_password not in repr(receipt)
    update = client.update_task.await_args.kwargs
    assert update["custom_fields"] == {1439: "ivanov.i", 1440: known_password}
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

    assert exc_info.value.code == "credentials_delivery_unknown_outcome"
    assert exc_info.value.ad_object_created is True
    assert exc_info.value.retryable is False


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


@pytest.mark.asyncio
async def test_preflight_succeeds_when_user_dn_raises_ldap_no_such_object():
    from ldap3.core.exceptions import LDAPNoSuchObjectResult

    provisioner, conn, _client = _provisioner_with_verified_ldap()

    target_ou = MagicMock()
    target_ou.ou.value = "ИТ"
    target_ou.distinguishedName.value = "OU=ИТ,OU=CORPORATE_USERS,DC=corporate,DC=loc"

    def custom_search(*args, **kwargs):
        search_base = kwargs.get("search_base") or (args[0] if args else "")
        if "CN=Иванов Иван" in search_base:
            raise LDAPNoSuchObjectResult()
        if "OU=ИТ" in search_base:
            conn.entries = [target_ou]
            return
        conn.entries = []

    conn.search.side_effect = custom_search

    preview = await provisioner.preflight(
        last_name="Иванов",
        first_name="Иван",
        middle_name="",
        department="ИТ",
        company="",
        target_ou_dn="OU=ИТ,OU=CORPORATE_USERS,DC=corporate,DC=loc",
    )

    assert preview["target_ou"] == "OU=ИТ,OU=CORPORATE_USERS,DC=corporate,DC=loc"
    assert preview["preview_sam_account_name"] == "ivanov.i"
    assert preview["preview_upn"] == "ivanov.i@corporate.loc"


@pytest.mark.asyncio
async def test_preflight_raises_when_ad_object_already_exists():
    provisioner, conn, _client = _provisioner_with_verified_ldap()

    existing_user = MagicMock()
    existing_user.distinguishedName.value = "CN=Иванов Иван,OU=ИТ,OU=CORPORATE_USERS,DC=corporate,DC=loc"
    target_ou = MagicMock()
    target_ou.ou.value = "ИТ"
    target_ou.distinguishedName.value = "OU=ИТ,OU=CORPORATE_USERS,DC=corporate,DC=loc"

    def custom_search(*args, **kwargs):
        search_base = kwargs.get("search_base") or (args[0] if args else "")
        if "CN=Иванов Иван" in search_base:
            conn.entries = [existing_user]
            return
        if "OU=ИТ" in search_base:
            conn.entries = [target_ou]
            return
        conn.entries = []

    conn.search.side_effect = custom_search

    with pytest.raises(AccountProvisioningError) as exc_info:
        await provisioner.preflight(
            last_name="Иванов",
            first_name="Иван",
            middle_name="",
            department="ИТ",
            company="",
            target_ou_dn="OU=ИТ,OU=CORPORATE_USERS,DC=corporate,DC=loc",
        )

    assert exc_info.value.code == "ad_object_already_exists"


@pytest.mark.asyncio
async def test_preflight_raises_when_selected_ou_not_found():
    from ldap3.core.exceptions import LDAPNoSuchObjectResult

    provisioner, conn, _client = _provisioner_with_verified_ldap()

    def custom_search(*args, **kwargs):
        search_base = kwargs.get("search_base") or (args[0] if args else "")
        if "OU=Несуществующий" in search_base:
            raise LDAPNoSuchObjectResult()
        conn.entries = []

    conn.search.side_effect = custom_search

    with pytest.raises(AccountProvisioningError) as exc_info:
        await provisioner.preflight(
            last_name="Иванов",
            first_name="Иван",
            middle_name="",
            department="ИТ",
            company="",
            target_ou_dn="OU=Несуществующий,OU=CORPORATE_USERS,DC=corporate,DC=loc",
        )

    assert exc_info.value.code == "ad_target_ou_not_found"

