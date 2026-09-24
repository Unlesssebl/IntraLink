"""Tests for Knowledge Base incremental synchronization and hybrid quality cascade."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.src.features.knowledge_base.sync_service import (
    KnowledgeBaseSyncService,
    evaluate_solution_quality,
)
from core.database.base import Base
from core.database.models import TaskKnowledgeBase
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO

# --- 1. L1 Heuristic Quality Tests ---


def test_quality_gate_rejects_single_word_boilerplate():
    boilerplate = [
        "ок",
        "ок.",
        "Готово!",
        "сделано",
        "выполнено",
        "закрыта",
        "все работает",
        "всё работает",
        "спасибо",
        "принято в работу",
        "заявка выполнена в штатном режиме",
    ]
    for text in boilerplate:
        is_quality, reason = evaluate_solution_quality(text, status_id=4)
        assert is_quality is False, f"Expected '{text}' to be rejected, got accepted ({reason})"


def test_quality_gate_rejects_administrative_closures():
    closures = [
        "Не дозвонился до заявителя в течение дня.",
        "Акт прикрепил к заявке, закрываю.",
        "Повторная заявка, работы ведутся в основной.",
        "Заявка потеряла актуальность.",
        "Неправильный раздел, создайте заявку заново.",
    ]
    for text in closures:
        is_quality, reason = evaluate_solution_quality(text, status_id=4)
        assert is_quality is False, f"Expected '{text}' to be rejected, got accepted ({reason})"


def test_quality_gate_rejects_system_logs():
    system_logs = [
        "Заявка автоматически переведена в статус Выполнена по истечении 24 часов.",
        "Оцените качество обслуживания по ссылке.",
        "Назначен исполнитель Иванов И.И.",
    ]
    for text in system_logs:
        is_quality, reason = evaluate_solution_quality(text, status_id=4)
        assert is_quality is False, f"Expected system log '{text}' to be rejected"


def test_quality_gate_accepts_actionable_technical_solutions():
    valid_solutions = [
        "Перезапущена служба очереди печати Spooler, удалены зависшие задания.",
        "Разблокирована учетная запись в Active Directory, сброшен флаг lockout.",
        "Заменен тонер-картридж TK-1170 в сетевом МФУ, проверена тестовая печать.",
    ]
    for text in valid_solutions:
        is_quality, reason = evaluate_solution_quality(text, status_id=4)
        assert is_quality is True, f"Expected '{text}' to be accepted, rejected with: {reason}"


def test_quality_gate_length_boundaries():
    # Regular closed ticket requires at least 20 chars
    short_text = "Сброшен пароль."
    is_q, _ = evaluate_solution_quality(short_text, status_id=4)
    assert is_q is False

    # Status 30 (Cancelled) requires at least 60 chars of reasoned justification
    short_cancel = "Отменена по просьбе заявителя по телефону."
    is_q, _ = evaluate_solution_quality(short_cancel, status_id=30)
    assert is_q is False

    valid_cancel = (
        "Заявка отменена по согласованию: закупка оборудования перенесена на следующий квартал "
        "согласно служебной записке № 42-ИТ."
    )
    is_q, _ = evaluate_solution_quality(valid_cancel, status_id=30)
    assert is_q is True


# --- 2. L2 LLM-as-a-Judge Tests ---


@pytest.mark.asyncio
async def test_llm_judge_approves_actionable_solution():
    mock_ai_client = AsyncMock()
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "1"
    mock_resp.choices = [mock_choice]
    mock_ai_client.chat.completions.create.return_value = mock_resp

    service = KnowledgeBaseSyncService(ai_client=mock_ai_client)
    approved, reason = await service.evaluate_solution_quality_llm(
        problem="Не открывается сетевая папка",
        solution="Добавлены права доступа группе Domain Users на шару \\\\SRV-FS01\\Docs",
        service_name="Сетевые ресурсы",
        task_name="Доступ к папке",
    )
    assert approved is True
    assert "одобрено" in reason


@pytest.mark.asyncio
async def test_llm_judge_rejects_uninformative_solution():
    mock_ai_client = AsyncMock()
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "0"
    mock_resp.choices = [mock_choice]
    mock_ai_client.chat.completions.create.return_value = mock_resp

    service = KnowledgeBaseSyncService(ai_client=mock_ai_client)
    approved, reason = await service.evaluate_solution_quality_llm(
        problem="Не работает 1С",
        solution="Решено по согласованию с главным бухгалтером без изменений",
        service_name="1С:Предприятие",
        task_name="Сбой 1С",
    )
    assert approved is False
    assert "отклонено" in reason


@pytest.mark.asyncio
async def test_llm_judge_fallback_on_network_error():
    mock_ai_client = AsyncMock()
    mock_ai_client.chat.completions.create.side_effect = RuntimeError("LiteLLM connection refused")

    service = KnowledgeBaseSyncService(ai_client=mock_ai_client)
    approved, reason = await service.evaluate_solution_quality_llm(
        problem="Проблема с принтером",
        solution="Заменен фотобарабан",
    )
    # Graceful fallback to L1
    assert approved is True
    assert "fallback" in reason


# --- 3. Lifetime Solution Extraction Hierarchy Tests ---


def test_extract_solution_from_lifetime_priorities():
    service = KnowledgeBaseSyncService()
    task = TaskDTO(
        id=1001,
        name="Зависает Directum",
        service_id=234,
        service_name="Directum",
        creator_id=42,  # User 42 is creator
        executor_ids="99",  # Engineer 99 is assigned executor
        custom_fields={},
    )

    lifetime = [
        # Event 1: Creator writes a comment (MUST be ignored)
        TaskLifetimeEventDTO(
            id=1,
            task_id=1001,
            created="2026-09-24 10:00",
            editor_id=42,
            editor="Заявитель",
            comment="У меня все заработало само, спасибо!",
            status_id=2,
        ),
        # Event 2: System robot notification (MUST be ignored)
        TaskLifetimeEventDTO(
            id=2,
            task_id=1001,
            created="2026-09-24 10:05",
            editor_id=1,
            editor="intraservice",
            comment="Заявка автоматически закрыта.",
            status_id=4,
        ),
        # Event 3: Actual closing comment from engineer
        TaskLifetimeEventDTO(
            id=3,
            task_id=1001,
            created="2026-09-24 10:10",
            editor_id=99,
            editor="Инженер 99",
            comment="Очищен локальный кэш пользователя в папке Directum, перезапущен процесс.",
            status_id=4,
        ),
    ]

    extracted = service.extract_solution_from_lifetime(task, lifetime)
    assert extracted == "Очищен локальный кэш пользователя в папке Directum, перезапущен процесс."


# --- 4. End-to-End Incremental Sync Integration Test ---


@pytest.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_incremental_full_cycle(sqlite_session):
    mock_intraservice = AsyncMock()
    mock_ai_client = AsyncMock()

    # Ticket 1: Valid technical solution
    t1 = TaskDTO(
        id=201,
        name="Не печатает МФУ",
        description="Ошибка замятия бумаги",
        service_id=233,
        service_name="Принтеры",
        status_id=4,
        status_name="Закрыта",
        creator_id=10,
        executor_ids="50",
        custom_fields={},
    )
    # Ticket 2: Boilerplate uninformative reply ("ок")
    t2 = TaskDTO(
        id=202,
        name="Сбой мыши",
        description="Не двигается курсор",
        service_id=233,
        service_name="Принтеры",
        status_id=4,
        status_name="Закрыта",
        creator_id=11,
        executor_ids="50",
        custom_fields={},
    )
    # Ticket 3: Semantic duplicate of Ticket 1
    t3 = TaskDTO(
        id=203,
        name="Не печатает сетевой принтер",
        description="Замятие",
        service_id=233,
        service_name="Принтеры",
        status_id=4,
        status_name="Закрыта",
        creator_id=12,
        executor_ids="50",
        custom_fields={},
    )

    mock_intraservice.get_tasks.side_effect = [
        [t1, t2, t3],  # Page 1
        [],  # Page 2 (end)
    ]

    mock_intraservice.get_task_lifetime.side_effect = [
        [
            TaskLifetimeEventDTO(
                id=1,
                task_id=201,
                editor_id=50,
                editor="Инженер",
                comment="Извлечен замятый лист из лотка №2, очищены ролики захвата бумаги.",
                status_id=4,
            )
        ],
        [
            TaskLifetimeEventDTO(
                id=2,
                task_id=202,
                editor_id=50,
                editor="Инженер",
                comment="ок",
                status_id=4,
            )
        ],
        [
            TaskLifetimeEventDTO(
                id=3,
                task_id=203,
                editor_id=50,
                editor="Инженер",
                comment="Извлечен замятый лист из лотка №2, очищены ролики захвата бумаги.",
                status_id=4,
            )
        ],
    ]

    service = KnowledgeBaseSyncService(
        intraservice_client=mock_intraservice,
        ai_client=mock_ai_client,
    )

    with (
        patch("core.rag.sync.get_embedding_vector", new_callable=AsyncMock) as mock_embed,
        patch.object(service, "evaluate_solution_quality_llm", new_callable=AsyncMock) as mock_llm_judge,
    ):

        mock_llm_judge.return_value = (True, "одобрено")
        # Embedding vector
        mock_embed.return_value = [0.05] * 1024

        stats = await service.sync_incremental(
            session=sqlite_session,
            hours=48,
            quota_per_service=30,
            throttle_delay_sec=0.0,
            ai_eval=True,
        )

        assert stats.processed == 3
        # t1 should be indexed
        assert stats.indexed == 1
        # t2 should be skipped by L1 ("ок")
        assert stats.skipped_low_quality == 1
        # t3 should be skipped by Cosine Gate (duplicate vector of t1)
        assert stats.skipped_duplicates == 1

        # Verify t1 in database
        stmt = select(TaskKnowledgeBase).where(TaskKnowledgeBase.task_id == 201)
        item = (await sqlite_session.execute(stmt)).scalar_one_or_none()
        assert item is not None
        assert "Извлечен замятый лист" in item.solution
        assert item.service_id == 233
