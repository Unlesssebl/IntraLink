"""Tests for verifier prompt construction, security invariants, and versioning."""

from core.routing.contracts import (
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingEvidence,
    ScenarioCandidate,
    SnapshotComment,
    TicketSnapshot,
)
from core.routing.profile_registry import get_default_profile_registry
from core.routing.verifier.privacy import VerifierPrivacyRouter
from core.routing.verifier.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_verifier_user_payload,
)


def test_prompt_version_is_fixed():
    """DoD: Prompt version is candidate-verifier-v1."""
    assert PROMPT_VERSION == "candidate-verifier-v1"


def test_system_prompt_forbids_injection_and_confidence():
    """System prompt must explicitly forbid prompt injections, confidence, statuses, and parameters."""
    lower_prompt = SYSTEM_PROMPT.lower()

    # Data separation & injection warning
    assert "непроверенным внешним вводом" in lower_prompt or "untrusted" in lower_prompt
    assert "проигнорированы" in lower_prompt

    # Forbidding non-verdict fields
    assert "запрещено" in lower_prompt
    assert "confidence" in lower_prompt
    assert "статус" in lower_prompt
    assert "параметр" in lower_prompt


def test_user_payload_contains_intent_summary_and_omits_required_facts():
    """User payload must contain intent_summary and prior evidence, and strictly omit required_facts."""
    registry = get_default_profile_registry()
    router = VerifierPrivacyRouter()

    snapshot = TicketSnapshot(
        task_id=999,
        status_id=1,
        service_id=19,
        service_name="Принтеры и МФУ",
        title="Установка сетевого принтера",
        description="Прошу подключить сетевой принтер в кабинете 305",
        public_comments=[
            SnapshotComment(id=1, text="Принтер HP 402dne", author_name="User", is_private=False)
        ],
        custom_fields={"custom1": "secret"},
        entities={"pc_name": "WKS-1234"},
        snapshot_hash="0" * 64,
    )

    ev = RoutingEvidence(
        id="ev-catalog-1",
        candidate_key="install_printer",
        source=EvidenceSource.service_id,
        polarity=EvidencePolarity.supports,
        strength=EvidenceStrength.exact,
        source_ref="service_id:19",
    )

    candidate = ScenarioCandidate(
        scenario_key="install_printer",
        scenario_version="1.0.0",
        evidence_ids=["ev-catalog-1"],
    )

    prepared = router.prepare(snapshot, [candidate])
    payload = build_verifier_user_payload(prepared, [candidate], [ev], registry)

    # Check top-level payload keys
    assert set(payload.keys()) == {"service", "title", "description", "comments", "candidates"}
    assert payload["service"]["id"] == 19
    assert payload["title"] == "Установка сетевого принтера"
    assert payload["comments"][0]["id"] == 1

    # Check candidate structure
    cand_obj = payload["candidates"][0]
    assert cand_obj["scenario_key"] == "install_printer"
    assert "intent_summary" in cand_obj
    assert len(cand_obj["intent_summary"]) > 0

    # Ensure required_facts is completely absent from user payload
    assert "required_facts" not in cand_obj
    assert "facts" not in payload

    # Check prior evidence attached
    assert len(cand_obj["prior_evidence"]) == 1
    assert cand_obj["prior_evidence"][0]["id"] == "ev-catalog-1"
    assert cand_obj["prior_evidence"][0]["polarity"] == "supports"
