"""
Юнит-тесты для core-api/app/services/crypto.py

Покрываются сценарии:
  - encrypt_token: шифрование работает, пустой токен, нет ключа
  - decrypt_token: дешифровка работает, невалидный токен (fallback), пустой токен, нет ключа
  - Симметричность: зашифрованный токен успешно расшифровывается
  - Устойчивость к исключениям: ошибки не пробрасываются наружу
"""

from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Вспомогательные константы
# ---------------------------------------------------------------------------

# Валидный ключ Fernet для тестового окружения
TEST_FERNET_KEY = Fernet.generate_key().decode("utf-8")
PLAIN_TOKEN = "dXNlcjpwYXNz"  # base64 от "user:pass"


# ---------------------------------------------------------------------------
# БЛОК 1: encrypt_token
# ---------------------------------------------------------------------------


class TestEncryptToken:
    """Тесты функции encrypt_token."""

    def test_encrypts_token_when_key_set(self):
        """При наличии ключа токен должен шифроваться (результат ≠ оригиналу)."""
        from cryptography.fernet import Fernet as F

        fernet = F(TEST_FERNET_KEY.encode())

        import app.services.crypto as crypto_module

        original_fernet = crypto_module._fernet
        crypto_module._fernet = fernet

        result = crypto_module.encrypt_token(PLAIN_TOKEN)

        crypto_module._fernet = original_fernet

        assert result != PLAIN_TOKEN
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_plain_token_when_no_key(self):
        """Без ключа шифрования токен возвращается как есть."""
        import app.services.crypto as crypto_module

        original_fernet = crypto_module._fernet
        crypto_module._fernet = None

        result = crypto_module.encrypt_token(PLAIN_TOKEN)

        crypto_module._fernet = original_fernet
        assert result == PLAIN_TOKEN

    def test_returns_empty_string_unchanged(self):
        """Пустой токен возвращается пустой строкой (нет попытки шифрования)."""
        import app.services.crypto as crypto_module
        from cryptography.fernet import Fernet as F

        original_fernet = crypto_module._fernet
        crypto_module._fernet = F(TEST_FERNET_KEY.encode())

        result = crypto_module.encrypt_token("")

        crypto_module._fernet = original_fernet
        assert result == ""

    def test_returns_token_on_encryption_exception(self):
        """При ошибке шифрования возвращает исходный токен, не пробрасывает исключение."""
        import app.services.crypto as crypto_module

        bad_fernet = object()  # не Fernet, вызовет ошибку
        original_fernet = crypto_module._fernet
        crypto_module._fernet = bad_fernet  # type: ignore[assignment]

        result = crypto_module.encrypt_token(PLAIN_TOKEN)

        crypto_module._fernet = original_fernet
        assert result == PLAIN_TOKEN


# ---------------------------------------------------------------------------
# БЛОК 2: decrypt_token
# ---------------------------------------------------------------------------


class TestDecryptToken:
    """Тесты функции decrypt_token."""

    def test_decrypts_valid_encrypted_token(self):
        """Корректно зашифрованный токен должен успешно расшифровываться."""
        from cryptography.fernet import Fernet as F

        fernet = F(TEST_FERNET_KEY.encode())
        encrypted = fernet.encrypt(PLAIN_TOKEN.encode()).decode()

        import app.services.crypto as crypto_module

        original_fernet = crypto_module._fernet
        crypto_module._fernet = fernet

        result = crypto_module.decrypt_token(encrypted)

        crypto_module._fernet = original_fernet
        assert result == PLAIN_TOKEN

    def test_returns_token_as_is_when_no_key(self):
        """Без ключа шифрования токен возвращается как есть (passthrough)."""
        import app.services.crypto as crypto_module

        original_fernet = crypto_module._fernet
        crypto_module._fernet = None

        result = crypto_module.decrypt_token("some_base64_token")

        crypto_module._fernet = original_fernet
        assert result == "some_base64_token"

    def test_returns_empty_string_unchanged(self):
        """Пустой токен возвращается пустой строкой."""
        import app.services.crypto as crypto_module
        from cryptography.fernet import Fernet as F

        original_fernet = crypto_module._fernet
        crypto_module._fernet = F(TEST_FERNET_KEY.encode())

        result = crypto_module.decrypt_token("")

        crypto_module._fernet = original_fernet
        assert result == ""

    def test_returns_invalid_token_as_is_fallback(self):
        """
        Токен, который не удалось расшифровать (InvalidToken — например, старый
        не зашифрованный base64) должен возвращаться как есть без исключения.
        """
        from cryptography.fernet import Fernet as F
        import app.services.crypto as crypto_module

        original_fernet = crypto_module._fernet
        crypto_module._fernet = F(TEST_FERNET_KEY.encode())

        # Обычный base64 — не является валидным Fernet-токеном
        plain_b64 = "dXNlcjpwYXNz"
        result = crypto_module.decrypt_token(plain_b64)

        crypto_module._fernet = original_fernet
        # Должен вернуть исходную строку (fallback), а не бросить исключение
        assert result == plain_b64

    def test_returns_token_on_generic_exception(self):
        """При неожиданном исключении возвращает исходный токен, не пробрасывает ошибку."""
        import app.services.crypto as crypto_module

        bad_fernet = object()  # вызовет AttributeError при вызове decrypt
        original_fernet = crypto_module._fernet
        crypto_module._fernet = bad_fernet  # type: ignore[assignment]

        result = crypto_module.decrypt_token(PLAIN_TOKEN)

        crypto_module._fernet = original_fernet
        assert result == PLAIN_TOKEN


# ---------------------------------------------------------------------------
# БЛОК 3: Симметричность encrypt/decrypt
# ---------------------------------------------------------------------------


class TestEncryptDecryptSymmetry:
    """Тесты на симметричность: зашифровать → расшифровать = оригинал."""

    def test_round_trip_with_valid_key(self):
        """encrypt_token → decrypt_token должен вернуть исходный токен."""
        from cryptography.fernet import Fernet as F

        fernet = F(TEST_FERNET_KEY.encode())

        import app.services.crypto as crypto_module

        original_fernet = crypto_module._fernet
        crypto_module._fernet = fernet

        encrypted = crypto_module.encrypt_token(PLAIN_TOKEN)
        decrypted = crypto_module.decrypt_token(encrypted)

        crypto_module._fernet = original_fernet
        assert decrypted == PLAIN_TOKEN

    def test_round_trip_with_special_chars(self):
        """Токен со спецсимволами должен проходить round-trip без потерь."""
        from cryptography.fernet import Fernet as F

        fernet = F(TEST_FERNET_KEY.encode())

        import app.services.crypto as crypto_module

        original_fernet = crypto_module._fernet
        crypto_module._fernet = fernet

        special_token = "user+name:p@ssw0rd!#$%"
        encrypted = crypto_module.encrypt_token(special_token)
        decrypted = crypto_module.decrypt_token(encrypted)

        crypto_module._fernet = original_fernet
        assert decrypted == special_token

    def test_round_trip_without_key_is_identity(self):
        """Без ключа encrypt и decrypt — обе функции identity (токен не меняется)."""
        import app.services.crypto as crypto_module

        original_fernet = crypto_module._fernet
        crypto_module._fernet = None

        encrypted = crypto_module.encrypt_token(PLAIN_TOKEN)
        decrypted = crypto_module.decrypt_token(encrypted)

        crypto_module._fernet = original_fernet
        assert decrypted == PLAIN_TOKEN
