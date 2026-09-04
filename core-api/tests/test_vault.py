"""
Тесты для единого хранилища секретов (SSOT Credentials Vault).
Проверяет шифрование Fernet, запись в PostgreSQL, синхронизацию с Redis и авто-прогрев.
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.db import Base
from app.services import vault
from app.services.crypto import decrypt_token


@pytest_asyncio.fixture
async def test_db():
    """Тестовая SQLite in-memory сессия базы данных с активным ключом Fernet."""
    from cryptography.fernet import Fernet
    import app.services.crypto as crypto_module

    old_fernet = crypto_module._fernet
    crypto_module._fernet = Fernet(Fernet.generate_key())

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    crypto_module._fernet = old_fernet


@pytest.mark.asyncio
@patch("app.services.vault.get_redis_client")
async def test_vault_save_and_sync_service_account(mock_redis_factory, test_db: AsyncSession):
    """
    Проверяет сохранение сервисного аккаунта IntraService в PostgreSQL
    и автоматическую запись зашифрованного токена в Redis.
    """
    mock_redis = AsyncMock()
    mock_redis_factory.return_value = mock_redis

    res = await vault.save_service_account_credentials(
        test_db,
        login="svc_intraservice",
        password="SuperSecretPassword123!",
        base_url="https://helpdesk.corporate.loc",
    )

    assert res["status"] == "success"
    assert res["login"] == "svc_intraservice"
    assert res["is_password_set"] is True

    # Проверка сохранения в PostgreSQL
    raw = await vault.get_raw_setting(test_db, vault.KEY_SERVICE_ACCOUNT)
    assert raw is not None
    assert raw["login"] == "svc_intraservice"
    assert raw["encrypted_password"] != "SuperSecretPassword123!"
    assert decrypt_token(raw["encrypted_password"]) == "SuperSecretPassword123!"

    # Проверка вызова Redis (worker:service_auth_b64)
    mock_redis.set.assert_awaited()
    redis_call_args = mock_redis.set.call_args[0]
    assert redis_call_args[0] == vault.REDIS_KEY_SERVICE_AUTH


@pytest.mark.asyncio
@patch("app.services.vault.get_redis_client")
async def test_vault_save_and_sync_domain_credentials(mock_redis_factory, test_db: AsyncSession):
    """
    Проверяет сохранение единой доменной учетной записи (WinRM + LDAPS) в PostgreSQL
    и синхронизацию в Redis (worker:domain_auth).
    """
    mock_redis = AsyncMock()
    mock_redis_factory.return_value = mock_redis

    res = await vault.save_domain_credentials(
        test_db,
        username="svc_intralink@corporate.loc",
        password="DomainPassword999!",
        domain="corporate.loc",
        dc_host="dc01.corporate.loc",
        ldaps_port=636,
    )

    assert res["status"] == "success"
    assert res["username"] == "svc_intralink@corporate.loc"
    assert res["domain"] == "corporate.loc"
    assert res["dc_host"] == "dc01.corporate.loc"

    # Проверка сохранения в PostgreSQL
    raw = await vault.get_raw_setting(test_db, vault.KEY_DOMAIN)
    assert raw is not None
    assert raw["username"] == "svc_intralink@corporate.loc"
    assert decrypt_token(raw["encrypted_password"]) == "DomainPassword999!"

    # Проверка записи в Redis (worker:domain_auth)
    mock_redis.set.assert_awaited()
    redis_call_args = mock_redis.set.call_args[0]
    assert redis_call_args[0] == vault.REDIS_KEY_DOMAIN_AUTH

    # Расшифровываем значение, переданное в Redis
    enc_payload = redis_call_args[1]
    decrypted_json = json.loads(decrypt_token(enc_payload))
    assert decrypted_json["username"] == "svc_intralink@corporate.loc"
    assert decrypted_json["password"] == "DomainPassword999!"


@pytest.mark.asyncio
@patch("app.services.vault.get_redis_client")
async def test_vault_warmup_from_postgres(mock_redis_factory, test_db: AsyncSession):
    """
    Проверяет, что при пустом Redis вызов sync_vault_to_redis
    автоматически прогревает все ключи из PostgreSQL.
    """
    mock_redis = AsyncMock()
    mock_redis_factory.return_value = mock_redis

    # Сохраняем учетки в PostgreSQL
    await vault.save_service_account_credentials(
        test_db,
        login="svc_test",
        password="Pwd1!",
    )
    await vault.save_domain_credentials(
        test_db,
        username="svc_domain@corp.loc",
        password="Pwd2!",
    )

    mock_redis.reset_mock()

    # Имитация перезапуска Core API: прогрев кэша Redis из БД
    sync_results = await vault.sync_vault_to_redis(test_db)
    assert sync_results["service_auth"] is True
    assert sync_results["domain_auth"] is True
    assert mock_redis.set.call_count == 2


@pytest.mark.asyncio
@patch("app.services.vault.get_redis_client")
async def test_vault_get_status_no_passwords(mock_redis_factory, test_db: AsyncSession):
    """
    Проверяет, что get_vault_status возвращает корректные булевы флаги
    и имена пользователей, не раскрывая пароли.
    """
    mock_redis = AsyncMock()
    mock_redis.get.side_effect = lambda k: b"encrypted_token" if k in (vault.REDIS_KEY_SERVICE_AUTH, vault.REDIS_KEY_DOMAIN_AUTH) else ("online" if k == vault.REDIS_KEY_WIN_DAEMON_HEALTH else None)
    mock_redis_factory.return_value = mock_redis

    await vault.save_service_account_credentials(test_db, "admin_user", "Secret1")
    await vault.save_domain_credentials(test_db, "ad_admin@corp.loc", "Secret2")
    await vault.save_local_admin_credentials(test_db, ".\\Administrator", "Secret3")

    status = await vault.get_vault_status(test_db)
    assert status["is_ready"] is True
    assert status["service_account"]["is_configured"] is True
    assert status["service_account"]["login"] == "admin_user"
    assert "password" not in status["service_account"]

    assert status["domain"]["is_configured"] is True
    assert status["domain"]["username"] == "ad_admin@corp.loc"
    assert "password" not in status["domain"]

    assert status["local_admin"]["is_configured"] is True
    assert status["local_admin"]["username"] == ".\\Administrator"
    assert "password" not in status["local_admin"]

    assert status["execution_worker"]["online"] is True


@pytest.mark.asyncio
@patch("app.services.vault.check_tcp_port")
async def test_vault_winrm_connection_test(mock_check_port):
    """
    Проверяет работу экспресс-теста подключения WinRM.
    """
    mock_check_port.return_value = True
    res = await vault.test_winrm_connection("dc01.corporate.loc", 5985)
    assert res["success"] is True
    assert res["port"] == 5985
    assert "успешно доступен" in res["message"]

    mock_check_port.return_value = False
    fail_res = await vault.test_winrm_connection("offline-host", 5985)
    assert fail_res["success"] is False


@pytest.mark.asyncio
@patch("app.services.vault.get_redis_client")
async def test_vault_api_endpoints(mock_redis_factory, test_db: AsyncSession):
    """
    Проверяет работу HTTP-эндпоинтов /api/v1/admin/vault/* через TestClient.
    """
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.database.db import get_db
    from app.routers.admin_settings import require_admin_auth

    mock_redis = AsyncMock()
    mock_redis_factory.return_value = mock_redis

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_admin_auth] = lambda: {"sub": "admin", "role": "admin"}

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. GET /api/v1/admin/vault/status
            resp = await client.get("/api/v1/admin/vault/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "is_ready" in data
            assert "domain" in data

            # 2. POST /api/v1/admin/vault/domain
            post_dom = await client.post(
                "/api/v1/admin/vault/domain",
                json={
                    "username": "svc_test@corporate.loc",
                    "password": "SecretDomain123",
                    "domain": "corporate.loc",
                },
            )
            assert post_dom.status_code == 200
            assert post_dom.json()["status"] == "success"

            # 3. POST /api/v1/admin/vault/local-admin
            post_local = await client.post(
                "/api/v1/admin/vault/local-admin",
                json={
                    "username": ".\\Администратор",
                    "password": "LocalPassword!",
                },
            )
            assert post_local.status_code == 200

            # 4. Проверяем обновление статуса
            resp_after = await client.get("/api/v1/admin/vault/status")
            assert resp_after.status_code == 200
            data_after = resp_after.json()
            assert data_after["domain"]["is_configured"] is True
            assert data_after["domain"]["username"] == "svc_test@corporate.loc"
    finally:
        app.dependency_overrides.clear()
