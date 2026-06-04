"""
Юнит-тесты для core-api/app/services/worker.py

Покрываются сценарии:
  - process_user: happy path (новая заявка, комментарий, смена статуса)
  - process_user: граничные случаи (нет auth_b64, нет last_check_time, API вернул None)
  - get_redis_client: синглтон-поведение
  - close_redis: корректное закрытие и обнуление
  - Форматирование сообщений уведомлений
"""
import asyncio
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch, call

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def make_user(
    tg_user_id: int = 123456,
    is_user_id: int = 42,
    is_password_b64: str = "dXNlcjpwYXNz",
    last_task_id: int = 10,
    last_check_time: str = "2025-06-01 10:00:00",
) -> MagicMock:
    """Создаёт мок объекта пользователя БД."""
    user = MagicMock()
    user.tg_user_id = tg_user_id
    user.is_user_id = is_user_id
    user.is_password_b64 = is_password_b64
    user.last_task_id = last_task_id
    user.last_check_time = last_check_time
    return user


def make_task(
    task_id: int = 20,
    name: str = "Тестовая заявка",
    status_name: str = "Открыта",
    status_id: int = 1,
    executor_ids: str = "42",
) -> dict:
    return {
        "Id": task_id,
        "Name": name,
        "StatusName": status_name,
        "StatusId": status_id,
        "ExecutorIds": executor_ids,
    }


def make_lifetime_event(
    date: str = "2025-06-01 11:00:00",
    comments: str = "",
    status_id: int = 0,
    editor: str = "Иван",
) -> dict:
    return {
        "Date": date,
        "Comments": comments,
        "StatusId": status_id,
        "Editor": editor,
    }


# ---------------------------------------------------------------------------
# БЛОК 1: get_redis_client и close_redis
# ---------------------------------------------------------------------------

class TestRedisClientLifecycle:
    """Тесты управления жизненным циклом Redis-клиента."""

    def test_get_redis_client_creates_singleton(self):
        """
        get_redis_client должен возвращать один и тот же объект при повторных вызовах.
        """
        with patch("app.services.worker.settings") as mock_settings, \
             patch("app.services.worker.aioredis.from_url") as mock_from_url:
            mock_settings.REDIS_URL = "redis://localhost:6379/0"
            mock_from_url.return_value = MagicMock()

            # Сбрасываем глобальное состояние перед тестом
            import app.services.worker as worker_module
            worker_module.redis_client = None

            client1 = worker_module.get_redis_client()
            client2 = worker_module.get_redis_client()

            assert client1 is client2
            mock_from_url.assert_called_once()

            # Восстанавливаем
            worker_module.redis_client = None

    @pytest.mark.asyncio
    async def test_close_redis_closes_and_nones_client(self):
        """
        close_redis должен вызвать .close() на клиенте и установить redis_client = None.
        """
        import app.services.worker as worker_module

        mock_client = AsyncMock()
        worker_module.redis_client = mock_client

        await worker_module.close_redis()

        mock_client.close.assert_awaited_once()
        assert worker_module.redis_client is None

    @pytest.mark.asyncio
    async def test_close_redis_noop_when_none(self):
        """
        close_redis не должен падать, если клиент уже None.
        """
        import app.services.worker as worker_module
        worker_module.redis_client = None

        # Не должно бросать исключений
        await worker_module.close_redis()
        assert worker_module.redis_client is None


# ---------------------------------------------------------------------------
# БЛОК 2: process_user — граничные случаи (быстрый выход)
# ---------------------------------------------------------------------------

class TestProcessUserEarlyReturn:
    """Тесты ранних выходов из process_user при невалидных данных."""

    @pytest.mark.asyncio
    async def test_user_not_found_in_db(self, mock_redis, utc_now, moscow_tz, base_web_url):
        """
        Если db.get(User, user_id) вернул None — функция должна вернуться без ошибок.
        """
        import app.services.worker as worker_module

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm):
            await worker_module.process_user(
                user_id=999,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_missing_auth_b64(self, mock_redis, utc_now, moscow_tz, base_web_url):
        """
        Если у пользователя нет is_password_b64 — функция должна завершиться без публикации.
        """
        import app.services.worker as worker_module

        user = make_user(is_password_b64="")
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_last_check_time_sets_time_and_returns(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Если last_check_time не задан — воркер должен сохранить текущее время и выйти.
        """
        import app.services.worker as worker_module

        user = make_user(last_check_time=None)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        # Должен сохранить дату и выйти без API-вызовов
        assert user.last_check_time == utc_now.strftime("%Y-%m-%d %H:%M:%S")
        mock_db.commit.assert_awaited_once()
        mock_redis.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_returns_none_for_new_tasks_skips_iteration(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Если get_tasks() вернул None (ошибка API) — итерация пропускается, без записи в БД.
        """
        import app.services.worker as worker_module

        user = make_user()
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock, return_value=None):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_db.commit.assert_not_awaited()
        mock_redis.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# БЛОК 3: process_user — happy path (новая заявка)
# ---------------------------------------------------------------------------

class TestProcessUserNewTask:
    """Тесты обработки НОВЫХ заявок."""

    @pytest.mark.asyncio
    async def test_new_task_published_to_redis(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Новая заявка с Id > last_task_id должна опубликовать событие в Redis.
        """
        import app.services.worker as worker_module

        user = make_user(last_task_id=10)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        new_task = make_task(task_id=20, name="Принтер не работает", status_name="Открыта")
        tasks_response = {"Tasks": [new_task], "Statuses": []}
        # Обновлённые заявки — пустые (нет обновлений)
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response, updated_response]):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_awaited_once()
        call_args = mock_redis.publish.call_args
        channel = call_args[0][0]
        payload = json.loads(call_args[0][1])

        assert channel == "intraservice_events"
        assert payload["event_type"] == "new_task"
        assert payload["task_id"] == 20
        assert payload["tg_user_id"] == 123456
        assert "Принтер не работает" in payload["task_name"]

    @pytest.mark.asyncio
    async def test_task_with_lower_id_not_published(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Заявка с Id <= last_task_id НЕ должна публиковаться (уже известна).
        """
        import app.services.worker as worker_module

        user = make_user(last_task_id=20)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        old_task = make_task(task_id=15)  # Id < last_task_id=20
        tasks_response = {"Tasks": [old_task], "Statuses": []}
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response, updated_response]):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_last_task_id_updated_correctly(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        После обнаружения новой заявки last_task_id должен обновиться до максимального Id.
        """
        import app.services.worker as worker_module

        user = make_user(last_task_id=10)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        task1 = make_task(task_id=15)
        task2 = make_task(task_id=30)
        tasks_response = {"Tasks": [task1, task2], "Statuses": []}
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response, updated_response]):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        assert user.last_task_id == 30

    @pytest.mark.asyncio
    async def test_new_task_status_from_statuses_map(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Если StatusName пустой, но StatusId есть в словаре — используем словарь статусов.
        """
        import app.services.worker as worker_module

        user = make_user(last_task_id=10)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        task = make_task(task_id=20, status_name="", status_id=5)
        tasks_response = {
            "Tasks": [task],
            "Statuses": [{"Id": 5, "Name": "В работе"}]
        }
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response, updated_response]):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        assert "В работе" in payload["message"]

    @pytest.mark.asyncio
    async def test_new_task_status_fallback_to_na(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Если StatusName пуст и нет статуса в словаре — используем 'N/A'.
        """
        import app.services.worker as worker_module

        user = make_user(last_task_id=10)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        task = make_task(task_id=20, status_name=None, status_id=999)
        tasks_response = {"Tasks": [task], "Statuses": []}
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response, updated_response]):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        assert "N/A" in payload["message"]


# ---------------------------------------------------------------------------
# БЛОК 4: process_user — happy path (комментарий)
# ---------------------------------------------------------------------------

class TestProcessUserNewComment:
    """Тесты обработки новых КОММЕНТАРИЕВ."""

    @pytest.mark.asyncio
    async def test_new_comment_published(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Новый комментарий от исполнителя должен публиковаться в Redis.
        """
        import app.services.worker as worker_module

        user = make_user(is_user_id=42)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        tasks_response_empty = {"Tasks": [], "Statuses": []}
        # Обновлённая заявка, где исполнитель == is_user_id
        updated_task = make_task(task_id=5, executor_ids="42")
        updated_response = {"Tasks": [updated_task], "Statuses": []}

        # last_check_time = 2025-06-01 10:00:00 UTC = 13:00:00 МСК
        # Комментарий в 13:01 МСК — строго позже last_check (условие > строгое)
        lifetime = [make_lifetime_event(date="2025-06-01 13:01:00", comments="Всё починено!")]

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response_empty, updated_response]), \
             patch("app.services.worker.get_task_lifetime", new_callable=AsyncMock,
                   return_value=lifetime):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_awaited_once()
        payload = json.loads(mock_redis.publish.call_args[0][1])
        assert payload["event_type"] == "new_comment"
        assert "Всё починено!" in payload["message"]

    @pytest.mark.asyncio
    async def test_old_comment_not_published(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Комментарий ДО last_check_time не должен публиковаться.
        """
        import app.services.worker as worker_module

        # last_check_time = 2025-06-01 10:00:00 UTC = 13:00:00 МСК
        user = make_user(is_user_id=42, last_check_time="2025-06-01 10:00:00")
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        tasks_response_empty = {"Tasks": [], "Statuses": []}
        updated_task = make_task(task_id=5, executor_ids="42")
        updated_response = {"Tasks": [updated_task], "Statuses": []}

        # Комментарий в 12:59 МСК — до last_check (13:00 МСК)
        lifetime = [make_lifetime_event(date="2025-06-01 12:59:00", comments="Старый комментарий")]

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response_empty, updated_response]), \
             patch("app.services.worker.get_task_lifetime", new_callable=AsyncMock,
                   return_value=lifetime):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_executor_not_in_task_skipped(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Если пользователь НЕ является исполнителем задачи — пропустить её.
        """
        import app.services.worker as worker_module

        user = make_user(is_user_id=42)  # Исполнитель в API будет другой
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        tasks_response_empty = {"Tasks": [], "Statuses": []}
        updated_task = make_task(task_id=5, executor_ids="99,100")  # Нет 42
        updated_response = {"Tasks": [updated_task], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response_empty, updated_response]), \
             patch("app.services.worker.get_task_lifetime", new_callable=AsyncMock) as mock_lifetime:
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_lifetime.assert_not_awaited()
        mock_redis.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# БЛОК 5: process_user — смена статуса
# ---------------------------------------------------------------------------

class TestProcessUserStatusChange:
    """Тесты обработки смены СТАТУСА заявки."""

    @pytest.mark.asyncio
    async def test_status_change_published(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Событие смены статуса (StatusId != 0, нет Comments) должно публиковаться.
        """
        import app.services.worker as worker_module

        user = make_user(is_user_id=42)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        tasks_response_empty = {"Tasks": [], "Statuses": []}
        updated_task = make_task(task_id=5, executor_ids="42", status_name="Закрыта")
        updated_response = {"Tasks": [updated_task], "Statuses": []}

        # lifetime: событие смены статуса (нет комментария, есть StatusId)
        lifetime = [make_lifetime_event(
            date="2025-06-01 13:30:00",
            comments="",
            status_id=3,
        )]

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response_empty, updated_response]), \
             patch("app.services.worker.get_task_lifetime", new_callable=AsyncMock,
                   return_value=lifetime):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_awaited_once()
        payload = json.loads(mock_redis.publish.call_args[0][1])
        assert payload["event_type"] == "status_change"
        assert "Закрыта" in payload["message"]


# ---------------------------------------------------------------------------
# БЛОК 6: process_user — обработка ошибок и Redis
# ---------------------------------------------------------------------------

class TestProcessUserErrorHandling:
    """Тесты обработки ошибок в process_user."""

    @pytest.mark.asyncio
    async def test_redis_publish_error_does_not_crash(
        self, utc_now, moscow_tz, base_web_url
    ):
        """
        Ошибка при publish в Redis не должна прерывать выполнение.
        Коммит в БД всё равно должен произойти.
        """
        import app.services.worker as worker_module

        user = make_user(last_task_id=10)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        failing_redis = AsyncMock()
        failing_redis.publish = AsyncMock(side_effect=Exception("Redis connection refused"))

        new_task = make_task(task_id=20)
        tasks_response = {"Tasks": [new_task], "Statuses": []}
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response, updated_response]):
            # Не должно бросать исключение
            await worker_module.process_user(
                user_id=123456,
                redis_client=failing_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        # БД зафиксирована
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_api_returns_none_for_updated_tasks_skips(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Если get_tasks() для обновлённых заявок вернул None — итерация пропускается.
        """
        import app.services.worker as worker_module

        user = make_user()
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        tasks_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response, None]):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_db.commit.assert_not_awaited()
        mock_redis.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_last_check_time_updated_after_successful_processing(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        После успешной обработки last_check_time должен обновиться до current_time_utc.
        """
        import app.services.worker as worker_module

        user = make_user()
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        tasks_response = {"Tasks": [], "Statuses": []}
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response, updated_response]):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        expected_time = utc_now.strftime("%Y-%m-%d %H:%M:%S")
        assert user.last_check_time == expected_time
        mock_db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# БЛОК 7: Форматы ответа API (list vs dict)
# ---------------------------------------------------------------------------

class TestApiResponseFormats:
    """Тесты на обработку разных форматов ответа API (list / dict)."""

    @pytest.mark.asyncio
    async def test_tasks_as_list_format(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Если get_tasks() вернул список (вместо dict) — задачи должны обрабатываться корректно.
        """
        import app.services.worker as worker_module

        user = make_user(last_task_id=10)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        new_task = make_task(task_id=25, status_name="Открыта")
        # Формат ответа — список
        tasks_response_list = [new_task]
        updated_response = []

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response_list, updated_response]):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_awaited_once()
        payload = json.loads(mock_redis.publish.call_args[0][1])
        assert payload["event_type"] == "new_task"
        assert payload["task_id"] == 25

    @pytest.mark.asyncio
    async def test_lifetime_as_dict_with_key(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Если lifetime_data — словарь с ключом 'TaskLifetimes', события должны читаться из него.
        """
        import app.services.worker as worker_module

        user = make_user(is_user_id=42)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=user)
        mock_db.commit = AsyncMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        tasks_response_empty = {"Tasks": [], "Statuses": []}
        updated_task = make_task(task_id=5, executor_ids="42")
        updated_response = {"Tasks": [updated_task], "Statuses": []}

        # Событие в 13:01 МСК — строго позже last_check (13:00 МСК)
        event = make_lifetime_event(date="2025-06-01 13:01:00", comments="Из словаря!")
        lifetime_dict = {"TaskLifetimes": [event]}

        semaphore = asyncio.Semaphore(5)

        with patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm), \
             patch("app.services.worker.get_tasks", new_callable=AsyncMock,
                   side_effect=[tasks_response_empty, updated_response]), \
             patch("app.services.worker.get_task_lifetime", new_callable=AsyncMock,
                   return_value=lifetime_dict):
            await worker_module.process_user(
                user_id=123456,
                redis_client=mock_redis,
                base_web_url=base_web_url,
                semaphore=semaphore,
                current_time_utc=utc_now,
                intraservice_tz=moscow_tz,
            )

        mock_redis.publish.assert_awaited_once()
        payload = json.loads(mock_redis.publish.call_args[0][1])
        assert "Из словаря!" in payload["message"]
