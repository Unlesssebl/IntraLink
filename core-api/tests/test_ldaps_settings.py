import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database.db import Base, SystemSetting, get_db
from app.main import app
from app.routers.self_service import create_printer_run_token
from app.services.active_directory import ConnectionTestResult, LDAPSConfig, add_computer_to_wlan_group
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def test_db_session():
    """Тестовая in-memory база данных SQLite."""
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


@pytest.mark.asyncio
async def test_admin_auth_success_and_failure():
    """Проверка корпоративной RBAC-аутентификации администратора IntraService."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Неверный логин/пароль в IntraService -> 401
        with patch("app.routers.admin_settings.verify_credentials", return_value=(None, None)):
            res_fail = await client.post(
                "/api/v1/admin/auth/login",
                json={"username": "belikov.a", "password": "wrong-password-123"},
            )
            assert res_fail.status_code == 401
            assert "Неверный логин или пароль" in res_fail.json()["detail"]

        # 2. Верные учетные данные, но логин НЕ в ADMIN_LOGINS -> 403 Forbidden
        with patch("app.routers.admin_settings.verify_credentials", return_value=("auth_b64", 1000)):
            res_forbidden = await client.post(
                "/api/v1/admin/auth/login",
                json={"username": "unauthorized_user", "password": "valid_password"},
            )
            assert res_forbidden.status_code == 403
            assert "не имеет прав администратора" in res_forbidden.json()["detail"]

        # 3. Верный логин из ADMIN_LOGINS (belikov.a) -> 200 + токен
        with patch("app.routers.admin_settings.verify_credentials", return_value=("auth_b64", 8664)):
            res_ok = await client.post(
                "/api/v1/admin/auth/login",
                json={"username": "belikov.a", "password": "valid_password"},
            )
            assert res_ok.status_code == 200
            data = res_ok.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"
            assert data["expires_in"] > 0
            admin_token = data["access_token"]

        # 4. SSO: вход без передачи логина/пароля при наличии валидной сессии в Cookie
        client.cookies.set("admin_session", admin_token)
        res_sso = await client.post("/api/v1/admin/auth/login")
        assert res_sso.status_code == 200
        assert res_sso.json()["access_token"] == admin_token

        # 5. Fallback: вход по устаревшему ADMIN_PASSWORD (если задан)
        with patch.object(settings, "ADMIN_PASSWORD", "legacy-master-pass"):
            res_fallback = await client.post(
                "/api/v1/admin/auth/login",
                json={"password": "legacy-master-pass"},
            )
            assert res_fallback.status_code == 200
            assert "access_token" in res_fallback.json()


@pytest.mark.asyncio
async def test_settings_endpoints_require_auth():
    """Проверка защиты административных эндпоинтов от неавторизованного доступа."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Без токена -> 401
        res = await client.get("/api/v1/admin/settings")
        assert res.status_code == 401

        # С невалидным токеном -> 401
        res_invalid = await client.get(
            "/api/v1/admin/settings",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert res_invalid.status_code == 401


@pytest.mark.asyncio
async def test_crud_ldaps_and_helpdesk_settings(test_db_session: AsyncSession):
    """Проверка сохранения и чтения настроек LDAPS и Helpdesk профилей."""
    async def override_get_db():
        yield test_db_session
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Получаем токен
        with patch("app.routers.admin_settings.verify_credentials", return_value=("auth_b64", 8664)):
            login_res = await client.post(
                "/api/v1/admin/auth/login",
                json={"username": "belikov.a", "password": "valid_password"},
            )
            token = login_res.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

        # 1. Сохраняем настройки LDAPS
        ldaps_payload = {
            "server": "dc01.test.corp",
            "port": 636,
            "use_ssl": True,
            "user_dn": "svc_test@test.corp",
            "password": "SecretLdapPassword123!",
            "is_password_set": False,
            "base_dn": "DC=test,DC=corp",
            "wlan_group_name": "WLAN-TEST",
            "domain_name": "test.corp",
        }
        post_ldaps = await client.post(
            "/api/v1/admin/settings/ldaps",
            json=ldaps_payload,
            headers=headers,
        )
        assert post_ldaps.status_code == 200
        saved_ldaps = post_ldaps.json()
        assert saved_ldaps["server"] == "dc01.test.corp"
        assert saved_ldaps["is_password_set"] is True
        # Пароль не должен возвращаться в открытом виде
        assert saved_ldaps["password"] is None

        # 2. Сохраняем настройки Helpdesk
        helpdesk_payload = {
            "primary_executor_id": 9999,
            "default_executor_ids": "9999,10000",
            "primary_filter_id": 1234,
            "timezone": "Europe/Moscow",
        }
        post_hd = await client.post(
            "/api/v1/admin/settings/helpdesk",
            json=helpdesk_payload,
            headers=headers,
        )
        assert post_hd.status_code == 200

        # 3. Сохраняем локального администратора (fallback)
        local_payload = {
            "username": ".\\Администратор",
            "password": "LocalSuperSecretPassword123!",
        }
        post_local = await client.post(
            "/api/v1/admin/settings/local-admin",
            json=local_payload,
            headers=headers,
        )
        assert post_local.status_code == 200
        assert post_local.json()["is_password_set"] is True

        # 4. Читаем все настройки
        get_all = await client.get("/api/v1/admin/settings", headers=headers)
        assert get_all.status_code == 200
        data = get_all.json()
        assert data["ldaps"]["server"] == "dc01.test.corp"
        assert data["ldaps"]["wlan_group_name"] == "WLAN-TEST"
        assert data["ldaps"]["is_password_set"] is True
        assert data["helpdesk"]["primary_executor_id"] == 9999
        assert data["helpdesk"]["primary_filter_id"] == 1234
        assert data["local_admin"]["username"] == ".\\Администратор"
        assert data["local_admin"]["is_password_set"] is True
        assert data["local_admin"]["password"] is None


@pytest.mark.asyncio
async def test_ldaps_test_connection_endpoint(test_db_session: AsyncSession):
    """Проверка эндпоинта тестирования связи с LDAPS."""
    async def override_get_db():
        yield test_db_session
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.routers.admin_settings.verify_credentials", return_value=("auth_b64", 8664)):
            login_res = await client.post(
                "/api/v1/admin/auth/login",
                json={"username": "belikov.a", "password": "valid_password"},
            )
            token = login_res.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

        mock_test_result = ConnectionTestResult(
            success=True,
            latency_ms=15.4,
            message="Успешное подключение и аутентификация в Active Directory по LDAPS",
            details={"server": "dc01.test.corp", "search_verified": True},
        )

        with patch(
            "app.routers.admin_settings.test_ldaps_connection",
            new_callable=AsyncMock,
            return_value=mock_test_result,
        ):
            res = await client.post(
                "/api/v1/admin/settings/ldaps/test",
                json={
                    "server": "dc01.test.corp",
                    "port": 636,
                    "use_ssl": True,
                    "user_dn": "svc@test.corp",
                    "password": "Password!",
                    "is_password_set": True,
                    "base_dn": "DC=test,DC=corp",
                    "wlan_group_name": "WLAN-WORKNET",
                    "domain_name": "test.corp",
                },
                headers=headers,
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["latency_ms"] == 15.4
            assert "Успешное подключение" in data["message"]


@pytest.mark.asyncio
async def test_add_computer_to_wlan_group_success():
    """Проверка прямого добавления ПК в группу Wi-Fi через LDAPS-сервис."""
    config = LDAPSConfig(
        server="dc.test.corp",
        port=636,
        use_ssl=True,
        user_dn="admin@test.corp",
        password="pwd",
        base_dn="DC=test,DC=corp",
        wlan_group_name="WLAN-WORKNET",
        domain_name="test.corp",
    )

    with patch("ldap3.Connection") as mock_conn_cls:
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn

        # Мокаем поиск компьютера
        mock_comp_entry = MagicMock()
        mock_comp_entry.distinguishedName.value = "CN=PC-TEST-01,OU=Computers,DC=test,DC=corp"

        # Мокаем поиск группы
        mock_group_entry = MagicMock()
        mock_group_entry.distinguishedName.value = "CN=WLAN-WORKNET,OU=Groups,DC=test,DC=corp"
        mock_group_entry.member.values = []

        def side_search(search_base, search_filter, search_scope, attributes):
            if "objectCategory=computer" in search_filter:
                mock_conn.entries = [mock_comp_entry]
            elif "objectCategory=group" in search_filter:
                mock_conn.entries = [mock_group_entry]
            return True

        mock_conn.search.side_effect = side_search
        mock_conn.modify.return_value = True
        mock_conn_cls.return_value = mock_conn

        result = await add_computer_to_wlan_group("PC-TEST-01", config)
        assert result["success"] is True
        assert result["already_member"] is False
        assert result["computer_name"] == "PC-TEST-01"
        assert result["group_name"] == "WLAN-WORKNET"


@pytest.mark.asyncio
async def test_self_service_fallback_router():
    """Проверка Fallback One-Liner роутера для принтеров."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Мокаем Redis для сохранения токена
        fake_storage = {}

        class FakeRedis:
            async def setex(self, key, ttl, val):
                fake_storage[key] = val

            async def get(self, key):
                return fake_storage.get(key)

            async def delete(self, key):
                fake_storage.pop(key, None)

            async def aclose(self):
                pass

        with patch("app.routers.self_service.get_redis_client", return_value=FakeRedis()):
            token = await create_printer_run_token(
                task_id=9876,
                pc_name="PC-USER-01",
                printer_name="Canon iR-ADV C3520",
                driver_name="Canon Generic Plus PCL6",
                ip_address="192.168.1.50",
            )
            assert token is not None

            # 1. Получение скрипта по токену
            script_res = await client.get(f"/api/v1/run/{token}")
            assert script_res.status_code == 200
            assert "Canon iR-ADV C3520" in script_res.text
            assert "Add-PrinterPort" in script_res.text
            assert "Invoke-RestMethod" in script_res.text

            # 2. Отправка отчета о завершении
            complete_res = await client.post(
                f"/api/v1/run/{token}/complete",
                json={
                    "status": "success",
                    "pc": "PC-USER-01",
                    "details": {"installed": True},
                },
            )
            assert complete_res.status_code == 200
            assert complete_res.json()["status"] == "success"
            assert complete_res.json()["task_id"] == 9876
