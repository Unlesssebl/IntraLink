"""Unit and integration tests for RoutingDecisionRepository and RoutingDecisionService."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database.models import Base
from core.routing.cascade import ROUTER_VERSION, RoutingCascade
from core.routing.contracts import (
    CandidateVerification,
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingDecision,
    RoutingEvidence,
    RoutingState,
    ScenarioCandidate,
    TicketSnapshot,
    VerificationVerdict,
    VerifierTrace,
)
from core.routing.exceptions import RoutingPersistenceError
from core.routing.persistence import RoutingDecisionRepository, RoutingDecisionService


@pytest.fixture
async def async_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


def _make_sample_decision(task_id: int = 1001) -> RoutingDecision:
    ev = RoutingEvidence(
        id="ev-1",
        candidate_key="install_printer",
        source=EvidenceSource.service_id,
        strength=EvidenceStrength.exact,
        polarity=EvidencePolarity.supports,
        source_ref="service_id",
    )
    cand = ScenarioCandidate(
        scenario_key="install_printer",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_id},
    )
    trace = VerifierTrace(
        status="success",
        prompt_version="verifier-v1",
        sanitizer_version="dlp-v1",
        privacy_zone="green",
        model_alias="helpdesk-fast",
        sanitized_input_hash="a" * 64,
    )
    ver = CandidateVerification(
        scenario_key="install_printer",
        verdict=VerificationVerdict.supported,
        evidence_spans=["принтер"],
        referenced_evidence_ids=["ev-1"],
    )

    return RoutingDecision(
        id=uuid4(),
        task_id=task_id,
        snapshot_hash="s" * 64,
        router_version=ROUTER_VERSION,
        prompt_version="verifier-v1",
        state=RoutingState.selected,
        selected_scenario="install_printer",
        selected_scenario_version="1.0.0",
        candidates=[cand],
        evidence=[ev],
        verifier_result=[ver],
        missing_facts=[],
        degradation_reason=None,
        decision_reason_codes=["verifier_arbitration", "single_supported_candidate", "facts_complete"],
        degraded_components={"semantic": "provider_timeout"},
        verifier_trace=trace,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_repository_insert_and_get_by_id(async_session_factory):
    repo = RoutingDecisionRepository()
    decision = _make_sample_decision(task_id=2001)

    async with async_session_factory() as session, session.begin():
        await repo.insert(decision=decision, session=session)

    async with async_session_factory() as session:
        loaded = await repo.get_by_id(decision.id, session=session)
        assert loaded is not None
        assert loaded.id == decision.id
        assert loaded.task_id == 2001
        assert loaded.selected_scenario == "install_printer"
        assert loaded.state == RoutingState.selected
        assert loaded.decision_reason_codes == ["verifier_arbitration", "single_supported_candidate", "facts_complete"]
        assert loaded.degraded_components == {"semantic": "provider_timeout"}
        assert loaded.verifier_trace is not None
        assert loaded.verifier_trace.status == "success"
        assert loaded.verifier_trace.prompt_version == "verifier-v1"


@pytest.mark.asyncio
async def test_repository_get_latest_for_task(async_session_factory):
    repo = RoutingDecisionRepository()
    d1 = _make_sample_decision(task_id=3001)
    d2 = _make_sample_decision(task_id=3001)

    async with async_session_factory() as session, session.begin():
        await repo.insert(decision=d1, session=session)
        await repo.insert(decision=d2, session=session)

    async with async_session_factory() as session:
        latest = await repo.get_latest_for_task(3001, session=session)
        assert latest is not None
        assert latest.id == d2.id


@pytest.mark.asyncio
async def test_service_external_session_flush_vs_rollback(async_session_factory):
    """External session: service performs flush(), caller manages commit or rollback."""
    mock_cascade = AsyncMock(spec=RoutingCascade)
    decision = _make_sample_decision(task_id=4001)
    mock_cascade.decide.return_value = decision

    repo = RoutingDecisionRepository()
    service = RoutingDecisionService(cascade=mock_cascade, repository=repo)
    snap = TicketSnapshot(
        task_id=4001,
        status_id=1,
        title="Title",
        description="Desc",
        snapshot_hash="s" * 64,
    )

    # 1. Rollback case: record should NOT be in DB
    async with async_session_factory() as session, session.begin():
        stored = await service.analyze_and_store(snap, session=session)
        assert stored.id == decision.id
        await session.rollback()

    async with async_session_factory() as session:
        assert await repo.get_by_id(decision.id, session=session) is None

    # 2. Commit case: record should be in DB
    async with async_session_factory() as session, session.begin():
        await service.analyze_and_store(snap, session=session)
        # commits on exit of begin()

    async with async_session_factory() as session:
        assert await repo.get_by_id(decision.id, session=session) is not None


@pytest.mark.asyncio
async def test_service_owned_session_commits_automatically(async_session_factory):
    """Owned session (session=None): commits automatically and manages transaction."""
    mock_cascade = AsyncMock(spec=RoutingCascade)
    decision = _make_sample_decision(task_id=5001)
    mock_cascade.decide.return_value = decision

    repo = RoutingDecisionRepository()
    service = RoutingDecisionService(
        cascade=mock_cascade,
        repository=repo,
        session_factory=async_session_factory,
    )
    snap = TicketSnapshot(
        task_id=5001,
        status_id=1,
        title="Title",
        description="Desc",
        snapshot_hash="s" * 64,
    )

    stored = await service.analyze_and_store(snap, session=None)
    assert stored.id == decision.id

    async with async_session_factory() as session:
        loaded = await repo.get_by_id(decision.id, session=session)
        assert loaded is not None
        assert loaded.task_id == 5001


@pytest.mark.asyncio
async def test_service_raises_routing_persistence_error_on_db_failure():
    """Persistence error raises RoutingPersistenceError and does not return unpersisted decision."""
    mock_cascade = AsyncMock(spec=RoutingCascade)
    decision = _make_sample_decision(task_id=6001)
    mock_cascade.decide.return_value = decision

    mock_repo = AsyncMock(spec=RoutingDecisionRepository)
    mock_repo.insert.side_effect = RuntimeError("Database disk full")

    service = RoutingDecisionService(cascade=mock_cascade, repository=mock_repo)
    snap = TicketSnapshot(
        task_id=6001,
        status_id=1,
        title="Title",
        description="Desc",
        snapshot_hash="s" * 64,
    )

    mock_session = AsyncMock(spec=AsyncSession)
    with pytest.raises(RoutingPersistenceError, match="routing_persistence_failed") as exc_info:
        await service.analyze_and_store(snap, session=mock_session)

    # Check exception chaining without string leakage in main exception message
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value) == "routing_persistence_failed"
