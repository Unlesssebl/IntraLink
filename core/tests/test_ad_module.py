"""Tests for Active Directory utilities: transliteration, passwords, connection pooling."""

import pytest

from core.ad import (
    ADPoolConfig,
    ActiveDirectoryPool,
    SecretPassword,
    generate_sam_account_name,
    generate_secure_password,
    is_valid_domain_computer,
    mask_password,
    transliterate_to_latin,
)
from core.ad.password import AMBIGUOUS_CHARS


def test_transliteration_gost_779():
    """Verify ГОСТ 7.79-2000 System B transliteration."""
    assert transliterate_to_latin("Иванов") == "ivanov"
    assert transliterate_to_latin("Щукин") == "shchukin"
    assert transliterate_to_latin("Юрьев") == "yurev"
    assert transliterate_to_latin("Чайковский") == "chaykovskiy"
    assert transliterate_to_latin("Подъём") == "podyom"


def test_generate_sam_account_name():
    """Verify standard sAMAccountName format, collision indices and length limit."""
    # Standard format: surname.i
    assert generate_sam_account_name("Иванов", "Иван") == "ivanov.i"

    # Collision index > 1
    assert generate_sam_account_name("Иванов", "Иван", collision_index=2) == "ivanov.i2"
    assert generate_sam_account_name("Иванов", "Иван", collision_index=10) == "ivanov.i10"

    # Long surname truncated to <= 20 chars
    long_surname = "Оченьдлиннаяфамилиясотрудника"
    sam = generate_sam_account_name(long_surname, "Иван", collision_index=3)
    assert len(sam) <= 20
    assert sam.endswith(".i3")


def test_password_zero_plaintext_policy():
    """Verify passwords are redacted by default and comply with complexity requirements."""
    pwd = generate_secure_password(14)
    assert isinstance(pwd, SecretPassword)

    # String representation must be redacted
    assert str(pwd) == "***REDACTED***"
    assert repr(pwd) == "SecretPassword('***REDACTED***')"
    assert mask_password("any_plain_password") == "***REDACTED***"

    # Raw value retrieval only via get_secret_value()
    raw = pwd.get_secret_value()
    assert len(raw) == 14

    # Complexity checks
    assert any(c.isupper() for c in raw), "Password must contain uppercase letters"
    assert any(c.islower() for c in raw), "Password must contain lowercase letters"
    assert any(c.isdigit() for c in raw), "Password must contain digits"
    assert any(c in "!@#$%^&*()_+=-" for c in raw), "Password must contain special characters"

    # Zero ambiguous characters
    assert not any(c in AMBIGUOUS_CHARS for c in raw), "Ambiguous characters must not be present"


def test_ad_pool_configuration():
    """Verify ADPoolConfig validation and default values."""
    config = ADPoolConfig(
        servers=["dc1.corporate.loc", "dc2.corporate.loc"],
        connect_timeout=1.5,
        port=389,
    )
    assert config.servers == ["dc1.corporate.loc", "dc2.corporate.loc"]
    assert config.connect_timeout == 1.5

    pool = ActiveDirectoryPool(config=config)
    assert len(pool._servers) == 2
    assert pool.server_pool is not None


class MockRedis:
    def __init__(self) -> None:
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0):
        self.store[key] = value
        return True


@pytest.mark.asyncio
async def test_is_valid_domain_computer_empty():
    assert await is_valid_domain_computer("") is False
    assert await is_valid_domain_computer("   ") is False


@pytest.mark.asyncio
async def test_is_valid_domain_computer_redis_cache_hit():
    mock_redis = MockRedis()
    await mock_redis.set("cache:ad:computer:WKS-1020", "1")
    assert await is_valid_domain_computer("wks-1020", redis_client=mock_redis) is True

    await mock_redis.set("cache:ad:computer:CANON-MF3010", "0")
    assert await is_valid_domain_computer("CANON-MF3010", redis_client=mock_redis) is False


@pytest.mark.asyncio
async def test_is_valid_domain_computer_dns_success(monkeypatch):
    mock_redis = MockRedis()

    async def fake_dns(host, timeout_sec=0.4):
        return "10.244.1.50" if host == "WKS-ONLINE" else None

    monkeypatch.setattr("core.diagnostic.ping.resolve_dns_fast", fake_dns)

    res = await is_valid_domain_computer("WKS-ONLINE", redis_client=mock_redis)
    assert res is True
    assert await mock_redis.get("cache:ad:computer:WKS-ONLINE") == "1"


@pytest.mark.asyncio
async def test_is_valid_domain_computer_ad_success(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    mock_redis = MockRedis()

    async def fake_dns(host, timeout_sec=0.4):
        return None

    monkeypatch.setattr("core.diagnostic.ping.resolve_dns_fast", fake_dns)

    mock_pool = MagicMock(spec=ActiveDirectoryPool)
    mock_pool.get_computer = AsyncMock(return_value={"cn": "WKS-IN-AD", "operating_system": "Windows 11"})

    res = await is_valid_domain_computer("WKS-IN-AD", redis_client=mock_redis, ad_pool=mock_pool)
    assert res is True
    assert await mock_redis.get("cache:ad:computer:WKS-IN-AD") == "1"


@pytest.mark.asyncio
async def test_is_valid_domain_computer_negative_caching(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    mock_redis = MockRedis()

    async def fake_dns(host, timeout_sec=0.4):
        return None

    monkeypatch.setattr("core.diagnostic.ping.resolve_dns_fast", fake_dns)

    mock_pool = MagicMock(spec=ActiveDirectoryPool)
    mock_pool.get_computer = AsyncMock(return_value=None)

    res = await is_valid_domain_computer("PANTUM-P3300", redis_client=mock_redis, ad_pool=mock_pool)
    assert res is False
    assert await mock_redis.get("cache:ad:computer:PANTUM-P3300") == "0"

