"""Comprehensive unit and integration tests for Triage, Relevance Gateway, Anti-Loop Guard, and Optimistic Lock."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database.base import Base
from core.database.models import TriageAudit
from core.intraservice.dto import TaskDTO
from core.triage.gateway import RelevanceGateway
from core.autopilot.dialogue import AntiLoopGuard
from core.intraservice.auth import (
    ServiceAuthBootstrap,
    ServiceAuthCredentials,
)
from worker.src.tasks.triage import (
    set_triage_client,
    set_triage_policy_service,
    set_triage_redis_client,
    set_triage_registry,
    set_triage_service_auth,
    set_triage_session_factory,
    triage_task,
)


@pytest.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    set_triage_session_factory(session_factory)
    yield session_factory

    set_triage_session_factory(None)
    await engine.dispose()


@pytest.fixture
def mock_service_auth():
    auth_bootstrap = AsyncMock(spec=ServiceAuthBootstrap)
    auth_bootstrap.bootstrap_auth.return_value = ServiceAuthCredentials(
        login="bot_user",
        auth_b64="Ym90X3VzZXI6c2VjcmV0",
        bot_user_id=9999,
        created_at=datetime.now(timezone.utc),
    )
    set_triage_service_auth(auth_bootstrap)
    set_triage_redis_client(AsyncMock())
    yield auth_bootstrap
    set_triage_service_auth(None)
    set_triage_redis_client(None)


@pytest.fixture
def cleanup_triage_hooks():
    yield
    set_triage_client(None)
    set_triage_session_factory(None)
    set_triage_service_auth(None)
    set_triage_redis_client(None)
    set_triage_registry(None)
    set_triage_policy_service(None)


# ==============================================================================
# 1. Anti-Loop Guard Tests
# ==============================================================================


def test_anti_loop_auto_reply_detection():
    guard = AntiLoopGuard()

    # English auto-replies and bounces
    assert guard.is_auto_reply("Out of Office: I am away until next Monday") is True
    assert guard.is_auto_reply("Automatic reply: Thanks for reaching out", subject="Auto-reply") is True
    assert guard.is_auto_reply("Delivery Status Notification (Failure): undelivered mail") is True
    assert guard.is_auto_reply("I will be away on business trip") is True

    # Russian auto-replies
    assert guard.is_auto_reply("Автоматический ответ: нахожусь в отпуске до 10 октября") is True
    assert guard.is_auto_reply("Отсутствую на рабочем месте. По срочным вопросам звоните 11-22") is True
    assert guard.is_auto_reply("Уведомление о недоставке: сообщение не доставлено получателю") is True
    assert guard.is_auto_reply("Письмо сгенерировано автоматически") is True

    # Legitimate user messages
    assert guard.is_auto_reply("Прошу настроить принтер на рабочем месте WKS-1020") is False
    assert guard.is_auto_reply("Не запускается 1С, пишет ошибку блокировки") is False


def test_anti_loop_bot_author_filter():
    guard = AntiLoopGuard()

    # Bot's own messages
    assert guard.is_bot_author(author_id=9999, bot_user_id=9999) is True
    assert guard.is_bot_author(author_id="9999", bot_user_id=9999) is True

    # User messages
    assert guard.is_bot_author(author_id=105, bot_user_id=9999) is False
    assert guard.is_bot_author(author_id=None, bot_user_id=9999) is False


def test_anti_loop_clarification_rounds_limit():
    guard = AntiLoopGuard(max_clarification_rounds=2)

    # 1 question round from bot
    events_1 = [
        {"UserId": 9999, "Text": "Пожалуйста, укажите IP-адрес принтера?", "IsPrivate": False},
        {"UserId": 105, "Text": "Он на столе стоит", "IsPrivate": False},
    ]
    assert guard.count_clarification_rounds(events_1, bot_user_id=9999) == 1
    assert guard.check_clarification_limit(events_1, bot_user_id=9999) is False

    # 2 questions from bot (limit reached)
    events_2 = [
        {"UserId": 9999, "Text": "Уточните IP адрес?", "IsPrivate": False},
        {"UserId": 105, "Text": "Не знаю", "IsPrivate": False},
        {"UserId": 9999, "Text": "Посмотрите на наклейку на задней панели?", "IsPrivate": False},
    ]
    assert guard.count_clarification_rounds(events_2, bot_user_id=9999) == 2
    assert guard.check_clarification_limit(events_2, bot_user_id=9999) is True


# ==============================================================================
# 2. Relevance Gateway Tests (Deterministic Rules)
# ==============================================================================


def test_relevance_gateway_eds_detection():
    gateway = RelevanceGateway()

    # Case 1: EDS certificate in Section 02 (Software) -> Rejection
    task_eds_in_software = TaskDTO(
        Id=101,
        Name="Не видит сертификат ЭЦП КриптоПро в СБИС",
        Description="Установите пожалуйста новую электронную подпись на ПК",
        ServiceId=18,
    )
    decision = gateway.evaluate(task_eds_in_software)
    assert decision.is_irrelevant is True
    assert decision.target_service_id == 24
    assert "09. Электронная цифровая подпись" in decision.target_service_name
    assert "эцп" in [m.lower() for m in decision.matched_markers]
    assert "https://servicedesk-pub.corporate.loc/Task/Create?serviceid=24" in decision.public_comment

    # Case 2: Bank-client in Section 11 (General) -> Rejection
    task_bank = TaskDTO(
        Id=102,
        Name="Сбербанк бизнес не видит токен рутокен",
        Description="Продлили ключ, нужно обновить сертификат",
        ServiceId=16,
    )
    decision_bank = gateway.evaluate(task_bank)
    assert decision_bank.is_irrelevant is True
    assert decision_bank.target_service_id == 24

    # Case 3: EDS already in Section 09 -> Valid / Eligible (not cancelled)
    task_eds_in_proper_root = TaskDTO(
        Id=103,
        Name="Продление сертификата ЭЦП директора",
        Description="Заявка в профильный раздел",
        ServiceId=24,
    )
    assert gateway.evaluate(task_eds_in_proper_root).is_irrelevant is False

    # Case 4: SBIS mentioned alongside printer problem (Exception: Helpdesk 1st line)
    task_sbis_printer = TaskDTO(
        Id=104,
        Name="Не печатает накладные из СБИС",
        Description="Принтер HP LaserJet не реагирует, зависла очередь печати",
        ServiceId=19,
    )
    assert gateway.evaluate(task_sbis_printer).is_irrelevant is False

    # Case 5: Kontur mentioned alongside network outage (Exception: Helpdesk 1st line)
    task_kontur_network = TaskDTO(
        Id=105,
        Name="Нет сети для входа в Контур",
        Description="Пинг до роутера отсутствует, нет интернета",
        ServiceId=20,
    )
    assert gateway.evaluate(task_kontur_network).is_irrelevant is False


def test_relevance_gateway_1c_detection():
    gateway = RelevanceGateway()

    # Case 1: 1C DB error in Section 02 (Software) -> Rejection
    task_1c = TaskDTO(
        Id=201,
        Name="Ошибка 1С:Предприятие при закрытии месяца",
        Description="Не проводится документ реализации, заблокирована таблица",
        ServiceId=18,
    )
    decision = gateway.evaluate(task_1c)
    assert decision.is_irrelevant is True
    assert decision.target_service_id == 15
    assert "06. Вопросы по 1С" in decision.target_service_name
    assert "https://servicedesk-pub.corporate.loc/Task/Create?serviceid=15" in decision.public_comment

    # Case 2: 1C already in Section 06 -> Valid / Eligible
    task_1c_proper = TaskDTO(
        Id=202,
        Name="Ошибка 1С УПП",
        Description="База 1С",
        ServiceId=15,
    )
    assert gateway.evaluate(task_1c_proper).is_irrelevant is False

    # Case 3: 1C mentioned alongside Network failure (Exception: 1st line infra issue)
    task_1c_network_incident = TaskDTO(
        Id=203,
        Name="Отвалилась 1С и нет сети в отделе продаж",
        Description="Коммутатор не отвечает, пинг отсутствует, обрыв сети",
        ServiceId=20,
    )
    assert gateway.evaluate(task_1c_network_incident).is_irrelevant is False

    # Case 4: 1C mentioned alongside Printer hardware fault (Exception: 1st line peripheral)
    task_1c_printer_fault = TaskDTO(
        Id=204,
        Name="Печать из 1С: принтер не печатает, замятие бумаги",
        Description="МФУ Kyocera зажевало бумагу",
        ServiceId=19,
    )
    assert gateway.evaluate(task_1c_printer_fault).is_irrelevant is False


def test_relevance_gateway_legitimate_helpdesk_tickets():
    gateway = RelevanceGateway()

    # Printer setup
    task_printer = TaskDTO(
        Id=301,
        Name="Настроить принтер Kyocera Ecosys на WKS-1020",
        Description="IP: 10.244.1.20, рабочий компьютер",
        ServiceId=19,
    )
    assert gateway.evaluate(task_printer).is_irrelevant is False

    # Software installation
    task_soft = TaskDTO(
        Id=302,
        Name="Установить архиватор 7-Zip",
        Description="На рабочий ПК",
        ServiceId=18,
    )
    assert gateway.evaluate(task_soft).is_irrelevant is False


# ==============================================================================
# 3. Triage Task Workflow Integration Tests (Optimistic Lock & DB Audit)
# ==============================================================================


@pytest.mark.asyncio
async def test_triage_task_cancels_irrelevant_eds(test_db, mock_service_auth, cleanup_triage_hooks):
    session_factory = test_db

    mock_client = AsyncMock()
    # Task with EDS issue in wrong catalog root (Section 18)
    task_dto = TaskDTO(
        Id=7001,
        Name="Не работает ЭЦП КриптоПро в СБИС",
        Description="Помогите продлить сертификат подписи",
        ServiceId=18,
        ServiceName="02. Установка и настройка программ",
        StatusId=1,  # Новая
        StatusName="Новая",
        ExecutorIds="",
    )
    mock_client.get_task.return_value = task_dto
    mock_client.update_task.return_value = True
    set_triage_client(mock_client)

    result = await triage_task(task_id=7001)

    assert result["status"] == "canceled"
    assert result["task_id"] == 7001
    assert result["target_service_id"] == 24

    # Verify IntraService API update calls
    assert mock_client.update_task.call_count == 2
    # 1. Public cancellation comment with status 30
    call_public = mock_client.update_task.call_args_list[0].kwargs
    assert call_public["task_id"] == 7001
    assert call_public["status_id"] == 30
    assert call_public["is_private"] is False
    assert "https://servicedesk-pub.corporate.loc/Task/Create?serviceid=24" in call_public["comment"]

    # 2. Private technical audit note
    call_private = mock_client.update_task.call_args_list[1].kwargs
    assert call_private["task_id"] == 7001
    assert call_private["is_private"] is True
    assert "[ТРИАЖ: ШЛЮЗ РЕЛЕВАНТНОСТИ]" in call_private["comment"]

    # Verify audit record persisted in PostgreSQL / SQLite
    async with session_factory() as session:
        stmt = select(TriageAudit).where(TriageAudit.task_id == 7001)
        db_res = await session.execute(stmt)
        record = db_res.scalar_one_or_none()
        assert record is not None
        assert record.action == "cancel_irrelevant"
        assert record.applied is True
        assert record.model_used == "deterministic_gateway"
        assert record.confidence == 1.0
        assert record.decision_json["target_service_id"] == 24


@pytest.mark.asyncio
async def test_triage_task_cancels_irrelevant_1c(test_db, mock_service_auth, cleanup_triage_hooks):
    session_factory = test_db

    mock_client = AsyncMock()
    # Task with 1C issue in wrong catalog root (Section 19 - Hardware)
    task_dto = TaskDTO(
        Id=7002,
        Name="Ошибка 1С:Предприятие при проведении",
        Description="База 1С выдает ошибку блокировки таблицы",
        ServiceId=19,
        ServiceName="03. Установка и обслуживание оргтехники",
        StatusId=1,
        StatusName="Новая",
        ExecutorIds="",
    )
    mock_client.get_task.return_value = task_dto
    mock_client.update_task.return_value = True
    set_triage_client(mock_client)

    result = await triage_task(task_id=7002)

    assert result["status"] == "canceled"
    assert result["task_id"] == 7002
    assert result["target_service_id"] == 15

    # Verify database audit
    async with session_factory() as session:
        stmt = select(TriageAudit).where(TriageAudit.task_id == 7002)
        record = (await session.execute(stmt)).scalar_one_or_none()
        assert record is not None
        assert record.action == "cancel_irrelevant"
        assert record.decision_json["target_service_id"] == 15


@pytest.mark.asyncio
async def test_triage_task_full_auto_assignment(test_db, mock_service_auth, cleanup_triage_hooks):
    session_factory = test_db

    mock_client = AsyncMock()
    task_dto = TaskDTO(
        Id=7003,
        Name="Подключить сетевой принтер HP LaserJet Pro M404dn к WKS-0050",
        Description="Сетевой адрес 10.244.20.15",
        ServiceId=19,
        StatusId=1,
        ExecutorIds="",
    )
    mock_client.get_task.return_value = task_dto
    set_triage_client(mock_client)
    set_triage_session_factory(session_factory)

    with patch("worker.src.tasks.autopilot.autopilot_task.kiq", new_callable=AsyncMock) as mock_autopilot:
        result = await triage_task(task_id=7003)

        assert result["status"] == "auto_assigned_full_auto"
        assert result["scenario"] == "install_printer"
        assert result["confidence"] >= 0.70
        assert result["task_id"] == 7003

        # IntraService task update: assigned to bot, status 2 (In work)
        mock_client.update_task.assert_called_once()
        call_kwargs = mock_client.update_task.call_args.kwargs
        assert call_kwargs["task_id"] == 7003
        assert call_kwargs["status_id"] == 2
        assert call_kwargs["executor_ids"] == "9999"
        assert "🤖 [Автопилот]" in call_kwargs["comment"]

        # Enqueued in Taskiq autopilot_task
        mock_autopilot.assert_called_once_with(task_id=7003)

        # Audit record exists with action "auto_assigned_full_auto"
        async with session_factory() as session:
            stmt = select(TriageAudit).where(TriageAudit.task_id == 7003)
            record = (await session.execute(stmt)).scalar_one_or_none()
            assert record is not None
            assert record.action == "auto_assigned_full_auto"
            assert record.applied is True


@pytest.mark.asyncio
async def test_triage_task_assisted_mode_prefetches_plan(test_db, mock_service_auth, cleanup_triage_hooks):
    session_factory = test_db

    mock_client = AsyncMock()
    # General question / low confidence ticket remains in ASSISTED
    task_dto = TaskDTO(
        Id=7007,
        Name="Уточнение регламента командировок",
        Description="Подскажите, какие документы нужны для согласования авансового отчета",
        ServiceId=16,
        StatusId=1,
        ExecutorIds="",
    )
    mock_client.get_task.return_value = task_dto
    set_triage_client(mock_client)
    set_triage_session_factory(session_factory)

    with patch("worker.src.tasks.plan_prefetch.prefetch_agent_plan_task.kiq", new_callable=AsyncMock) as mock_prefetch:
        result = await triage_task(task_id=7007)

        assert result["status"] == "passed_gateway"
        assert result["task_id"] == 7007

        # No status updates or bot assignments for ASSISTED ticket
        mock_client.update_task.assert_not_called()

        # Prefetched for Copilot UI
        mock_prefetch.assert_called_once_with(ticket_id=7007)

        # Audit record exists with action "passed_gateway"
        async with session_factory() as session:
            stmt = select(TriageAudit).where(TriageAudit.task_id == 7007)
            record = (await session.execute(stmt)).scalar_one_or_none()
            assert record is not None
            assert record.action == "passed_gateway"
            assert record.applied is False


@pytest.mark.asyncio
async def test_triage_task_optimistic_lock_already_closed(test_db, mock_service_auth, cleanup_triage_hooks):
    mock_client = AsyncMock()
    task_closed = TaskDTO(
        Id=7004,
        Name="ЭЦП КриптоПро",
        Description="Уже сделано",
        ServiceId=18,
        StatusId=30,  # Отменена
        StatusName="Отменена",
    )
    mock_client.get_task.return_value = task_closed
    set_triage_client(mock_client)

    result = await triage_task(task_id=7004)
    assert result["status"] == "skipped"
    assert result["reason"] == "already_closed"
    mock_client.update_task.assert_not_called()


@pytest.mark.asyncio
async def test_triage_task_optimistic_lock_human_assigned(test_db, mock_service_auth, cleanup_triage_hooks):
    mock_client = AsyncMock()
    task_claimed = TaskDTO(
        Id=7005,
        Name="ЭЦП КриптоПро",
        Description="Взято инженером",
        ServiceId=18,
        StatusId=1,
        ExecutorIds="105",  # Human engineer ID
    )
    mock_client.get_task.return_value = task_claimed
    set_triage_client(mock_client)

    result = await triage_task(task_id=7005)
    assert result["status"] == "skipped"
    assert result["reason"] == "assigned_to_human"
    mock_client.update_task.assert_not_called()


@pytest.mark.asyncio
async def test_triage_task_auto_reply_skipped(test_db, mock_service_auth, cleanup_triage_hooks):
    mock_client = AsyncMock()
    task_auto_reply = TaskDTO(
        Id=7006,
        Name="Automatic reply: Out of office",
        Description="I am out of the office until next week. Undelivered mail.",
        ServiceId=16,
        StatusId=1,
        ExecutorIds="",
    )
    mock_client.get_task.return_value = task_auto_reply
    set_triage_client(mock_client)

    result = await triage_task(task_id=7006)
    assert result["status"] == "skipped"
    assert result["reason"] == "auto_reply_detected"
    mock_client.update_task.assert_not_called()
