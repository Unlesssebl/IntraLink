import os
import sys
import pytest
import json
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

# Добавляем путь к printer-worker в sys.path, чтобы избежать проблем с импортами в IDE
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Устанавливаем переменные окружения до импорта
os.environ["BOT_API_KEY"] = "dummy_key"
os.environ["REDIS_URL"] = "redis://localhost:6379"


from orchestrator.schemas import PrintJob, JobState, ConnectionType, PrinterDriverInfo
from worker_services.redis_listener import (
    save_job_state,
    load_job_state,
    publish_approval_request,
    _process_approval_response,
    _process_manual_trigger,
    _process_event,
    _recover_orphan_jobs,
    start_redis_listener,
)


@pytest.fixture
def mock_redis():
    mock_client = AsyncMock()
    # Mocking standard redis methods
    mock_client.set = AsyncMock()
    mock_client.get = AsyncMock()
    mock_client.zadd = AsyncMock()
    mock_client.zremrangebyrank = AsyncMock()
    mock_client.publish = AsyncMock()

    async def mock_scan_iter(match=None):
        for key in [b"printer_job:111"]:
            yield key

    mock_client.scan_iter = mock_scan_iter

    with patch("worker_services.redis_listener.get_redis", return_value=mock_client):
        yield mock_client


@pytest.mark.asyncio
async def test_save_and_load_job_state(mock_redis):
    job = PrintJob(
        task_id=111,
        tg_user_id=222,
        raw_text="test job",
        state=JobState.PENDING,
        target_pc="PC-TEST",
        model_key="kyocera_ecosys_m2040dn",
    )

    mock_redis.get.return_value = job.model_dump_json()

    await save_job_state(job)
    mock_redis.set.assert_called_once()
    mock_redis.zadd.assert_called_once()
    mock_redis.zremrangebyrank.assert_called_once()

    loaded = await load_job_state(111)
    assert loaded is not None
    assert loaded.task_id == 111
    assert loaded.state == JobState.PENDING
    mock_redis.get.assert_called_once_with("printer_job:111")


@pytest.mark.asyncio
async def test_publish_approval_request(mock_redis):
    job = PrintJob(
        task_id=111,
        tg_user_id=222,
        raw_text="test job",
        state=JobState.WAITING_APPROVAL,
        target_pc="PC-TEST",
        model_key="kyocera_ecosys_m2040dn",
        connection_type=ConnectionType.TCPIP,
        driver_info=PrinterDriverInfo(
            model_key="kyocera_ecosys_m2040dn",
            display_name="Kyocera ECOSYS M2040dn",
            driver_name="Kyocera ECOSYS M2040dn KX",
            driver_inf_path="\\\\srv\\share\\m2040.inf",
            vendor="Kyocera",
            connection_type=ConnectionType.TCPIP,
        ),
    )

    await publish_approval_request(job)
    mock_redis.publish.assert_called_once()
    args = mock_redis.publish.call_args[0]
    assert args[0] == "printer_actions"
    payload = json.loads(args[1])
    assert payload["event_type"] == "printer_approval_request"
    assert payload["task_id"] == 111
    assert payload["driver_name"] == "Kyocera ECOSYS M2040dn"


@pytest.mark.asyncio
async def test_process_approval_response_approve(mock_redis):
    job = PrintJob(
        task_id=111,
        tg_user_id=222,
        raw_text="test job",
        state=JobState.WAITING_APPROVAL,
    )
    mock_redis.get.return_value = job.model_dump_json()

    mock_orch = AsyncMock()
    with patch("worker_main.get_orchestrator", return_value=mock_orch):
        payload = {"task_id": 111, "action": "approve", "tg_user_id": 222}
        await _process_approval_response(payload)
        mock_orch.run.assert_called_once()


@pytest.mark.asyncio
async def test_process_approval_response_reject(mock_redis):
    job = PrintJob(
        task_id=111,
        tg_user_id=222,
        raw_text="test job",
        state=JobState.WAITING_APPROVAL,
    )
    mock_redis.get.return_value = job.model_dump_json()

    mock_orch = AsyncMock()
    with patch("worker_main.get_orchestrator", return_value=mock_orch):
        payload = {"task_id": 111, "action": "reject", "tg_user_id": 222}
        await _process_approval_response(payload)
        mock_orch.handle_failure.assert_called_once()


@pytest.mark.asyncio
async def test_process_manual_trigger(mock_redis):
    mock_orch = AsyncMock()
    mock_orch.kb = MagicMock()
    mock_orch.kb.find_by_key.return_value = None

    with patch("worker_main.get_orchestrator", return_value=mock_orch):
        payload = {
            "task_id": 111,
            "tg_user_id": 222,
            "target_pc": "PC-TEST",
            "model_key": "kyocera_ecosys_m2040dn",
            "connection_type": "tcpip",
        }
        await _process_manual_trigger(payload)
        mock_orch.run.assert_called_once()
        mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_process_event_with_custom_fields(mock_redis):
    payload = {
        "event_type": "new_task",
        "task_id": 111,
        "tg_user_id": 222,
        "task_data": {
            "Name": "Install printer",
            "Description": "Kyocera",
            "Field1112": "PC-TEST-111",
            "Field1103": "kyocera_ecosys_m2040dn",
        },
    }

    mock_orch = AsyncMock()
    with patch("worker_main.get_orchestrator", return_value=mock_orch):
        await _process_event(payload)
        mock_orch.run.assert_called_once()
        job_run = mock_orch.run.call_args[0][0]
        assert job_run.target_pc == "PC-TEST-111"
        assert job_run.model_key == "kyocera_ecosys_m2040dn"


@pytest.mark.asyncio
async def test_process_event_fallback_request(mock_redis):
    payload = {
        "event_type": "new_task",
        "task_id": 111,
        "tg_user_id": 222,
        # No task_data, forces fallback HTTP request
    }

    mock_orch = AsyncMock()
    mock_details = {
        "Task": {
            "Name": "Install printer",
            "Description": "Kyocera",
            "Field1112": "PC-TEST-HTTP",
            "Field1103": "kyocera_ecosys_m2040dn",
        }
    }

    with (
        patch("worker_main.get_orchestrator", return_value=mock_orch),
        patch(
            "worker_services.redis_listener.get_task_details",
            new_callable=AsyncMock,
            return_value=mock_details,
        ) as mock_get_details,
    ):
        await _process_event(payload)
        mock_get_details.assert_called_once_with(222, 111)
        mock_orch.run.assert_called_once()
        job_run = mock_orch.run.call_args[0][0]
        assert job_run.target_pc == "PC-TEST-HTTP"


@pytest.mark.asyncio
async def test_process_event_xml_fallback(mock_redis):
    payload = {
        "event_type": "new_task",
        "task_id": 111,
        "tg_user_id": 222,
        "task_data": {
            "Name": "Install printer",
            "Data": '<fields><field id="1112">PC-XML-TEST</field><field id="1103">kyocera_ecosys_m2040dn</field></fields>',
        },
    }

    mock_orch = AsyncMock()
    with patch("worker_main.get_orchestrator", return_value=mock_orch):
        await _process_event(payload)
        mock_orch.run.assert_called_once()
        job_run = mock_orch.run.call_args[0][0]
        assert job_run.target_pc == "PC-XML-TEST"
        assert job_run.model_key == "kyocera_ecosys_m2040dn"


@pytest.mark.asyncio
async def test_recover_orphan_jobs(mock_redis):
    # Setup scanning keys in redis (handled by mock_redis fixture)

    job = PrintJob(
        task_id=111,
        tg_user_id=222,
        raw_text="Orphan job",
        state=JobState.PROBING,
        target_pc="PC-ORPHAN",
    )
    mock_redis.get.return_value = job.model_dump_json()

    mock_wmi = MagicMock()
    mock_wmi.disable_winrm = AsyncMock()

    with (
        patch(
            "worker_services.credentials.get_domain_credentials",
            new_callable=AsyncMock,
            return_value=("domain", "user", "pass"),
        ),
        patch("executors.wmi_executor.WMIExecutor", return_value=mock_wmi),
        patch(
            "worker_services.action_executor.execute_action", new_callable=AsyncMock
        ) as mock_action,
    ):
        await _recover_orphan_jobs()
        mock_wmi.disable_winrm.assert_called_once()
        mock_action.assert_called_once()
        # State should be updated to FAILED in redis
        mock_redis.set.assert_called_once()
        saved_job_str = mock_redis.set.call_args[0][1]
        saved_job = PrintJob.model_validate_json(saved_job_str)
        assert saved_job.state == JobState.FAILED


@pytest.mark.asyncio
async def test_start_redis_listener_shutdown():
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    # Raise CancelledError to end loop immediately
    mock_pubsub.get_message = AsyncMock(side_effect=asyncio.CancelledError)

    mock_redis_inst = AsyncMock()

    mock_pubsub_mgr = MagicMock()
    mock_pubsub_mgr.__aenter__ = AsyncMock(return_value=mock_pubsub)
    mock_pubsub_mgr.__aexit__ = AsyncMock(return_value=None)
    mock_redis_inst.pubsub = MagicMock(return_value=mock_pubsub_mgr)

    with (
        patch(
            "worker_services.redis_listener._recover_orphan_jobs",
            new_callable=AsyncMock,
        ) as mock_recover,
        patch(
            "worker_services.redis_listener.aioredis.from_url",
            return_value=mock_redis_inst,
        ),
    ):
        # Оборачиваем в wait_for, чтобы тест гарантированно завершился при зависании
        try:
            await asyncio.wait_for(start_redis_listener(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("start_redis_listener hung and timed out")
        mock_recover.assert_called_once()
        mock_pubsub.subscribe.assert_called_once_with(
            "printer_actions", "ai_validated_events"
        )





@pytest.mark.asyncio
async def test_process_event_deduplication(mock_redis):
    # Тест дедупликации: если задача уже обрабатывается, событие пропускается
    payload = {
        "event_type": "new_task",
        "task_id": 111,
        "tg_user_id": 222,
        "task_data": {
            "Name": "Install printer",
            "Field1112": "PC-TEST-111",
            "Field1103": "kyocera_ecosys_m2040dn",
        },
    }
    mock_orch = AsyncMock()
    from worker_services.redis_listener import _active_tasks

    _active_tasks.add(111)
    try:
        with patch("worker_main.get_orchestrator", return_value=mock_orch):
            await _process_event(payload)
            mock_orch.run.assert_not_called()
    finally:
        _active_tasks.discard(111)


@pytest.mark.asyncio
async def test_process_approval_response_state_lost(mock_redis):
    # Если задача не найдена в Redis, должно вызваться действие on_state_lost
    mock_redis.get.return_value = None
    payload = {"task_id": 111, "action": "approve", "tg_user_id": 222}

    with patch(
        "worker_services.action_executor.execute_action", new_callable=AsyncMock
    ) as mock_action:
        await _process_approval_response(payload)
        mock_action.assert_called_once()
        assert mock_action.call_args[0][0] == "on_state_lost"
        dummy_job = mock_action.call_args[0][1]
        assert dummy_job.task_id == 111
        assert dummy_job.state == JobState.FAILED


@pytest.mark.asyncio
async def test_recover_orphan_jobs_waiting_approval(mock_redis):
    job = PrintJob(
        task_id=111,
        tg_user_id=222,
        raw_text="Orphan job waiting approval",
        state=JobState.WAITING_APPROVAL,
    )
    mock_redis.get.return_value = job.model_dump_json()

    with patch(
        "worker_services.action_executor.execute_action", new_callable=AsyncMock
    ) as mock_action:
        await _recover_orphan_jobs()
        mock_action.assert_called_once()
        assert mock_action.call_args[0][0] == "on_orphan_recovered"
        mock_redis.set.assert_called_once()
        saved_job = PrintJob.model_validate_json(mock_redis.set.call_args[0][1])
        assert saved_job.state == JobState.FAILED
        assert "Подтверждение" in saved_job.error_message


@pytest.mark.asyncio
async def test_start_redis_listener_message_routing():
    # Наш кастомный mock_pubsub вернет одно сообщение и выбросит CancelledError на второй итерации
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()

    messages = [
        {
            "type": "message",
            "data": json.dumps(
                {"event_type": "approval_response", "task_id": 111, "action": "approve"}
            ),
        },
        asyncio.CancelledError(),
    ]

    async def mock_get_message(*args, **kwargs):
        if not messages:
            await asyncio.sleep(1)
            return None
        val = messages.pop(0)
        if isinstance(val, BaseException):
            raise val
        return val

    mock_pubsub.get_message = mock_get_message

    mock_redis_inst = AsyncMock()
    mock_pubsub_mgr = MagicMock()
    mock_pubsub_mgr.__aenter__ = AsyncMock(return_value=mock_pubsub)
    mock_pubsub_mgr.__aexit__ = AsyncMock(return_value=None)
    mock_redis_inst.pubsub = MagicMock(return_value=mock_pubsub_mgr)

    with (
        patch(
            "worker_services.redis_listener._recover_orphan_jobs",
            new_callable=AsyncMock,
        ) as mock_recover,
        patch(
            "worker_services.redis_listener.aioredis.from_url",
            return_value=mock_redis_inst,
        ),
        patch(
            "worker_services.redis_listener._process_approval_response",
            new_callable=AsyncMock,
        ) as mock_process_approval,
    ):
        try:
            await asyncio.wait_for(start_redis_listener(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("start_redis_listener hung and timed out")

        mock_recover.assert_called_once()
        mock_pubsub.subscribe.assert_called_once()
        mock_process_approval.assert_called_once_with(
            {"event_type": "approval_response", "task_id": 111, "action": "approve"}
        )


@pytest.mark.asyncio
async def test_process_approval_response_update(mock_redis):
    job = PrintJob(
        task_id=111,
        tg_user_id=222,
        raw_text="test job",
        state=JobState.WAITING_APPROVAL,
        target_pc="OLD-PC",
        model_key="kyocera_ecosys_m2040dn",
        connection_type=ConnectionType.USB,
    )
    mock_redis.get.return_value = job.model_dump_json()

    mock_orch = AsyncMock()
    mock_orch.kb = MagicMock()
    mock_orch.kb.find_by_key.return_value = None

    with patch("worker_main.get_orchestrator", return_value=mock_orch):
        payload = {
            "task_id": 111,
            "action": "update",
            "tg_user_id": 222,
            "target_pc": "NEW-PC",
            "model_key": "hp_laserjet_1020",
            "connection_type": "tcpip",
        }
        await _process_approval_response(payload)

        # Проверяем, что оркестратор был запущен с обновленным job
        mock_orch.run.assert_called_once()
        updated_job = mock_orch.run.call_args[0][0]
        assert updated_job.target_pc == "NEW-PC"
        assert updated_job.model_key == "hp_laserjet_1020"
        assert updated_job.connection_type == ConnectionType.TCPIP

        # Проверяем, что обновленный стейт сохранился
        mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_process_manual_trigger_missing_data(mock_redis):
    # Нет task_id
    payload_no_id = {"tg_user_id": 222}
    await _process_manual_trigger(payload_no_id)

    # Нет target_pc или model_key
    payload_missing = {"task_id": 111, "tg_user_id": 222, "target_pc": "PC"}
    mock_orch = AsyncMock()
    with patch("worker_main.get_orchestrator", return_value=mock_orch):
        await _process_manual_trigger(payload_missing)
        mock_orch.run.assert_not_called()


@pytest.mark.asyncio
async def test_process_event_fallback_failure(mock_redis):
    payload = {
        "event_type": "new_task",
        "task_id": 111,
        "tg_user_id": 222,
        # Нет task_data, вынуждает делать fallback запрос
    }

    mock_orch = AsyncMock()
    # Возвращаем пустой ответ
    with (
        patch("worker_main.get_orchestrator", return_value=mock_orch),
        patch(
            "worker_services.redis_listener.get_task_details",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_get_details,
    ):
        await _process_event(payload)
        mock_get_details.assert_called_once_with(222, 111)
        mock_orch.run.assert_not_called()


@pytest.mark.asyncio
async def test_start_redis_listener_invalid_json():
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()

    # Сначала шлем невалидный JSON, затем не строку, затем CancelledError
    messages = [
        {"type": "message", "data": "invalid json {{"},
        {"type": "message", "data": 12345},  # не строка
        asyncio.CancelledError(),
    ]

    async def mock_get_message(*args, **kwargs):
        if not messages:
            await asyncio.sleep(1)
            return None
        val = messages.pop(0)
        if isinstance(val, BaseException):
            raise val
        return val

    mock_pubsub.get_message = mock_get_message
    mock_redis_inst = AsyncMock()
    mock_pubsub_mgr = MagicMock()
    mock_pubsub_mgr.__aenter__ = AsyncMock(return_value=mock_pubsub)
    mock_pubsub_mgr.__aexit__ = AsyncMock(return_value=None)
    mock_redis_inst.pubsub = MagicMock(return_value=mock_pubsub_mgr)

    with (
        patch(
            "worker_services.redis_listener._recover_orphan_jobs",
            new_callable=AsyncMock,
        ),
        patch(
            "worker_services.redis_listener.aioredis.from_url",
            return_value=mock_redis_inst,
        ),
    ):
        try:
            await asyncio.wait_for(start_redis_listener(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("start_redis_listener hung and timed out")


def test_get_redis_initialization():
    # Тест реального вызова get_redis (без патча)
    import worker_services.redis_listener as rl

    # Сохраняем оригинальное значение
    orig_client = rl._redis_client
    rl._redis_client = None
    try:
        with patch("worker_services.redis_listener.aioredis.from_url") as mock_from_url:
            r = rl.get_redis()
            mock_from_url.assert_called_once_with(rl.REDIS_URL, decode_responses=True)
            assert r == mock_from_url.return_value
            # Повторный вызов должен вернуть тот же клиент без повторного from_url
            r2 = rl.get_redis()
            assert r2 == r
            mock_from_url.assert_called_once()
    finally:
        rl._redis_client = orig_client


@pytest.mark.asyncio
async def test_process_event_new_task_routing(mock_redis):
    from worker_services.redis_listener import _process_event
    payload = {
        "event_type": "new_task",
        "task_id": 123,
        "tg_user_id": 456,
        "task_data": {
            "Name": "Install printer",
            "Field1112": "PC-123",
            "Field1103": "kyocera_ecosys_m2040dn"
        }
    }
    
    mock_orch = AsyncMock()
    with patch("worker_main.get_orchestrator", return_value=mock_orch):
        # 1. Сырое событие из общего канала -> Игнорируется
        await _process_event(payload, channel="intraservice_events")
        mock_orch.run.assert_not_called()
        
        # 2. Проверенное событие из валидированного канала -> Обрабатывается
        await _process_event(payload, channel="ai_validated_events")
        mock_orch.run.assert_called_once()

