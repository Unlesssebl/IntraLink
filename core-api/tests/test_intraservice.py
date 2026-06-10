"""
Юнит-тесты для core-api/app/services/intraservice.py

Покрываются сценарии:
  - parse_api_date: все поддерживаемые форматы, граничные случаи, невалидные строки
  - init_session / close_session: управление жизненным циклом aiohttp.ClientSession
  - _make_request: успешный ответ, HTTP-ошибки, сетевые исключения, авторизация
  - verify_credentials: успех, провал аутентификации
  - get_tasks: передача параметров и фильтров
  - get_task_lifetime: корректный endpoint и параметры
  - get_statuses: корректный endpoint
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import aiohttp


# ---------------------------------------------------------------------------
# БЛОК 1: parse_api_date
# ---------------------------------------------------------------------------

class TestParseApiDate:
    """Тесты парсинга дат из API IntraService."""

    def test_returns_none_for_none_input(self):
        """None → None."""
        from app.services.intraservice import parse_api_date
        assert parse_api_date(None) is None

    def test_returns_none_for_empty_string(self):
        """Пустая строка → None."""
        from app.services.intraservice import parse_api_date
        assert parse_api_date("") is None

    def test_parses_iso_format_with_space(self):
        """Формат 'YYYY-MM-DD HH:MM:SS' должен парситься корректно."""
        from app.services.intraservice import parse_api_date
        result = parse_api_date("2025-06-01 10:30:00")
        assert result == datetime(2025, 6, 1, 10, 30, 0)

    def test_parses_iso_format_without_seconds(self):
        """Формат 'YYYY-MM-DD HH:MM' должен парситься корректно."""
        from app.services.intraservice import parse_api_date
        result = parse_api_date("2025-06-01 10:30")
        assert result == datetime(2025, 6, 1, 10, 30, 0)

    def test_parses_iso_format_with_T_separator(self):
        """Формат с разделителем 'T' (ISO 8601) должен парситься как с пробелом."""
        from app.services.intraservice import parse_api_date
        result = parse_api_date("2025-06-01T10:30:00")
        assert result == datetime(2025, 6, 1, 10, 30, 0)

    def test_parses_russian_date_format_with_seconds(self):
        """Формат 'DD.MM.YYYY HH:MM:SS' должен парситься корректно."""
        from app.services.intraservice import parse_api_date
        result = parse_api_date("01.06.2025 10:30:00")
        assert result == datetime(2025, 6, 1, 10, 30, 0)

    def test_parses_russian_date_format_without_seconds(self):
        """Формат 'DD.MM.YYYY HH:MM' должен парситься корректно."""
        from app.services.intraservice import parse_api_date
        result = parse_api_date("01.06.2025 10:30")
        assert result == datetime(2025, 6, 1, 10, 30, 0)

    def test_returns_none_for_invalid_string(self):
        """Невалидная строка → None."""
        from app.services.intraservice import parse_api_date
        assert parse_api_date("not-a-date") is None

    def test_returns_none_for_partial_date_only(self):
        """Строка только с датой без времени → None (нет совпадения ни с одним форматом)."""
        from app.services.intraservice import parse_api_date
        assert parse_api_date("2025-06-01") is None

    def test_result_has_no_tzinfo(self):
        """Результат не должен содержать timezone (naive datetime)."""
        from app.services.intraservice import parse_api_date
        result = parse_api_date("2025-06-01 10:30:00")
        assert result is not None
        assert result.tzinfo is None


# ---------------------------------------------------------------------------
# БЛОК 2: Управление сессией aiohttp
# ---------------------------------------------------------------------------

class TestSessionManagement:
    """Тесты жизненного цикла aiohttp.ClientSession."""

    @pytest.mark.asyncio
    async def test_init_session_creates_client_session(self):
        """init_session должен создать _session если она None."""
        import app.services.intraservice as is_module
        is_module._session = None

        with patch("app.services.intraservice.aiohttp.TCPConnector") as mock_connector, \
             patch("app.services.intraservice.aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.return_value = MagicMock(closed=False)
            await is_module.init_session()
            mock_session_cls.assert_called_once()
            assert is_module._session is not None

        is_module._session = None

    @pytest.mark.asyncio
    async def test_init_session_does_not_recreate_open_session(self):
        """init_session не должен создавать новую сессию если старая ещё открыта."""
        import app.services.intraservice as is_module

        existing_session = MagicMock()
        existing_session.closed = False
        is_module._session = existing_session

        with patch("app.services.intraservice.aiohttp.ClientSession") as mock_session_cls:
            await is_module.init_session()
            mock_session_cls.assert_not_called()

        is_module._session = None

    @pytest.mark.asyncio
    async def test_close_session_closes_and_nones(self):
        """close_session должен закрыть сессию и установить _session = None."""
        import app.services.intraservice as is_module

        mock_session = AsyncMock()
        mock_session.closed = False
        is_module._session = mock_session

        await is_module.close_session()

        mock_session.close.assert_awaited_once()
        assert is_module._session is None

    @pytest.mark.asyncio
    async def test_close_session_noop_when_none(self):
        """close_session не должен падать если _session = None."""
        import app.services.intraservice as is_module
        is_module._session = None

        await is_module.close_session()  # не должно бросить исключение
        assert is_module._session is None

    @pytest.mark.asyncio
    async def test_close_session_noop_when_already_closed(self):
        """close_session не должен вызывать close() если сессия уже закрыта."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = True
        is_module._session = mock_session

        await is_module.close_session()

        # close() не вызывается, если session.closed == True
        mock_session.close.assert_not_called()
        # _session не обнуляется функцией (условие `not session.closed` не выполнилось)
        is_module._session = None  # cleanup


# ---------------------------------------------------------------------------
# Вспомогательная функция для мокирования aiohttp response
# ---------------------------------------------------------------------------

def _make_mock_response(status: int, json_data=None, text_data: str = ""):
    """Создаёт мок aiohttp ответа."""
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.json = AsyncMock(return_value=json_data)
    mock_response.text = AsyncMock(return_value=text_data)
    # Контекстный менеджер для `async with session.request(...) as response:`
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


# ---------------------------------------------------------------------------
# БЛОК 3: _make_request
# ---------------------------------------------------------------------------

class TestMakeRequest:
    """Тесты универсальной HTTP-функции _make_request."""

    @pytest.mark.asyncio
    async def test_returns_json_on_200(self):
        """При статусе 200 должен возвращать JSON-ответ."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request.return_value = _make_mock_response(200, json_data={"Tasks": []})
        is_module._session = mock_session

        with patch("app.services.intraservice.decrypt_token", return_value="user:pass"):
            result = await is_module._make_request("task", auth_b64="dXNlcjpwYXNz")

        assert result == {"Tasks": []}
        is_module._session = None

    @pytest.mark.asyncio
    async def test_returns_none_on_401(self):
        """При HTTP 401 должен возвращать None."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request.return_value = _make_mock_response(401, text_data="Unauthorized")
        is_module._session = mock_session

        result = await is_module._make_request("task")

        assert result is None
        is_module._session = None

    @pytest.mark.asyncio
    async def test_returns_none_on_500(self):
        """При HTTP 500 должен возвращать None."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request.return_value = _make_mock_response(500, text_data="Internal Server Error")
        is_module._session = mock_session

        result = await is_module._make_request("task")

        assert result is None
        is_module._session = None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_exception(self):
        """При сетевом исключении должен возвращать None, не пробрасывать ошибку."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request.side_effect = aiohttp.ClientConnectionError("Connection refused")
        is_module._session = mock_session

        result = await is_module._make_request("task")

        assert result is None
        is_module._session = None

    @pytest.mark.asyncio
    async def test_sets_authorization_header_when_auth_b64_provided(self):
        """При передаче auth_b64 должен устанавливать заголовок Authorization."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request.return_value = _make_mock_response(200, json_data={})
        is_module._session = mock_session

        with patch("app.services.intraservice.decrypt_token", return_value="decoded_token") as mock_decrypt:
            await is_module._make_request("task", auth_b64="encrypted_b64")
            mock_decrypt.assert_called_once_with("encrypted_b64")

        # Проверяем, что заголовок Authorization был передан
        call_kwargs = mock_session.request.call_args[1]
        assert "Authorization" in call_kwargs["headers"]
        assert call_kwargs["headers"]["Authorization"] == "Basic decoded_token"

        is_module._session = None

    @pytest.mark.asyncio
    async def test_no_authorization_header_without_auth_b64(self):
        """Без auth_b64 и auth_header заголовок Authorization не должен добавляться."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request.return_value = _make_mock_response(200, json_data={})
        is_module._session = mock_session

        await is_module._make_request("task")

        call_kwargs = mock_session.request.call_args[1]
        assert "Authorization" not in call_kwargs["headers"]

        is_module._session = None

    @pytest.mark.asyncio
    async def test_sets_authorization_header_when_auth_header_provided(self):
        """При передаче auth_header заголовок Authorization должен устанавливаться равным его значению."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request.return_value = _make_mock_response(200, json_data={})
        is_module._session = mock_session

        await is_module._make_request("task", auth_header="Basic my_token")

        call_kwargs = mock_session.request.call_args[1]
        assert "Authorization" in call_kwargs["headers"]
        assert call_kwargs["headers"]["Authorization"] == "Basic my_token"

        is_module._session = None

    @pytest.mark.asyncio
    async def test_url_constructed_correctly(self):
        """URL должен составляться из settings.INTRASERVICE_URL + endpoint."""
        import app.services.intraservice as is_module

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request.return_value = _make_mock_response(200, json_data={})
        is_module._session = mock_session

        with patch("app.services.intraservice.settings") as mock_settings:
            mock_settings.INTRASERVICE_URL = "http://intraservice.test/api/"
            mock_settings.SSL_VERIFY = False
            await is_module._make_request("task")

        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["url"] == "http://intraservice.test/api/task"

        is_module._session = None

    @pytest.mark.asyncio
    async def test_lazy_init_session_when_none(self):
        """Если сессия None — _make_request должен автоматически инициализировать её."""
        import app.services.intraservice as is_module
        is_module._session = None

        created_session = MagicMock()
        created_session.closed = False
        created_session.request.return_value = _make_mock_response(200, json_data={"ok": True})

        with patch("app.services.intraservice.init_session", new_callable=AsyncMock) as mock_init:
            async def set_session():
                is_module._session = created_session
            mock_init.side_effect = set_session

            result = await is_module._make_request("task")

        mock_init.assert_awaited_once()
        assert result == {"ok": True}
        is_module._session = None


# ---------------------------------------------------------------------------
# БЛОК 4: verify_credentials
# ---------------------------------------------------------------------------

class TestVerifyCredentials:
    """Тесты верификации учётных данных пользователя."""

    @pytest.mark.asyncio
    async def test_returns_auth_b64_and_user_id_on_success(self):
        """При успешном ответе должен вернуть (auth_b64, user_id)."""
        import app.services.intraservice as is_module
        import base64

        user_response = {"Id": 42, "Name": "Иван"}

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=user_response):
            auth_b64, user_id = await is_module.verify_credentials("ivan", "secret")

        expected_b64 = base64.b64encode(b"ivan:secret").decode()
        assert auth_b64 == expected_b64
        assert user_id == 42

    @pytest.mark.asyncio
    async def test_returns_none_none_on_api_failure(self):
        """При ошибке API (None) должен вернуть (None, None)."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=None):
            auth_b64, user_id = await is_module.verify_credentials("ivan", "wrong")

        assert auth_b64 is None
        assert user_id is None

    @pytest.mark.asyncio
    async def test_uses_basic_auth_not_b64_header(self):
        """verify_credentials должен генерировать auth_header через encode_basic_auth, а не передавать auth_b64."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value={"Id": 1}) as mock_request:
            await is_module.verify_credentials("user", "pass")

        call_kwargs = mock_request.call_args[1]
        assert call_kwargs.get("auth_header") == aiohttp.BasicAuth("user", "pass").encode()
        assert call_kwargs.get("auth_b64") is None

    @pytest.mark.asyncio
    async def test_calls_correct_endpoint_with_param(self):
        """verify_credentials должен запрашивать endpoint 'user' с getcurrentuserinfo=true."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value={"Id": 5}) as mock_request:
            await is_module.verify_credentials("u", "p")

        call_args, call_kwargs = mock_request.call_args
        assert call_kwargs.get("endpoint") == "user" or (call_args and call_args[0] == "user")
        params = call_kwargs.get("params", {})
        assert params.get("getcurrentuserinfo") == "true"


# ---------------------------------------------------------------------------
# БЛОК 5: get_tasks
# ---------------------------------------------------------------------------

class TestGetTasks:
    """Тесты получения списка задач."""

    @pytest.mark.asyncio
    async def test_calls_task_endpoint(self):
        """get_tasks должен обращаться к endpoint 'task'."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value={"Tasks": []}) as mock_req:
            await is_module.get_tasks("auth_b64_value")

        call_args, call_kwargs = mock_req.call_args
        endpoint = call_args[0] if call_args else call_kwargs.get("endpoint")
        assert endpoint == "task"

    @pytest.mark.asyncio
    async def test_passes_auth_b64(self):
        """get_tasks должен передавать auth_b64 в _make_request."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value={}) as mock_req:
            await is_module.get_tasks("my_auth_token")

        call_kwargs = mock_req.call_args[1]
        assert call_kwargs.get("auth_b64") == "my_auth_token"

    @pytest.mark.asyncio
    async def test_passes_filters_in_params(self):
        """get_tasks должен добавлять переданные фильтры в params."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value={}) as mock_req:
            await is_module.get_tasks("auth", {"CreatedMoreThan": "2025-06-01 10:00"})

        call_kwargs = mock_req.call_args[1]
        params = call_kwargs.get("params", {})
        assert params.get("CreatedMoreThan") == "2025-06-01 10:00"
        # Базовый include=status должен всегда присутствовать
        assert "status" in params.get("include", "")

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self):
        """Если API вернул None — get_tasks тоже должен вернуть None."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=None):
            result = await is_module.get_tasks("auth")

        assert result is None


# ---------------------------------------------------------------------------
# БЛОК 6: get_task_lifetime
# ---------------------------------------------------------------------------

class TestGetTaskLifetime:
    """Тесты получения истории изменений заявки."""

    @pytest.mark.asyncio
    async def test_calls_tasklifetime_endpoint(self):
        """get_task_lifetime должен обращаться к endpoint 'tasklifetime'."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=[]) as mock_req:
            await is_module.get_task_lifetime("auth", task_id=42)

        call_args, call_kwargs = mock_req.call_args
        endpoint = call_args[0] if call_args else call_kwargs.get("endpoint")
        assert endpoint == "tasklifetime"

    @pytest.mark.asyncio
    async def test_passes_task_id_in_params(self):
        """get_task_lifetime должен передавать taskid в params."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=[]) as mock_req:
            await is_module.get_task_lifetime("auth", task_id=99)

        call_kwargs = mock_req.call_args[1]
        assert call_kwargs.get("params", {}).get("taskid") == 99


# ---------------------------------------------------------------------------
# БЛОК 7: get_statuses
# ---------------------------------------------------------------------------

class TestGetStatuses:
    """Тесты получения справочника статусов."""

    @pytest.mark.asyncio
    async def test_calls_taskstatus_endpoint(self):
        """get_statuses должен обращаться к endpoint 'taskstatus'."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=[{"Id": 1, "Name": "Открыта"}]) as mock_req:
            result = await is_module.get_statuses("auth")

        call_args, call_kwargs = mock_req.call_args
        endpoint = call_args[0] if call_args else call_kwargs.get("endpoint")
        assert endpoint == "taskstatus"
        assert result == [{"Id": 1, "Name": "Открыта"}]


# ---------------------------------------------------------------------------
# БЛОК 8: add_task_comment
# ---------------------------------------------------------------------------

class TestAddTaskComment:
    """Тесты добавления комментария к задаче."""

    @pytest.mark.asyncio
    async def test_calls_task_endpoint_put(self):
        """add_task_comment должен обращаться к PUT task/{task_id}."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value={"Id": 1}) as mock_req:
            result = await is_module.add_task_comment("auth", task_id=42, comment="Тестовый коммент")

        assert result is True
        call_args, call_kwargs = mock_req.call_args
        endpoint = call_args[0] if call_args else call_kwargs.get("endpoint")
        method = call_kwargs.get("method")
        json_data = call_kwargs.get("json_data")
        assert endpoint == "task/42"
        assert method == "PUT"
        assert json_data == {"Comment": "Тестовый коммент"}

    @pytest.mark.asyncio
    async def test_returns_false_on_api_error(self):
        """Если API вернул ошибку (None), add_task_comment должен вернуть False."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=None):
            result = await is_module.add_task_comment("auth", task_id=42, comment="Тестовый коммент")

        assert result is False


# ---------------------------------------------------------------------------
# БЛОК 9: update_task_status
# ---------------------------------------------------------------------------

class TestUpdateTaskStatus:
    """Тесты изменения статуса задачи."""

    @pytest.mark.asyncio
    async def test_calls_task_endpoint_put(self):
        """update_task_status должен обращаться к PUT task/{task_id}."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value={"Id": 1}) as mock_req:
            result = await is_module.update_task_status("auth", task_id=42, status_id=5)

        assert result is True
        call_args, call_kwargs = mock_req.call_args
        endpoint = call_args[0] if call_args else call_kwargs.get("endpoint")
        method = call_kwargs.get("method")
        json_data = call_kwargs.get("json_data")
        assert endpoint == "task/42"
        assert method == "PUT"
        assert json_data == {"StatusId": 5}

    @pytest.mark.asyncio
    async def test_returns_false_on_api_error(self):
        """Если API вернул ошибку (None), update_task_status должен вернуть False."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=None):
            result = await is_module.update_task_status("auth", task_id=42, status_id=5)

        assert result is False


# ---------------------------------------------------------------------------
# БЛОК 10: add_task_expenses
# ---------------------------------------------------------------------------

class TestAddTaskExpenses:
    """Тесты добавления трудозатрат по задаче."""

    @pytest.mark.asyncio
    async def test_calls_taskexpenses_endpoint_post(self):
        """add_task_expenses должен обращаться к POST taskexpenses."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value={"Id": 99}) as mock_req:
            result = await is_module.add_task_expenses("auth", task_id=42, minutes=30)

        assert result is True
        call_args, call_kwargs = mock_req.call_args
        endpoint = call_args[0] if call_args else call_kwargs.get("endpoint")
        method = call_kwargs.get("method")
        json_data = call_kwargs.get("json_data")
        assert endpoint == "taskexpenses"
        assert method == "POST"
        assert json_data == {"TaskId": 42, "Minutes": 30}

    @pytest.mark.asyncio
    async def test_returns_false_on_api_error(self):
        """Если API вернул ошибку (None), add_task_expenses должен вернуть False."""
        import app.services.intraservice as is_module

        with patch("app.services.intraservice._make_request", new_callable=AsyncMock,
                   return_value=None):
            result = await is_module.add_task_expenses("auth", task_id=42, minutes=30)

        assert result is False


