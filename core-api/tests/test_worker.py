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
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def make_user(
    tg_user_id: int = 123456,
    is_user_id: int = 42,
    is_password_b64: str = "dXNlcjpwYXNz",
    last_task_id: int = 10,
    last_check_time: str | None = "2025-06-01 10:00:00",
    is_login: str = "test_user",
) -> MagicMock:
    """Создаёт мок объекта пользователя БД."""
    user = MagicMock()
    user.tg_user_id = tg_user_id
    user.is_user_id = is_user_id
    user.is_password_b64 = is_password_b64
    user.last_task_id = last_task_id
    user.last_check_time = last_check_time
    user.is_login = is_login
    return user


def make_task(
    task_id: int = 20,
    name: str = "Тестовая заявка",
    status_name: str | None = "Открыта",
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
        with (
            patch("app.services.worker.settings") as mock_settings,
            patch("app.services.worker.aioredis.from_url") as mock_from_url,
        ):
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
    async def test_user_not_found_in_db(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
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
    async def test_user_missing_auth_b64(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
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

        new_task = make_task(
            task_id=20, name="Принтер не работает", status_name="Открыта"
        )
        tasks_response = {"Tasks": [new_task], "Statuses": []}
        # Обновлённые заявки — пустые (нет обновлений)
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response, updated_response],
            ),
        ):
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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response, updated_response],
            ),
        ):
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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response, updated_response],
            ),
        ):
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
        tasks_response = {"Tasks": [task], "Statuses": [{"Id": 5, "Name": "В работе"}]}
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response, updated_response],
            ),
        ):
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
        assert "В работе" in payload["text"]

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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response, updated_response],
            ),
        ):
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
        assert "N/A" in payload["text"]


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
        lifetime = [
            make_lifetime_event(date="2025-06-01 13:01:00", comments="Всё починено!")
        ]

        semaphore = asyncio.Semaphore(5)

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response_empty, updated_response],
            ),
            patch(
                "app.services.worker.get_task_lifetime",
                new_callable=AsyncMock,
                return_value=lifetime,
            ),
        ):
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
        assert "Всё починено!" in payload["text"]

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
        lifetime = [
            make_lifetime_event(
                date="2025-06-01 12:59:00", comments="Старый комментарий"
            )
        ]

        semaphore = asyncio.Semaphore(5)

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response_empty, updated_response],
            ),
            patch(
                "app.services.worker.get_task_lifetime",
                new_callable=AsyncMock,
                return_value=lifetime,
            ),
        ):
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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response_empty, updated_response],
            ),
            patch(
                "app.services.worker.get_task_lifetime", new_callable=AsyncMock
            ) as mock_lifetime,
        ):
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
        lifetime = [
            make_lifetime_event(
                date="2025-06-01 13:30:00",
                comments="",
                status_id=3,
            )
        ]

        semaphore = asyncio.Semaphore(5)

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response_empty, updated_response],
            ),
            patch(
                "app.services.worker.get_task_lifetime",
                new_callable=AsyncMock,
                return_value=lifetime,
            ),
        ):
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
        assert "Закрыта" in payload["text"]


# ---------------------------------------------------------------------------
# БЛОК 5.5: process_user — назначение исполнителя
# ---------------------------------------------------------------------------


class TestProcessUserExecutorAssigned:
    """Тесты обработки назначения исполнителя."""

    @pytest.mark.asyncio
    async def test_executor_assigned_published(
        self, mock_redis, utc_now, moscow_tz, base_web_url
    ):
        """
        Событие назначения исполнителя (Executors) должно публиковаться.
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
        updated_task = make_task(
            task_id=5, executor_ids="42", status_name="Открыта", status_id=31
        )
        updated_response = {"Tasks": [updated_task], "Statuses": []}

        # lifetime: событие смены исполнителя (Executors)
        lifetime = [
            {
                "Date": "2025-06-01 13:30:00",
                "EditorId": 8664,
                "Editor": "Беликов Ален",
                "Executors": "belikov IntraTest",
            }
        ]

        semaphore = asyncio.Semaphore(5)

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response_empty, updated_response],
            ),
            patch(
                "app.services.worker.get_task_lifetime",
                new_callable=AsyncMock,
                return_value=lifetime,
            ),
        ):
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
        assert payload["event_type"] == "executor_assigned"
        assert "belikov IntraTest" in payload["text"]
        assert payload["status_id"] == 31


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
        failing_redis.publish = AsyncMock(
            side_effect=Exception("Redis connection refused")
        )

        new_task = make_task(task_id=20)
        tasks_response = {"Tasks": [new_task], "Statuses": []}
        updated_response = {"Tasks": [], "Statuses": []}

        semaphore = asyncio.Semaphore(5)

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response, updated_response],
            ),
        ):
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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response, None],
            ),
        ):
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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response, updated_response],
            ),
        ):
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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response_list, updated_response],
            ),
        ):
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

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
            patch(
                "app.services.worker.get_tasks",
                new_callable=AsyncMock,
                side_effect=[tasks_response_empty, updated_response],
            ),
            patch(
                "app.services.worker.get_task_lifetime",
                new_callable=AsyncMock,
                return_value=lifetime_dict,
            ),
        ):
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
        assert "Из словаря!" in payload["text"]


# ---------------------------------------------------------------------------
# БЛОК 8: check_updates с сервисным аккаунтом
# ---------------------------------------------------------------------------


class TestCheckUpdatesServiceUser:
    """Тесты check_updates для работы с сервисным аккаунтом."""

    @pytest.mark.asyncio
    @patch("app.services.worker.get_redis_client")
    @patch("app.services.worker.settings")
    @patch("app.services.worker.get_tasks", new_callable=AsyncMock)
    async def test_check_updates_with_service_user(
        self, mock_get_tasks, mock_settings, mock_get_redis, moscow_tz, utc_now
    ):
        """
        Проверяет, что check_updates корректно считывает и обрабатывает задачи,
        назначенные на сервисный аккаунт, генерируя событие с tg_user_id = None.
        """
        import app.services.worker as worker_module

        # Настраиваем мок настроек
        mock_settings.INTRASERVICE_SERVICE_USER_ID = 9999
        mock_settings.INTRASERVICE_SERVICE_LOGIN = "intratest"
        mock_settings.INTRASERVICE_SERVICE_PASSWORD = "password"
        mock_settings.INTRASERVICE_TZ = "Europe/Moscow"
        mock_settings.MAX_CONCURRENT_REQUESTS = 5
        mock_settings.POLLING_INTERVAL = 30
        mock_settings.INTRASERVICE_URL = "http://intraservice.test/api/"

        # Настраиваем мок Redis
        mock_redis_client = AsyncMock()
        # имитируем, что last_check_time уже задан
        # и service_last_task_id = 100
        mock_redis_client.get.side_effect = lambda key: {
            "worker:last_check_time": "2025-06-01 10:00:00",
            "worker:service_last_task_id": "100",
        }.get(key)
        mock_get_redis.return_value = mock_redis_client

        # Настраиваем мок сессии базы данных (пустая БД, нет обычных пользователей)
        mock_db = AsyncMock()
        mock_db_result = MagicMock()
        mock_db_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_db_result)

        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)

        # Настраиваем мок API IntraService
        # Новая задача #105 назначена на исполнителя 9999
        new_task = make_task(task_id=105, executor_ids="9999", name="Сервисный принтер")
        mock_get_tasks.side_effect = [
            {"Tasks": [new_task], "Statuses": []},  # new tasks
            {"Tasks": [], "Statuses": []},  # updated tasks
        ]

        with (
            patch("app.services.worker.AsyncSessionLocal", return_value=mock_db_cm),
        ):
            await worker_module.check_updates()

        # Проверяем, что событие ушло в Redis с правильными полями
        mock_redis_client.publish.assert_awaited_once()
        call_args = mock_redis_client.publish.call_args
        channel = call_args[0][0]
        payload = json.loads(call_args[0][1])

        assert channel == "intraservice_events"
        assert payload["event_type"] == "new_task"
        assert payload["tg_user_id"] is None
        assert payload["is_user_id"] == 9999
        assert payload["is_login"] == "intratest"
        assert payload["task_id"] == 105

        assert payload["task_id"] == 105

        # Проверяем, что service_last_task_id обновился в Redis
        mock_redis_client.set.assert_any_call("worker:service_last_task_id", 105)


# ---------------------------------------------------------------------------
# БЛОК 9: Тесты check_waiting_printer_tasks (умное возобновление задач)
# ---------------------------------------------------------------------------


class TestCheckWaitingPrinterTasks:
    """Тесты функции check_waiting_printer_tasks."""

    @pytest.mark.asyncio
    @patch("app.services.worker.get_tasks_by_status", new_callable=AsyncMock)
    async def test_no_tasks(self, mock_get_tasks):
        """Если API вернул пустой список задач — ничего не делаем."""
        import app.services.worker as worker_module

        mock_get_tasks.return_value = []
        redis = AsyncMock()
        semaphore = asyncio.Semaphore(5)
        users_by_is_id = {"9999": worker_module.VirtualServiceUser(9999, "intratest")}

        await worker_module.check_waiting_printer_tasks(
            "mock_auth", redis, semaphore, users_by_is_id
        )

        mock_get_tasks.assert_awaited_once_with("mock_auth", 35)
        redis.get.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.services.worker.get_tasks_by_status", new_callable=AsyncMock)
    async def test_already_processed(self, mock_get_tasks):
        """Если задача уже обработана (есть в Redis) — пропускаем."""
        import app.services.worker as worker_module

        task = make_task(task_id=123, executor_ids="9999")
        mock_get_tasks.return_value = [task]

        redis = AsyncMock()
        redis.get = AsyncMock(return_value="2026-06-17 12:00:00")
        semaphore = asyncio.Semaphore(5)
        users_by_is_id = {"9999": worker_module.VirtualServiceUser(9999, "intratest")}

        await worker_module.check_waiting_printer_tasks(
            "mock_auth", redis, semaphore, users_by_is_id
        )

        redis.get.assert_awaited_once_with("printer_resumed:123")
        redis.set.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.services.worker.get_tasks_by_status", new_callable=AsyncMock)
    async def test_last_comment_by_service(self, mock_get_tasks):
        """Если последний комментарий оставлен сервисным аккаунтом — пропускаем."""
        import app.services.worker as worker_module

        task = make_task(task_id=123, executor_ids="9999")
        mock_get_tasks.return_value = [task]

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)

        # Последний комментарий от сервисного аккаунта (EditorId = 9891)
        lifetime_event = make_lifetime_event(
            date="2026-06-17 12:00:00", comments="Не вижу компьютер"
        )
        lifetime_event["EditorId"] = 9891  # settings.INTRASERVICE_SERVICE_USER_ID
        lifetime_event["Editor"] = "intratest"

        semaphore = asyncio.Semaphore(5)
        users_by_is_id = {"9999": worker_module.VirtualServiceUser(9999, "intratest")}

        with (
            patch(
                "app.services.worker.get_task_comments",
                new_callable=AsyncMock,
                return_value={"TaskLifetimes": [lifetime_event]},
            ),
            patch("app.services.worker.settings") as mock_settings,
        ):
            mock_settings.INTRASERVICE_SERVICE_USER_ID = 9891
            mock_settings.INTRASERVICE_SERVICE_LOGIN = "intratest"
            mock_settings.STATUS_WAITING_ID = 35

            await worker_module.check_waiting_printer_tasks(
                "mock_auth", redis, semaphore, users_by_is_id
            )

        redis.get.assert_awaited_once_with("printer_resumed:123")
        redis.set.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.services.worker.get_tasks_by_status", new_callable=AsyncMock)
    async def test_success_with_ip_and_pc(self, mock_get_tasks):
        """Если пользователь написал IP и имя ПК (смешанный язык/кириллица) — обновляем поля и статус."""
        import app.services.worker as worker_module

        task = make_task(task_id=123, executor_ids="9999")
        mock_get_tasks.return_value = [task]

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)

        # Комментарий пользователя с кириллицей и IP
        lifetime_event = make_lifetime_event(
            date="2026-06-17 12:00:00",
            comments="Компьютер КЗМ1234, а айпи принтера 10.244.15.55",
        )
        lifetime_event["EditorId"] = 1111  # обычный пользователь
        lifetime_event["Editor"] = "Иванов Иван"

        semaphore = asyncio.Semaphore(5)
        users_by_is_id = {"9999": worker_module.VirtualServiceUser(9999, "intratest")}

        with (
            patch(
                "app.services.worker.get_task_comments",
                new_callable=AsyncMock,
                return_value={"TaskLifetimes": [lifetime_event]},
            ),
            patch(
                "app.services.worker.update_task_custom_fields",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update_fields,
            patch(
                "app.services.worker.update_task_status",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update_status,
            patch("app.services.worker.settings") as mock_settings,
        ):
            mock_settings.INTRASERVICE_SERVICE_USER_ID = 9891
            mock_settings.INTRASERVICE_SERVICE_LOGIN = "intratest"
            mock_settings.STATUS_WAITING_ID = 35
            mock_settings.STATUS_OPEN_ID = 31
            mock_settings.PRINTER_PC_CUSTOM_FIELD_ID = 1112
            mock_settings.PRINTER_IP_CUSTOM_FIELD_ID = 1103

            await worker_module.check_waiting_printer_tasks(
                "mock_auth", redis, semaphore, users_by_is_id
            )

        # Проверяем, что поля извлечены и транслитерированы (КЗМ1234 -> KZM1234)
        mock_update_fields.assert_awaited_once_with(
            "mock_auth",
            123,
            [
                {"FieldId": 1103, "Value": "10.244.15.55"},
                {"FieldId": 1112, "Value": "KZM1234"},
            ],
        )
        mock_update_status.assert_awaited_once_with("mock_auth", 123, 31)
        redis.set.assert_awaited_once()
        assert "printer_resumed:123" in redis.set.call_args[0][0]
