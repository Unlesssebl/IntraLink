"""LLM Grey Zone Verifier service with strict backend validation.

Performs deterministic input validation, privacy-aware LLM evaluation,
exact evidence substring verification, and single-attempt repair recovery.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Sequence, Set

from pydantic import ValidationError

from core.routing.contracts import (
    CandidateVerification,
    EvidencePolarity,
    EvidenceStrength,
    RoutingEvidence,
    ScenarioCandidate,
    TicketSnapshot,
    VerificationVerdict,
)
from core.routing.profile_registry import RoutingProfileRegistry
from core.routing.verifier.contracts import (
    PrivacyZone,
    RawVerifierOutput,
    VerifierRunResult,
    VerifierRunStatus,
)
from core.routing.verifier.privacy import (
    SANITIZER_VERSION,
    PreparedVerifierInput,
    VerifierPrivacyRouter,
)
from core.routing.verifier.prompt import (
    PROMPT_VERSION,
    REPAIR_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_repair_user_payload,
    build_verifier_user_payload,
)
from core.routing.verifier.transport import (
    VerifierTransport,
    VerifierTransportTimeoutError,
    VerifierTransportUnavailableError,
)

logger = logging.getLogger("core.routing.verifier.service")


class GreyZoneVerifier:
    """Isolated LLM verifier for grey-zone routing candidates with strict invariants."""

    def __init__(
        self,
        transport: VerifierTransport,
        privacy_router: Optional[VerifierPrivacyRouter] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._transport = transport
        self._privacy_router = privacy_router or VerifierPrivacyRouter()
        self._timeout_seconds = timeout_seconds

    async def verify(
        self,
        *,
        snapshot: TicketSnapshot,
        candidates: Sequence[ScenarioCandidate],
        evidence: Sequence[RoutingEvidence],
        profiles: RoutingProfileRegistry,
    ) -> VerifierRunResult:
        """Execute safe verification of candidate scenarios with proof validation."""
        # 1. Input bounds and integrity check
        if not (1 <= len(candidates) <= 3):
            logger.warning("GreyZoneVerifier policy violation: candidate count %d outside [1..3]", len(candidates))
            return self._make_degraded("verifier_policy_blocked", snapshot.snapshot_hash)

        candidate_keys: Set[str] = set()
        for cand in candidates:
            if cand.scenario_key in candidate_keys:
                logger.warning("GreyZoneVerifier policy violation: duplicate candidate key '%s'", cand.scenario_key)
                return self._make_degraded("verifier_policy_blocked", snapshot.snapshot_hash)
            candidate_keys.add(cand.scenario_key)

            if profiles.get(cand.scenario_key) is None:
                logger.warning("GreyZoneVerifier policy violation: unknown scenario '%s'", cand.scenario_key)
                return self._make_degraded("verifier_policy_blocked", snapshot.snapshot_hash)

        evidence_by_id: Dict[str, RoutingEvidence] = {}
        for ev in evidence:
            evidence_by_id[ev.id] = ev

        for cand in candidates:
            for eid in cand.evidence_ids:
                if eid not in evidence_by_id:
                    logger.warning("GreyZoneVerifier policy violation: missing evidence '%s'", eid)
                    return self._make_degraded("verifier_policy_blocked", snapshot.snapshot_hash)
                ev = evidence_by_id[eid]
                if ev.candidate_key != cand.scenario_key or ev.polarity != EvidencePolarity.supports:
                    logger.warning("GreyZoneVerifier policy violation: invalid evidence link '%s'", eid)
                    return self._make_degraded("verifier_policy_blocked", snapshot.snapshot_hash)

            for cid in cand.contradiction_ids:
                if cid not in evidence_by_id:
                    logger.warning("GreyZoneVerifier policy violation: missing contradiction '%s'", cid)
                    return self._make_degraded("verifier_policy_blocked", snapshot.snapshot_hash)
                ev = evidence_by_id[cid]
                if ev.candidate_key != cand.scenario_key or ev.polarity != EvidencePolarity.contradicts:
                    logger.warning("GreyZoneVerifier policy violation: invalid contradiction link '%s'", cid)
                    return self._make_degraded("verifier_policy_blocked", snapshot.snapshot_hash)

        # 2. Prepare DLP-safe input and determine privacy routing
        prepared = self._privacy_router.prepare(snapshot, candidates)

        # 3. Build user payload
        payload = build_verifier_user_payload(prepared, candidates, evidence, profiles)

        # 4. Invoke LLM transport
        try:
            raw_response = await self._transport.complete_json(
                model_alias=prepared.requested_model_alias,
                system_prompt=SYSTEM_PROMPT,
                payload=payload,
                timeout_seconds=self._timeout_seconds,
            )
        except VerifierTransportTimeoutError:
            return self._make_degraded("verifier_timeout", prepared.sanitized_input_hash, prepared)
        except (VerifierTransportUnavailableError, Exception) as exc:
            logger.warning("GreyZoneVerifier transport failure: %s", type(exc).__name__)
            return self._make_degraded("verifier_unavailable", prepared.sanitized_input_hash, prepared)

        # 5. Parse and validate JSON output
        parsed_output, parse_err = self._parse_and_validate(raw_response)

        # 6. Single repair attempt for invalid JSON or schema errors
        if parsed_output is None:
            logger.info("GreyZoneVerifier: initial response failed schema (%s), attempting repair", parse_err)
            repair_payload = build_repair_user_payload(payload, parse_err)
            try:
                repair_response = await self._transport.complete_json(
                    model_alias=prepared.requested_model_alias,
                    system_prompt=REPAIR_SYSTEM_PROMPT,
                    payload=repair_payload,
                    timeout_seconds=self._timeout_seconds,
                )
                parsed_output, parse_err = self._parse_and_validate(repair_response)
            except VerifierTransportTimeoutError:
                return self._make_degraded("verifier_timeout", prepared.sanitized_input_hash, prepared)
            except Exception:
                return self._make_degraded("verifier_unavailable", prepared.sanitized_input_hash, prepared)

            if parsed_output is None:
                degrade_code = "verifier_invalid_json" if parse_err == "invalid_json" else "verifier_invalid_schema"
                return self._make_degraded(degrade_code, prepared.sanitized_input_hash, prepared)

        # 7. Check candidate set coverage (no unknown, no missing, no duplicates)
        returned_keys = [v.scenario_key for v in parsed_output.verifications]
        if len(returned_keys) != len(candidate_keys) or set(returned_keys) != candidate_keys:
            logger.warning("GreyZoneVerifier candidate mismatch: expected %s, got %s", candidate_keys, returned_keys)
            return self._make_degraded("verifier_candidate_mismatch", prepared.sanitized_input_hash, prepared)

        # 8. Check evidence spans, contradictions and referenced evidence IDs
        # Gather all sanitized texts to check substrings
        texts_to_check: List[str] = []
        if prepared.service_name:
            texts_to_check.append(prepared.service_name)
        if prepared.title:
            texts_to_check.append(prepared.title)
        if prepared.description:
            texts_to_check.append(prepared.description)
        for comm in prepared.public_comments:
            if comm.text:
                texts_to_check.append(comm.text)

        verified_list: List[CandidateVerification] = []
        for raw_v in parsed_output.verifications:
            # 8a. Check referenced evidence IDs
            for ref_id in raw_v.referenced_evidence_ids:
                if ref_id not in evidence_by_id:
                    logger.warning("GreyZoneVerifier invalid evidence: unknown referenced ID '%s'", ref_id)
                    return self._make_degraded("verifier_invalid_evidence", prepared.sanitized_input_hash, prepared)
                ev = evidence_by_id[ref_id]
                if ev.candidate_key != raw_v.scenario_key or ev.polarity != EvidencePolarity.supports:
                    logger.warning("GreyZoneVerifier invalid evidence: mismatch on referenced ID '%s'", ref_id)
                    return self._make_degraded("verifier_invalid_evidence", prepared.sanitized_input_hash, prepared)

            # 8b. Check evidence spans are exact substrings
            valid_evidence_spans: List[str] = []
            for span in raw_v.evidence_spans:
                s_strip = span.strip()
                if not s_strip:
                    continue
                if not any(s_strip in t for t in texts_to_check):
                    logger.warning("GreyZoneVerifier invalid evidence: span '%s' not found in sanitized texts", s_strip)
                    return self._make_degraded("verifier_invalid_evidence", prepared.sanitized_input_hash, prepared)
                valid_evidence_spans.append(s_strip)

            # 8c. Check contradictions are exact substrings
            valid_contradictions: List[str] = []
            for contra in raw_v.contradictions:
                c_strip = contra.strip()
                if not c_strip:
                    continue
                if not any(c_strip in t for t in texts_to_check):
                    logger.warning("GreyZoneVerifier invalid evidence: contradiction '%s' not found in sanitized texts", c_strip)
                    return self._make_degraded("verifier_invalid_evidence", prepared.sanitized_input_hash, prepared)
                valid_contradictions.append(c_strip)

            # 8d. Verdict requirements
            if raw_v.verdict == VerificationVerdict.supported:
                # Requires at least one valid evidence span OR one referenced evidence with strength exact/strong
                has_strong_referenced = any(
                    evidence_by_id[ref_id].strength in (EvidenceStrength.exact, EvidenceStrength.strong)
                    for ref_id in raw_v.referenced_evidence_ids
                )
                if not valid_evidence_spans and not has_strong_referenced:
                    logger.warning("GreyZoneVerifier invalid evidence: supported verdict has no valid spans or strong evidence")
                    return self._make_degraded("verifier_invalid_evidence", prepared.sanitized_input_hash, prepared)

            elif raw_v.verdict == VerificationVerdict.contradicted:
                # Requires at least one contradiction span
                if not valid_contradictions:
                    logger.warning("GreyZoneVerifier invalid evidence: contradicted verdict has no contradiction span")
                    return self._make_degraded("verifier_invalid_evidence", prepared.sanitized_input_hash, prepared)

            verified_list.append(
                CandidateVerification(
                    scenario_key=raw_v.scenario_key,
                    verdict=raw_v.verdict,
                    evidence_spans=valid_evidence_spans,
                    contradictions=valid_contradictions,
                    missing_information=raw_v.missing_information,
                    referenced_evidence_ids=raw_v.referenced_evidence_ids,
                )
            )

        # 9. Return success result
        return VerifierRunResult(
            status=VerifierRunStatus.success,
            verifications=verified_list,
            prompt_version=PROMPT_VERSION,
            sanitizer_version=prepared.sanitizer_version,
            requested_model_alias=prepared.requested_model_alias,
            privacy_zone=prepared.privacy_zone,
            sanitized_input_hash=prepared.sanitized_input_hash,
            degradation_reason=None,
        )

    def _parse_and_validate(self, text: str) -> tuple[Optional[RawVerifierOutput], str]:
        """Safely parse JSON and validate strict Pydantic model."""
        try:
            data = json.loads(text)
        except Exception:
            return None, "invalid_json"

        if not isinstance(data, dict):
            return None, "schema_validation_error"

        try:
            output = RawVerifierOutput.model_validate(data)
            return output, ""
        except ValidationError:
            return None, "schema_validation_error"

    def _make_degraded(
        self,
        reason: str,
        input_hash: str,
        prepared: Optional[PreparedVerifierInput] = None,
    ) -> VerifierRunResult:
        """Construct deterministic degraded result with zero exception text."""
        zone = prepared.privacy_zone if prepared else PrivacyZone.red
        alias = prepared.requested_model_alias if prepared else self._privacy_router.local_model_alias
        sanitizer_ver = prepared.sanitizer_version if prepared else SANITIZER_VERSION

        return VerifierRunResult(
            status=VerifierRunStatus.degraded,
            verifications=[],
            prompt_version=PROMPT_VERSION,
            sanitizer_version=sanitizer_ver,
            requested_model_alias=alias,
            privacy_zone=zone,
            sanitized_input_hash=input_hash,
            degradation_reason=reason,
        )
