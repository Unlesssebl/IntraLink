import logging
from cryptography.fernet import Fernet, InvalidToken
from app.config import settings

logger = logging.getLogger(__name__)

_fernet = None

if settings.ENCRYPTION_KEY:
    try:
        _fernet = Fernet(settings.ENCRYPTION_KEY.encode('utf-8'))
    except Exception as e:
        logger.error("Failed to initialize Fernet with provided ENCRYPTION_KEY: %s", e)
else:
    logger.warning("ENCRYPTION_KEY is not set. Tokens will not be encrypted.")

def encrypt_token(plain_b64_token: str) -> str:
    """
    Шифрует токен.
    Если ключ шифрования не задан или токен пустой, возвращает токен как есть.
    """
    if not _fernet or not plain_b64_token:
        return plain_b64_token
    try:
        return _fernet.encrypt(plain_b64_token.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error("Error encrypting token: %s", e)
        return plain_b64_token

def decrypt_token(encrypted_token: str) -> str:
    """
    Расшифровывает токен.
    Если расшифровка не удалась (например, старый не зашифрованный токен),
    возвращает токен как есть.
    """
    if not _fernet or not encrypted_token:
        return encrypted_token
    try:
        return _fernet.decrypt(encrypted_token.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        # Вероятно, это старый не зашифрованный токен (Basic Auth base64)
        return encrypted_token
    except Exception as e:
        logger.error("Error decrypting token: %s", e)
        return encrypted_token
