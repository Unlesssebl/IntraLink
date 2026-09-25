"""Tests for Active Directory utilities: transliteration, passwords, connection pooling."""

import pytest

from core.ad import (
    ADPoolConfig,
    ActiveDirectoryPool,
    SecretPassword,
    generate_sam_account_name,
    generate_secure_password,
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
