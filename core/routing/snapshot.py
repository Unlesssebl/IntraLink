"""Safe TicketSnapshot factory for Evidence-Based Routing Cascade.

Builds normalized, sanitized, immutable snapshots of IntraService tickets with
cryptographic canonical SHA-256 integrity hashes, ensuring secrets and private
data are strictly filtered out without mutating the original TaskDTO.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.routing.contracts import (
    SnapshotAttachment,
    SnapshotComment,
    TicketSnapshot,
)

# Secret detection constants
SECRET_KEYWORDS = ("password", "passwd", "пароль", "secret", "token")
SECRET_EXACT_KEYS = {"1489", "field1489"}


def compute_canonical_snapshot_hash(payload: Any) -> str:
    """Compute deterministic SHA-256 hex digest from canonical JSON.

    - UTF-8 encoded;
    - ensure_ascii=False;
    - sort_keys=True for strict key ordering;
    - compact stable separators (',', ':');
    - excludes 'snapshot_hash' key if present.
    """
    if isinstance(payload, TicketSnapshot):
        return payload.snapshot_hash

    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")

    clean_data = {k: v for k, v in payload.items() if k != "snapshot_hash"}
    canonical_json = json.dumps(
        clean_data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _is_comment_private(c: Any) -> bool:
    """Check if comment or lifetime event is marked as private or restricted by security groups."""
    if isinstance(c, dict):
        if c.get("is_private") or c.get("IsPrivate") or c.get("IsPrivateComment"):
            return True
        sec = c.get("TaskCommentSecurityGroupIDs")
        if sec is None:
            sec = c.get("security_group_ids")
        if sec is not None:
            if isinstance(sec, (int, float)) and sec != 0:
                return True
            if isinstance(sec, str) and [s.strip() for s in sec.split(",") if s.strip()]:
                return True
            if isinstance(sec, (list, tuple, set)) and len(sec) > 0:
                return True
        return False

    if (
        getattr(c, "is_private", False)
        or getattr(c, "IsPrivateComment", False)
        or getattr(c, "IsPrivate", False)
    ):
        return True

    sec = getattr(c, "TaskCommentSecurityGroupIDs", None)
    if sec is None:
        sec = getattr(c, "security_group_ids", None)
    if sec is not None:
        if isinstance(sec, (int, float)) and sec != 0:
            return True
        if isinstance(sec, str) and [s.strip() for s in sec.split(",") if s.strip()]:
            return True
        if isinstance(sec, (list, tuple, set)) and len(sec) > 0:
            return True
    return False


class TicketSnapshotFactory:
    """Factory creating secure point-in-time TicketSnapshot instances from TaskDTO."""

    @classmethod
    def create(
        cls,
        task: TaskDTO,
        comments: Sequence[Any] | None = None,
        last_event_id: int | None = None,
    ) -> TicketSnapshot:
        """Create a sanitized TicketSnapshot with canonical hash.

        Args:
            task: Source TaskDTO (remains unmutated).
            comments: Optional sequence of comments (TaskCommentDTO, TaskLifetimeEventDTO, or similar).
            last_event_id: Optional audit event id watermark.

        Returns:
            Sanitized, immutable TicketSnapshot.
        """
        # 1. Sanitize entities - exclude it_password completely
        sanitized_entities: dict[str, str] = {}
        if isinstance(task.entities, ExtractedEntitiesDTO):
            raw_entities = task.entities.model_dump()
        elif isinstance(task.entities, dict):
            raw_entities = task.entities
        else:
            raw_entities = {}

        for k, v in raw_entities.items():
            if k == "it_password":
                continue
            if v is not None and str(v) != "":
                sanitized_entities[k] = str(v)

        # 2. Sanitize custom_fields - filter out sensitive fields (1489, password, token, etc.)
        sanitized_custom_fields: dict[str, str] = {}
        for k, v in (task.custom_fields or {}).items():
            k_str = str(k).strip()
            k_lower = k_str.lower()
            if k_lower in SECRET_EXACT_KEYS:
                continue
            if any(secret_kw in k_lower for secret_kw in SECRET_KEYWORDS):
                continue
            sanitized_custom_fields[k_str] = str(v) if v is not None else ""

        # 3. Filter public comments - strictly omit private comments and empty texts,
        # but calculate last_event_id watermark across all lifetime events (including private)
        public_comments: list[SnapshotComment] = []
        all_event_ids: list[int] = []
        if comments:
            for c in comments:
                c_id = getattr(c, "id", None) or getattr(c, "Id", None)
                if isinstance(c, dict) and c_id is None:
                    c_id = c.get("id") or c.get("Id")
                c_id_int: int | None = None
                if c_id is not None:
                    try:
                        c_id_int = int(c_id)
                        all_event_ids.append(c_id_int)
                    except (ValueError, TypeError):
                        pass

                if _is_comment_private(c):
                    continue

                # Text can be stored in 'comment' (TaskLifetimeEventDTO) or 'text' (TaskCommentDTO)
                raw_text = (
                    getattr(c, "comment", None)
                    or getattr(c, "Comment", None)
                    or getattr(c, "text", None)
                    or getattr(c, "Text", None)
                    or ""
                )
                text_str = str(raw_text).strip()
                if not text_str:
                    continue

                created_raw = (
                    getattr(c, "created", None)
                    or getattr(c, "Created", None)
                    or getattr(c, "created_at", None)
                    or getattr(c, "CreatedAt", None)
                )
                if isinstance(created_raw, datetime):
                    created_str = created_raw.isoformat()
                elif created_raw is not None:
                    created_str = str(created_raw)
                else:
                    created_str = None

                author = (
                    getattr(c, "author_name", None)
                    or getattr(c, "AuthorName", None)
                    or getattr(c, "editor", None)
                    or getattr(c, "Editor", None)
                    or getattr(c, "user_name", None)
                    or getattr(c, "UserName", None)
                    or ""
                )
                author_str = str(author).strip()

                public_comments.append(
                    SnapshotComment(
                        id=c_id_int,
                        text=text_str,
                        created_at=created_str,
                        author_name=author_str,
                        is_private=False,
                    )
                )

        effective_last_event_id = last_event_id
        if effective_last_event_id is None and all_event_ids:
            effective_last_event_id = max(all_event_ids)

        # 5. Attachments metadata only (no content)
        attachments: list[SnapshotAttachment] = []
        for att in task.attachments or []:
            att_id = getattr(att, "id", None) or getattr(att, "Id", 0)
            att_name = getattr(att, "name", "") or getattr(att, "Name", "")
            att_size = getattr(att, "size", 0) or getattr(att, "Size", 0)
            attachments.append(
                SnapshotAttachment(
                    id=att_id,
                    name=att_name,
                    size=att_size,
                )
            )

        # 6. Build payload for canonical hashing
        payload = {
            "task_id": task.id,
            "status_id": task.status_id,
            "service_id": task.service_id,
            "service_name": task.service_name,
            "title": task.name,
            "description": task.description,
            "public_comments": [c.model_dump() for c in public_comments],
            "custom_fields": sanitized_custom_fields,
            "entities": sanitized_entities,
            "attachments": [a.model_dump() for a in attachments],
            "last_event_id": effective_last_event_id,
        }

        snapshot_hash = compute_canonical_snapshot_hash(payload)

        # 7. Return immutable TicketSnapshot
        return TicketSnapshot(
            task_id=task.id,
            status_id=task.status_id,
            service_id=task.service_id,
            service_name=task.service_name,
            title=task.name,
            description=task.description,
            public_comments=public_comments,
            custom_fields=sanitized_custom_fields,
            entities=sanitized_entities,
            attachments=attachments,
            last_event_id=effective_last_event_id,
            snapshot_hash=snapshot_hash,
        )


# Functional aliases for backwards-compatibility
build_ticket_snapshot = TicketSnapshotFactory.create
compute_snapshot_hash = compute_canonical_snapshot_hash
