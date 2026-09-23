"""Integration tests for Triage feature slice and GEMINI.md business domain rules."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.src.core.db import get_db_session
from api.src.main import app
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO


@pytest.fixture
def eds_reject_task():
    return TaskDTO(
        Id=2001,
        Name="Выпуск личной ЭЦП на физлицо",
        Description="Прошу выпустить личную электронную подпись для налоговой на физлицо",
        ServiceId=9,
        ServiceName="09. Электронная подпись",
        StatusId=1,
        StatusName="Новая",
        ApplicantId=505,
        ApplicantName="Петров П.П.",
        entities=ExtractedEntitiesDTO(),
    )


@pytest.fixture
def directum_install_task():
    return TaskDTO(
        Id=2002,
        Name="Прошу установить directum на новый ноутбук",
        Description="Требуется инсталляция клиента Directum",
        ServiceId=1,
        ServiceName="Общие вопросы",
        StatusId=1,
        StatusName="Новая",
        ApplicantId=606,
        ApplicantName="Сидорова С.С.",
        entities=ExtractedEntitiesDTO(pc_name="NOTE-TEMP-01"),
    )


@pytest.mark.asyncio
async def test_triage_deterministic_rule_personal_eds_reject(eds_reject_task):
    """Verify GEMINI.md Section 09 rule: IT department rejects personal EDS with status 30."""

    async def override_db():
        session = AsyncMock()
        session.add = lambda x: None
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        yield session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    with patch("core.intraservice.client.IntraServiceClient.get_task", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = eds_reject_task
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v2/triage/analyze/2001")
            assert resp.status_code == 200
            data = resp.json()
            assert data["rule_matched"] == "RULE_PERSONAL_EDS_REJECT"
            assert data["decision"]["action"] == "redirect_service"
            assert data["decision"]["suggested_status_id"] == 30  # Отменена
            assert "регламенту холдинга ТЭМПО" in data["decision"]["suggested_comment"]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_triage_deterministic_rule_directum_install(directum_install_task):
    """Verify GEMINI.md Section 05 rule: Directum installation routed to service 233."""

    async def override_db():
        session = AsyncMock()
        session.add = lambda x: None
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        yield session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    with patch("core.intraservice.client.IntraServiceClient.get_task", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = directum_install_task
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v2/triage/analyze/2002")
            assert resp.status_code == 200
            data = resp.json()
            assert data["rule_matched"] == "RULE_DIRECTUM_INSTALL"
            assert data["decision"]["target_service_id"] == 233

    app.dependency_overrides.clear()
