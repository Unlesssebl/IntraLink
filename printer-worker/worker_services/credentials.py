import json
from worker_services.redis_listener import get_redis
from worker_services.crypto import decrypt_token
import worker_config as config


async def get_domain_credentials() -> tuple[str, str, str]:
    """
    Возвращает кортеж (domain, username, password) для WinRM/SMB.
    Сначала пытается получить настройки из Redis (настроенные через веб-панель),
    при их отсутствии или ошибке использует значения из переменных окружения.
    """
    r = get_redis()
    auth_data_encrypted = await r.get("worker:domain_auth")
    if auth_data_encrypted:
        try:
            auth_data_json = decrypt_token(auth_data_encrypted)
            auth_data = json.loads(auth_data_json)
            raw_username = auth_data.get("username", "")
            password = auth_data.get("password", "")
            domain = ""
            if "\\" in raw_username:
                domain, username = raw_username.split("\\", 1)
            else:
                username = raw_username

            if username and password:
                return domain, username, password
        except Exception:
            pass

    # Fallback на переменные окружения
    raw_username = config.WINRM_USERNAME
    domain = ""
    if "\\" in raw_username:
        domain, username = raw_username.split("\\", 1)
    else:
        username = raw_username
    return domain, username, config.WINRM_PASSWORD
