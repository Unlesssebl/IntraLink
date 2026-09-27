"""Verifier Privacy Router and Free-Text DLP Sanitizer.

Enforces zero-leakage DLP masking on untrusted ticket text, determines privacy
zones (RED / YELLOW / GREEN), selects model execution tiers (helpdesk-local vs
helpdesk-fast) and computes deterministic input hashes.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Dict, List, Optional, Sequence, Set

from pydantic import Field

from core.rag.sanitizer import PIISanitizer
from core.routing.contracts import ScenarioCandidate, TicketSnapshot
from core.routing.verifier.contracts import (
    PrivacyZone,
    VerifierBaseModel,
)

SANITIZER_VERSION = "dlp-verifier-v1"

# RED zone scenario keys by policy
RED_SCENARIO_KEYS = frozenset({"account_create", "account_lock", "grant_wlan"})

# Regex for internal IPv4 addresses
_INTERNAL_IPV4_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
)

# Regex for workstation / server hostname patterns
_HOSTNAME_RE = re.compile(
    r"\b(?:WKS|SRV|PC|DESKTOP|LAPTOP|NB|VM)-[A-Za-z0-9_\-]+\b|\b[A-Za-z0-9_\-]+\.(?:corporate\.loc|corp\.lan|local|corp|domain)\b",
    re.IGNORECASE,
)


class SanitizedComment(VerifierBaseModel):
    """Sanitized public comment representation with preserved identifier."""

    id: Optional[int] = None
    text: str


class PreparedVerifierInput(VerifierBaseModel):
    """Minimal, sanitized, privacy-routed input contract for LLM Verifier."""

    service_id: Optional[int] = None
    service_name: Optional[str] = None
    title: str
    description: str
    public_comments: List[SanitizedComment] = Field(default_factory=list)
    privacy_zone: PrivacyZone
    requested_model_alias: str
    detected_sensitive_types: List[str] = Field(default_factory=list)
    sanitizer_version: str
    sanitized_input_hash: str


class VerifierPrivacyRouter:
    """Privacy router with strict DLP masking and zero cloud fallback for RED zone."""

    def __init__(
        self,
        cloud_model_alias: str = "helpdesk-fast",
        local_model_alias: str = "helpdesk-local",
    ) -> None:
        self.cloud_model_alias = cloud_model_alias
        self.local_model_alias = local_model_alias
        self._pii_sanitizer = PIISanitizer(max_length=3000)

    def _sanitize_string(
        self,
        text: str,
        entities: Dict[str, str],
        detected_types: Set[str],
    ) -> str:
        """Sanitize a single text string using PII sanitizer, regexes and known entities."""
        if not text or not text.strip():
            return ""

        # 1. Base PII sanitizer (HTML cleanup, password, token, email, phone)
        res = self._pii_sanitizer.sanitize(text)
        clean = res.sanitized_text
        for dt in res.detected_types:
            detected_types.add(dt)

        # 2. Mask internal IPv4 addresses
        if _INTERNAL_IPV4_RE.search(clean):
            detected_types.add("INTERNAL_IP")
            clean = _INTERNAL_IPV4_RE.sub("[INTERNAL_IP]", clean)

        # 3. Mask hostname / workstation names
        if _HOSTNAME_RE.search(clean):
            detected_types.add("HOST")
            clean = _HOSTNAME_RE.sub("[HOST]", clean)

        # 4. Mask known identity and host values from snapshot entities
        # Sort values descending by length to avoid partial matches
        entity_items: List[tuple[str, str]] = []
        for k, v in entities.items():
            val = str(v).strip()
            if len(val) >= 3:
                entity_items.append((k, val))

        entity_items.sort(key=lambda item: len(item[1]), reverse=True)

        for key, val in entity_items:
            k_lower = key.lower()
            if k_lower in ("pc_name", "host", "hostname", "target_host"):
                tag = "[HOST]"
                type_name = "HOST"
            elif k_lower in ("email", "mail"):
                tag = "[EMAIL]"
                type_name = "EMAIL"
            elif k_lower in ("phone", "mobile", "telephone"):
                tag = "[PHONE]"
                type_name = "PHONE"
            else:
                tag = "[USER]"
                type_name = "USER"

            # Case-insensitive word-boundary or escaped literal replacement
            pattern = re.compile(re.escape(val), re.IGNORECASE)
            if pattern.search(clean):
                detected_types.add(type_name)
                clean = pattern.sub(tag, clean)

        return clean

    def prepare(
        self,
        snapshot: TicketSnapshot,
        candidates: Sequence[ScenarioCandidate],
    ) -> PreparedVerifierInput:
        """Prepare sanitized verifier input and evaluate privacy routing zone.

        Strict invariants:
        - NEVER includes custom_fields, entities, attachments, raw passwords, or execution parameters;
        - RED zone always assigned when PASSWORD/TOKEN is detected or sensitive scenarios are proposed;
        - RED zone strictly routes to local_model_alias with zero cloud fallback;
        - sanitized_input_hash is stable and deterministic.
        """
        detected_types: Set[str] = set()
        entities = snapshot.entities or {}

        # Sanitize allowed text fields
        sanitized_service_name = (
            self._sanitize_string(snapshot.service_name, entities, detected_types)
            if snapshot.service_name
            else None
        )
        sanitized_title = self._sanitize_string(snapshot.title, entities, detected_types)
        sanitized_description = self._sanitize_string(snapshot.description, entities, detected_types)

        sanitized_comments: List[SanitizedComment] = []
        for c in snapshot.public_comments:
            if c.is_private:
                continue
            c_text = self._sanitize_string(c.text, entities, detected_types)
            sanitized_comments.append(SanitizedComment(id=c.id, text=c_text))

        # Determine privacy zone
        candidate_keys = {c.scenario_key for c in candidates}
        has_red_scenario = bool(candidate_keys & RED_SCENARIO_KEYS)
        has_credentials = ("PASSWORD" in detected_types) or ("TOKEN" in detected_types)

        if has_credentials or has_red_scenario:
            zone = PrivacyZone.red
            model_alias = self.local_model_alias
        elif detected_types & {"USER", "EMAIL", "PHONE", "INTERNAL_IP", "HOST"}:
            zone = PrivacyZone.yellow
            model_alias = self.cloud_model_alias
        else:
            zone = PrivacyZone.green
            model_alias = self.cloud_model_alias

        sorted_detected = sorted(detected_types)

        # Compute deterministic canonical input hash
        hash_payload = {
            "service_id": snapshot.service_id,
            "service_name": sanitized_service_name,
            "title": sanitized_title,
            "description": sanitized_description,
            "public_comments": [{"id": sc.id, "text": sc.text} for sc in sanitized_comments],
            "privacy_zone": zone.value,
            "requested_model_alias": model_alias,
            "sanitizer_version": SANITIZER_VERSION,
        }
        canonical_json = json.dumps(
            hash_payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        input_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

        return PreparedVerifierInput(
            service_id=snapshot.service_id,
            service_name=sanitized_service_name,
            title=sanitized_title,
            description=sanitized_description,
            public_comments=sanitized_comments,
            privacy_zone=zone,
            requested_model_alias=model_alias,
            detected_sensitive_types=sorted_detected,
            sanitizer_version=SANITIZER_VERSION,
            sanitized_input_hash=input_hash,
        )
