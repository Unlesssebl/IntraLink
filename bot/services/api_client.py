import aiohttp
import logging
from typing import Optional, Dict, Any, List
from config import CORE_API_URL, BOT_API_KEY

logger = logging.getLogger(__name__)


class CoreAPIClient:
    def __init__(self, base_url: str = CORE_API_URL, api_key: str = BOT_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json", "X-Bot-Api-Key": api_key}
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        # TODO(security): Проверить SSL_VERIFY, если требуется. Используем системные настройки по умолчанию.
        try:
            session = await self.get_session()
            async with session.request(
                method=method,
                url=url,
                headers=self.headers,
                params=params,
                json=json_data,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status in (200, 201):
                    return await response.json()
                elif response.status == 404:
                    logger.warning(
                        "Ресурс не найден [%d] для %s", response.status, endpoint
                    )
                    return None
                else:
                    text = await response.text()
                    logger.error(
                        "Ошибка Core API [%d] для %s: %s",
                        response.status,
                        endpoint,
                        text,
                    )
                    return None
        except Exception as e:
            logger.exception(
                "Сетевая ошибка при обращении к Core API %s: %s", endpoint, e
            )
            return None

    async def login(
        self, tg_user_id: int, login: str, password: str
    ) -> Optional[Dict[str, Any]]:
        """
        Отправляет запрос на авторизацию пользователя в Core API.
        """
        payload = {"tg_user_id": tg_user_id, "login": login, "password": password}
        return await self.make_post_request("auth/login", payload)

    async def logout(self, tg_user_id: int) -> Optional[Dict[str, Any]]:
        """
        Удаляет учетные данные пользователя из Core API.
        """
        return await self._make_request(
            endpoint="auth/logout", method="DELETE", params={"tg_user_id": tg_user_id}
        )

    async def get_tasks(
        self, tg_user_id: int, filters: Optional[Dict[str, Any]] = None
    ) -> Optional[Any]:
        """
        Получает список задач для пользователя по tg_user_id.
        """
        params = {"tg_user_id": tg_user_id}
        if filters:
            params.update(filters)
        return await self._make_request(endpoint="tasks", method="GET", params=params)

    async def get_statuses(self, tg_user_id: int) -> Optional[List[Dict[str, Any]]]:
        """
        Получает список возможных статусов.
        """
        return await self._make_request(
            endpoint="statuses", method="GET", params={"tg_user_id": tg_user_id}
        )

    async def get_user(self, tg_user_id: int) -> Optional[Dict[str, Any]]:
        """
        Получает информацию о пользователе из Core API.
        """
        return await self._make_request(endpoint=f"users/{tg_user_id}", method="GET")

    async def make_post_request(
        self, endpoint: str, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        return await self._make_request(
            endpoint=endpoint, method="POST", json_data=payload
        )


api_client = CoreAPIClient()
