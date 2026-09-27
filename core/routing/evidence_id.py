"""Deterministic Evidence ID computation for Evidence-Based Routing Cascade.

Guarantees identical cryptographic SHA-256 identifiers for identical signals across
candidate providers and ticket snapshots.
"""

import hashlib
import json
from typing import Optional, Union

from core.routing.contracts import EvidencePolarity, EvidenceSource, EvidenceStrength


def compute_evidence_id(
    provider_name: str,
    snapshot_hash: str,
    candidate_key: str,
    source: Union[EvidenceSource, str],
    polarity: Union[EvidencePolarity, str],
    strength: Union[EvidenceStrength, str],
    source_ref: str,
    text_span: Optional[str] = None,
) -> str:
    """Compute deterministic SHA-256 hex digest for an atomic routing signal.

    Args:
        provider_name: Canonical name of the candidate provider.
        snapshot_hash: SHA-256 of the source TicketSnapshot.
        candidate_key: Scenario identifier the evidence belongs to.
        source: Field or origin of the signal (e.g. title, service_id).
        polarity: supports or contradicts.
        strength: exact, strong, or weak.
        source_ref: Reference identifying field/occurrence (e.g. 'title', 'comment:42').
        text_span: Exact substring extracted from ticket text, or None.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    raw_source = source.value if isinstance(source, EvidenceSource) else str(source)
    raw_polarity = polarity.value if isinstance(polarity, EvidencePolarity) else str(polarity)
    raw_strength = strength.value if isinstance(strength, EvidenceStrength) else str(strength)

    payload = {
        "candidate_key": candidate_key,
        "polarity": raw_polarity,
        "provider": provider_name,
        "snapshot_hash": snapshot_hash,
        "source": raw_source,
        "source_ref": source_ref,
        "strength": raw_strength,
        "text_span": text_span or "",
    }

    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
