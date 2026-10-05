"""Secret-safe canonical ticket snapshots for the ADR 0006 automation engine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from core.automation.contracts import (
    OuRoutingCategoryItem,
    OuRoutingContext,
    SnapshotAttachment,
    SnapshotComment,
    TicketSnapshot,
)
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


def _is_autopilot_comment(comment: Any) -> bool:
    text = str(_value(comment, "comment", "Comment", "text", "Text", default="")).strip()
    return text.startswith("[AUTO:")


def _is_comment_private(comment: Any) -> bool:
    if isinstance(comment, dict):
        if comment.get("is_private") or comment.get("IsPrivate") or comment.get("IsPrivateComment"):
            return True
        is_pub = comment.get("IsPublic")
        if is_pub is None:
            is_pub = comment.get("is_public")
        if is_pub is not None and (is_pub is False or str(is_pub).lower() in ("false", "0")):
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
        is_pub = getattr(comment, "is_public", getattr(comment, "IsPublic", None))
        if is_pub is not None and (is_pub is False or str(is_pub).lower() in ("false", "0")):
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


class TicketSnapshotFactory:
    """Build immutable snapshots without private comments, secrets, or attachment bodies."""

    @classmethod
    def create(
        cls,
        task: TaskDTO,
        comments: Sequence[Any] | None = None,
        last_event_id: int | None = None,
        applicant_company_hierarchy: Sequence[str] | None = None,
        ou_routing_context: OuRoutingContext | None = None,
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
            if _is_comment_private(comment) or _is_autopilot_comment(comment):
                continue
            raw_id = _value(comment, "id", "Id")
            comment_id: int | None = None
            if raw_id is not None:
                try:
                    comment_id = int(raw_id)
                    all_event_ids.append(comment_id)
                except (TypeError, ValueError):
                    pass
            text = str(_value(comment, "comment", "Comment", "text", "Text", default="")).strip()
            if not text:
                continue
            created = _value(comment, "created", "Created", "created_at", "CreatedAt")
            created_at = (
                created.isoformat()
                if isinstance(created, datetime)
                else (str(created) if created is not None else None)
            )
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
                    event_key=_value(comment, "event_key", "EventKey"),
                    text=text,
                    created_at=created_at,
                    author_name=author,
                    editor_id=_value(comment, "editor_id", "EditorId"),
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
        categories = (
            getattr(task, "categories", None)
            or entities.get("categories")
            or None
        )
        applicant_company = (
            getattr(task, "creator_company_name", None)
            or entities.get("applicant_company")
            or None
        )
        applicant_login = (
            getattr(task, "creator_login", None)
            or entities.get("applicant_login")
            or None
        )
        applicant_department = (
            getattr(task, "creator_department", None)
            or getattr(task, "applicant_department", None)
            or entities.get("applicant_department")
            or None
        )
        if ou_routing_context is None:
            ou_routing_context = _build_default_ou_routing_context(
                task, applicant_company_hierarchy=applicant_company_hierarchy
            )

        payload = {
            "task_id": task.id,
            "status_id": task.status_id,
            "service_id": task.service_id,
            "service_name": task.service_name,
            "task_type_id": task.task_type_id,
            "applicant_id": task.applicant_id,
            "title": task.name,
            "description": task.description,
            "public_comments": [item.model_dump() for item in public_comments],
            "custom_fields": custom_fields,
            "entities": entities,
            "attachments": [item.model_dump() for item in attachments],
            "categories": categories,
            "applicant_company": applicant_company,
            "applicant_login": applicant_login,
            "applicant_department": applicant_department,
            "last_event_id": event_id,
            "ou_routing_context": ou_routing_context.model_dump(),
        }
        return TicketSnapshot(
            **payload,
            snapshot_hash=compute_canonical_snapshot_hash(payload),
        )


def build_ou_routing_context(
    task: TaskDTO,
    *,
    routing_config: Any | None = None,
    applicant_company_hierarchy: Sequence[str] | None = None,
    policy_hash: str | None = None,
    policy_version: int | None = None,
) -> OuRoutingContext:
    from core.intraservice.dto import normalize_category_pairs

    norm_cats = getattr(task, "normalized_categories", None)
    if not norm_cats and (getattr(task, "category_ids", None) or getattr(task, "categories", None)):
        norm_cats = normalize_category_pairs(
            category_ids=getattr(task, "category_ids", None),
            categories=getattr(task, "categories", None),
        )

    cfg = routing_config
    if cfg is None:
        try:
            from core.ad.ou_routing import get_cached_routing_config
            res = get_cached_routing_config()
            if res:
                cfg = res[0]
                if policy_hash is None:
                    policy_hash = res[1]
                if policy_version is None:
                    policy_version = res[2]
        except Exception:
            pass

    diagnostics: list[str] = []
    cat_items: list[OuRoutingCategoryItem] = []

    service_rule = None
    if cfg is not None and getattr(cfg, "services", None):
        svc_id = getattr(task, "service_id", 53) or 53
        service_rule = cfg.services.get(svc_id)

    for cat in (norm_cats or []):
        mismatch = False
        if service_rule and cat.id in service_rule.categories:
            rule = service_rule.categories[cat.id]
            if rule.expected_names and cat.name:
                import unicodedata
                norm_cname = unicodedata.normalize("NFKC", cat.name).casefold().strip()
                match = any(
                    unicodedata.normalize("NFKC", exp).casefold().strip() == norm_cname
                    for exp in rule.expected_names
                )
                if not match:
                    mismatch = True
                    diagnostics.append(f"category_name_mismatch:{cat.id}")

        cat_items.append(OuRoutingCategoryItem(id=cat.id, name=cat.name, name_mismatch=mismatch))

    app_comp = getattr(task, "creator_company_name", None)
    app_dept = getattr(task, "creator_department", None) or getattr(task, "applicant_department", None)
    app_path = getattr(task, "creator_company_path", None)
    hierarchy = list(applicant_company_hierarchy or [])

    if not cat_items and (getattr(task, "categories", None) or getattr(task, "category_ids", None)):
        diagnostics.append("unparsed_categories")
    if not app_comp and not app_path:
        diagnostics.append("missing_applicant_company")

    return OuRoutingContext(
        categories=cat_items,
        applicant_company=app_comp,
        applicant_department=app_dept,
        applicant_company_hierarchy=hierarchy,
        applicant_company_path=app_path,
        routing_policy_version=policy_version,
        routing_policy_hash=policy_hash,
        diagnostics=diagnostics,
    )


def _build_default_ou_routing_context(
    task: TaskDTO,
    applicant_company_hierarchy: Sequence[str] | None = None,
) -> OuRoutingContext:
    return build_ou_routing_context(
        task,
        applicant_company_hierarchy=applicant_company_hierarchy,
    )


build_ticket_snapshot = TicketSnapshotFactory.create
compute_snapshot_hash = compute_canonical_snapshot_hash
