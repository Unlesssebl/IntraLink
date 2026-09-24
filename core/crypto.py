"""Symmetric cryptographic utilities for IntraLink tokens and credentials using Fernet."""

import logging
import os
from typing import Optional, Union

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("core.crypto")

_fernet: Optional[Fernet] = None


def get_fernet() -> Optional[Fernet]:
    """Get or initialize the Fernet cipher instance using ENCRYPTION_KEY from environment."""
    global _fernet
    if _fernet is not None:
        return _fernet

    raw_key = os.getenv("ENCRYPTION_KEY")
    if not raw_key:
        logger.warning("ENCRYPTION_KEY is not configured in environment. Tokens will not be encrypted.")
        return None

    try:
        key_bytes = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
        _fernet = Fernet(key_bytes)
        return _fernet
    except Exception as exc:
        logger.error("Failed to initialize Fernet with ENCRYPTION_KEY: %s", exc)
        return None


def set_fernet(cipher: Optional[Fernet]) -> None:
    """Override Fernet cipher for unit testing."""
    global _fernet
    _fernet = cipher


def encrypt_token(plain_b64_token: str) -> str:
    """Encrypt a Base64 token using Fernet symmetric encryption.

    If ENCRYPTION_KEY is not configured or token is empty, returns the token as-is.
    """
    if not plain_b64_token:
        return plain_b64_token

    cipher = get_fernet()
    if cipher is None:
        return plain_b64_token

    try:
        return cipher.encrypt(plain_b64_token.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        logger.error("Error encrypting token: %s", exc)
        return plain_b64_token


def decrypt_token(encrypted_token: Union[str, bytes]) -> str:
    """Decrypt a Fernet-encrypted token.

    If token is not encrypted (e.g. legacy plain Basic Auth base64) or
    decryption fails, returns the plaintext representation safely.
    """
    if not encrypted_token:
        return "" if encrypted_token is None else str(encrypted_token)

    token_str = (
        encrypted_token.decode("utf-8")
        if isinstance(encrypted_token, bytes)
        else str(encrypted_token)
    )

    cipher = get_fernet()
    if cipher is None:
        return token_str

    try:
        token_bytes = token_str.encode("utf-8")
        return cipher.decrypt(token_bytes).decode("utf-8")
    except InvalidToken:
        # Legacy unencrypted token fallback
        return token_str
    except Exception as exc:
        logger.error("Error decrypting token: %s", exc)
        return token_str


def encrypt_secret(value: str) -> str:
    """Encrypt ephemeral secret material and fail closed if key is missing."""
    if not value:
        raise ValueError("Secret value must not be empty")

    cipher = get_fernet()
    if not cipher:
        raise RuntimeError("ENCRYPTION_KEY is required for encrypting secret material")

    return cipher.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: Union[str, bytes]) -> str:
    """Decrypt an ephemeral secret; unlike legacy tokens, never return ciphertext."""
    cipher = get_fernet()
    if not cipher:
        raise RuntimeError("ENCRYPTION_KEY is required for decrypting secret material")

    token_bytes = value.encode("utf-8") if isinstance(value, str) else value
    return cipher.decrypt(token_bytes).decode("utf-8")
