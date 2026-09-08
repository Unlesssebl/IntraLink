import pytest
from app.database.db import AsyncSessionLocal, ResolutionPolicy, ResponseTemplate
from app.services.resolution_service import ResolutionUnavailable, resolve_outcome


@pytest.mark.asyncio
async def test_resolution_is_versioned_and_strict():
    async with AsyncSessionLocal() as test_db_session:
        template = ResponseTemplate(
            key="clarify",
            version=3,
            name="Clarify",
            template_text="Укажите {{ field }}",
            required_variables=["field"],
            created_by="test",
        )
        test_db_session.add(template)
        await test_db_session.flush()
        test_db_session.add(
            ResolutionPolicy(
                outcome_key="invalid",
                version=2,
                outcome_kind="clarification",
                template_id=template.id,
                target_status_id=35,
                status_name="Требует уточнения",
                expenses=5,
                risk_level=0,
                requires_approval=False,
                created_by="test",
            )
        )
        await test_db_session.commit()

        resolved = await resolve_outcome(
            test_db_session, "invalid", {"field": "ФИО"}, expected_kind="clarification"
        )
        assert resolved["comment"] == "Укажите ФИО"
        assert resolved["template_version"] == 3
        assert resolved["policy_version"] == 2

        with pytest.raises(ResolutionUnavailable, match="variables_missing"):
            await resolve_outcome(
                test_db_session, "invalid", {}, expected_kind="clarification"
            )


@pytest.mark.asyncio
async def test_materialize_typed_decision_with_resolution_and_metadata():
    from app.services.typed_decision_adapter import materialize_typed_decision

    async with AsyncSessionLocal() as test_db_session:
        template = ResponseTemplate(
            key="redirect_tpl",
            version=1,
            name="Redirect",
            template_text="Перенаправлено в {{ target_service }}",
            required_variables=["target_service"],
            created_by="test",
        )
        test_db_session.add(template)
        await test_db_session.flush()
        test_db_session.add(
            ResolutionPolicy(
                outcome_key="wrong_service",
                version=1,
                outcome_kind="resolution",
                template_id=template.id,
                target_status_id=30,
                status_name="Отменена",
                expenses=5,
                risk_level=0,
                requires_approval=False,
                created_by="test",
            )
        )
        await test_db_session.commit()

        raw_decision = {
            "name": "Decision",
            "typed_outcome": {
                "kind": "resolution",
                "outcome_key": "wrong_service",
                "target_status_id": 30,
                "context": {"target_service": "1С"},
                "metadata": {
                    "is_redirect": True,
                    "target_service_name": "1С",
                    "reason": "1C keywords found",
                },
            },
        }

        adapted = await materialize_typed_decision(test_db_session, raw_decision)
        assert adapted["status_id"] == 30
        assert adapted["comment"] == "Перенаправлено в 1С"
        assert adapted["is_redirect"] is True
        assert adapted["target_service_name"] == "1С"

