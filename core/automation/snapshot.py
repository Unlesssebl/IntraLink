"""Secret-safe canonical ticket snapshots for the ADR 0006 automation engine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from core.automation.contracts import SnapshotAttachment, SnapshotComment, TicketSnapshot
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO

SECRET_KEYWORDS = ("password", "passwd", "пароль", "secret", "token")
SECRET_EXACT_KEYS = {"1489", "field1489"}


def compute_canonical_snapshot_hash(payload: Any) -> str:
    """Return the deterministic SHA-256 hash of a snapshot payload."""
    if isinstance(payload, TicketSnapshot):
        return payload.snapshot_hash
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    clean_data = {key: value for key, value in payload.items() if key != "snapshot_hash"}
    canonical_json = json.dumps(
        clean_data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _is_comment_private(comment: Any) -> bool:
    if isinstance(comment, dict):
        if comment.get("is_private") or comment.get("IsPrivate") or comment.get("IsPrivateComment"):
            return True
        security = comment.get("TaskCommentSecurityGroupIDs")
        if security is None:
            security = comment.get("security_group_ids")
    else:
        if (
            getattr(comment, "is_private", False)
            or getattr(comment, "IsPrivateComment", False)
            or getattr(comment, "IsPrivate", False)
        ):
            return True
        security = getattr(comment, "TaskCommentSecurityGroupIDs", None)
        if security is None:
            security = getattr(comment, "security_group_ids", None)
    if security is None:
        return False
    if isinstance(security, (int, float)):
        return security != 0
    if isinstance(security, str):
        return bool([item.strip() for item in security.split(",") if item.strip()])
    return isinstance(security, (list, tuple, set)) and bool(security)


def _value(item: Any, *names: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        for name in names:
            value = item.get(name)
            if value is not None:
                return value
        return default
    for name in names:
        value = getattr(item, name, None)
        if value is not None:
            return value
    return default


class TicketSnapshotFactory:
    """Build immutable snapshots without private comments, secrets, or attachment bodies."""

    @classmethod
    def create(
        cls,
        task: TaskDTO,
        comments: Sequence[Any] | None = None,
        last_event_id: int | None = None,
    ) -> TicketSnapshot:
        if isinstance(task.entities, ExtractedEntitiesDTO):
            raw_entities = task.entities.model_dump()
        elif isinstance(task.entities, dict):
            raw_entities = task.entities
        else:
            raw_entities = {}
        entities = {
            str(key): str(value)
            for key, value in raw_entities.items()
            if key != "it_password" and value is not None and str(value) != ""
        }

        custom_fields: dict[str, str] = {}
        for key, value in (task.custom_fields or {}).items():
            normalized = str(key).strip()
            lower = normalized.lower()
            if lower in SECRET_EXACT_KEYS or any(marker in lower for marker in SECRET_KEYWORDS):
                continue
            custom_fields[normalized] = str(value) if value is not None else ""

        public_comments: list[SnapshotComment] = []
        all_event_ids: list[int] = []
        for comment in comments or []:
            raw_id = _value(comment, "id", "Id")
            comment_id: int | None = None
            if raw_id is not None:
                try:
                    comment_id = int(raw_id)
                    all_event_ids.append(comment_id)
                except (TypeError, ValueError):
                    pass
            if _is_comment_private(comment):
                continue
            text = str(_value(comment, "comment", "Comment", "text", "Text", default="")).strip()
            if not text:
                continue
            created = _value(comment, "created", "Created", "created_at", "CreatedAt")
            created_at = created.isoformat() if isinstance(created, datetime) else (str(created) if created is not None else None)
            author = str(
                _value(
                    comment,
                    "author_name",
                    "AuthorName",
                    "editor",
                    "Editor",
                    "user_name",
                    "UserName",
                    default="",
                )
            ).strip()
            public_comments.append(
                SnapshotComment(
                    id=comment_id,
                    text=text,
                    created_at=created_at,
                    author_name=author,
                )
            )

        event_id = last_event_id if last_event_id is not None else (max(all_event_ids) if all_event_ids else None)
        attachments = [
            SnapshotAttachment(
                id=int(_value(item, "id", "Id", default=0)),
                name=str(_value(item, "name", "Name", default="")),
                size=int(_value(item, "size", "Size", default=0)),
            )
            for item in (task.attachments or [])
        ]
        payload = {
            "task_id": task.id,
            "status_id": task.status_id,
            "service_id": task.service_id,
            "service_name": task.service_name,
            "title": task.name,
            "description": task.description,
            "public_comments": [item.model_dump() for item in public_comments],
            "custom_fields": custom_fields,
            "entities": entities,
            "attachments": [item.model_dump() for item in attachments],
            "last_event_id": event_id,
        }
        return TicketSnapshot(
            **payload,
            snapshot_hash=compute_canonical_snapshot_hash(payload),
        )


build_ticket_snapshot = TicketSnapshotFactory.create
compute_snapshot_hash = compute_canonical_snapshot_hash
