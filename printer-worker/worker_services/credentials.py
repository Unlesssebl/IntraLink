import json
import socket
import re
from worker_services.redis_listener import get_redis
from worker_services.crypto import decrypt_token
import worker_config as config


def format_smb_username(host: str, domain: str, username: str) -> str:
    """
    Форматирует имя пользователя для SMB-авторизации, приводя его к UPN-формату (user@domain.com)
    или сохраняя исходный формат (например, domain\\user), если это необходимо.
    """
    if not username:
        return ""

    # Если имя пользователя уже содержит спецсимволы домена (например, user@domain или domain\\user), возвращаем как есть
    if "@" in username or "\\" in username:
        return username

    # Пытаемся получить FQDN хоста
    try:
        fqdn = socket.getfqdn(host)
        # Проверяем, что fqdn не является IP-адресом
        if "." in fqdn and not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", fqdn):
            discovered_domain = fqdn.split(".", 1)[1]
            # Убедимся, что домен не является куском IP-адреса (например, "0.0.1")
            if discovered_domain and not re.match(r"^\d{1,3}\.\d{1,3}$|^\d{1,3}$", discovered_domain):
                return f"{username}@{discovered_domain}"
    except Exception:
        pass

    # Если FQDN получить не удалось или домен не обнаружен, используем переданный domain
    if domain:
        if "." in domain:
            return f"{username}@{domain}"
        else:
            return f"{domain}\\{username}"

    return username


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
