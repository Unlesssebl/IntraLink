"""Unit tests for TicketSnapshot and TicketSnapshotFactory.

Verifies canonical SHA-256 hash stability, field sensitivity, sanitization of
passwords/secrets, exclusion of private comments, and immutability of the source TaskDTO.
"""

from datetime import UTC, datetime

from core.intraservice.dto import (
    AttachmentDTO,
    ExtractedEntitiesDTO,
    TaskCommentDTO,
    TaskDTO,
)
from core.routing.snapshot import TicketSnapshotFactory


def _create_sample_task(
    id: int = 42,
    name: str = "Установка принтера HP LaserJet",
    description: str = "Прошу установить принтер в кабинете 305",
    service_id: int | None = 233,
    service_name: str | None = "Оборудование",
    status_id: int = 1,
    custom_fields: dict[str, str] | None = None,
    entities: ExtractedEntitiesDTO | None = None,
    attachments: list[AttachmentDTO] | None = None,
) -> TaskDTO:
    return TaskDTO(
        id=id,
        name=name,
        description=description,
        service_id=service_id,
        service_name=service_name,
        status_id=status_id,
        custom_fields=custom_fields or {"Room": "305", "PrinterModel": "HP P2035"},
        entities=entities or ExtractedEntitiesDTO(room="305", pc_name="KZM-WS001"),
        attachments=attachments
        or [
            AttachmentDTO(id=101, name="photo.png", size=2048),
        ],
    )


def test_snapshot_hash_stability():
    """Identical ticket data produces identical deterministic canonical SHA-256 hash."""
    task1 = _create_sample_task()
    task2 = _create_sample_task()

    snap1 = TicketSnapshotFactory.create(task1)
    snap2 = TicketSnapshotFactory.create(task2)

    assert snap1.snapshot_hash == snap2.snapshot_hash
    assert len(snap1.snapshot_hash) == 64


def test_hash_changes_on_significant_field_mutation():
    """Hash changes when significant fields (description, service_id, title) change."""
    base_task = _create_sample_task()
    base_snap = TicketSnapshotFactory.create(base_task)

    # 1. Changed description
    task_diff_desc = _create_sample_task(description="Совершенно другое описание проблемы")
    snap_diff_desc = TicketSnapshotFactory.create(task_diff_desc)
    assert snap_diff_desc.snapshot_hash != base_snap.snapshot_hash

    # 2. Changed service_id
    task_diff_service = _create_sample_task(service_id=40)
    snap_diff_service = TicketSnapshotFactory.create(task_diff_service)
    assert snap_diff_service.snapshot_hash != base_snap.snapshot_hash

    # 3. Changed title
    task_diff_title = _create_sample_task(name="Заблокировалась учетная запись")
    snap_diff_title = TicketSnapshotFactory.create(task_diff_title)
    assert snap_diff_title.snapshot_hash != base_snap.snapshot_hash


def test_custom_fields_order_does_not_affect_hash():
    """Dictionary key order in custom_fields does not affect canonical hash."""
    task_a = _create_sample_task(
        custom_fields={"zeta": "100", "alpha": "200", "beta": "300"}
    )
    task_b = _create_sample_task(
        custom_fields={"alpha": "200", "beta": "300", "zeta": "100"}
    )

    snap_a = TicketSnapshotFactory.create(task_a)
    snap_b = TicketSnapshotFactory.create(task_b)

    assert snap_a.snapshot_hash == snap_b.snapshot_hash


def test_private_comments_text_strictly_excluded_but_updates_last_event_id():
    """Private comments are excluded from public_comments text, but update last_event_id watermark."""
    task = _create_sample_task()
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)

    public_comment = TaskCommentDTO(
        id=1,
        text="Публичное сообщение пользователю",
        author_name="Инженер",
        created_at=now,
        is_private=False,
    )
    private_comment = TaskCommentDTO(
        id=2,
        text="Скрытая техническая заметка автопилота",
        author_name="Автопилот",
        created_at=now,
        is_private=True,
    )

    snap_only_public = TicketSnapshotFactory.create(task, comments=[public_comment])
    snap_both = TicketSnapshotFactory.create(task, comments=[public_comment, private_comment])

    # Public comments list contains ONLY public comments
    assert len(snap_both.public_comments) == 1
    assert snap_both.public_comments[0].text == "Публичное сообщение пользователю"
    assert snap_both.public_comments[0].is_private is False

    # Private text is strictly absent from snapshot model dump
    assert "Скрытая техническая заметка автопилота" not in snap_both.model_dump_json()

    # last_event_id tracks the newest event (including private)
    assert snap_only_public.last_event_id == 1
    assert snap_both.last_event_id == 2

    # New event ID increments snapshot hash (OCC watermark guard)
    assert snap_both.snapshot_hash != snap_only_public.snapshot_hash


def test_it_password_absent_in_serialized_snapshot():
    """entities.it_password is completely removed and absent from serialized JSON."""
    task = _create_sample_task(
        entities=ExtractedEntitiesDTO(
            pc_name="KZM-WS001",
            it_login="ivanov.i",
            it_password="SuperSecretPassword123!",
        )
    )

    snapshot = TicketSnapshotFactory.create(task)
    dumped_json = snapshot.model_dump_json()

    assert "SuperSecretPassword123!" not in dumped_json
    assert "it_password" not in snapshot.entities
    assert "it_password" not in dumped_json
    assert snapshot.entities.get("it_login") == "ivanov.i"


def test_field1489_and_secret_keys_excluded():
    """Fields named 1489, Field1489, and password/token keywords are excluded."""
    task = _create_sample_task(
        custom_fields={
            "1489": "SecretPlainPass",
            "Field1489": "AnotherSecretPass",
            "user_password": "MyPassword999",
            "auth_token": "bearer-xyz-12345",
            "Пароль_от_1С": "1c_password_val",
            "secret_code": "424242",
            "ValidKey": "NormalValue",
        }
    )

    snapshot = TicketSnapshotFactory.create(task)
    dumped_json = snapshot.model_dump_json()

    # Secret values must be absent
    assert "SecretPlainPass" not in dumped_json
    assert "AnotherSecretPass" not in dumped_json
    assert "MyPassword999" not in dumped_json
    assert "bearer-xyz-12345" not in dumped_json
    assert "1c_password_val" not in dumped_json
    assert "424242" not in dumped_json

    # Non-secret key preserved
    assert snapshot.custom_fields == {"ValidKey": "NormalValue"}


def test_attachment_metadata_preserved_content_absent():
    """Attachment id, name, and size are recorded, but file content is omitted."""
    task = _create_sample_task(
        attachments=[
            AttachmentDTO(id=777, name="error_log.txt", size=1048576),
        ]
    )

    snapshot = TicketSnapshotFactory.create(task)
    assert len(snapshot.attachments) == 1
    att = snapshot.attachments[0]
    assert att.id == 777
    assert att.name == "error_log.txt"
    assert att.size == 1048576
    # SnapshotAttachment has extra="forbid" and only id, name, size
    assert not hasattr(att, "content")


def test_source_task_dto_not_mutated():
    """Original TaskDTO must not be mutated by TicketSnapshotFactory."""
    original_entities = ExtractedEntitiesDTO(
        pc_name="KZM-WS001",
        it_login="petrov.p",
        it_password="OriginalSecretPassword!",
    )
    original_custom_fields = {
        "Field1489": "SensitiveValue",
        "SafeField": "SafeValue",
    }
    task = _create_sample_task(
        entities=original_entities,
        custom_fields=original_custom_fields,
    )

    snapshot = TicketSnapshotFactory.create(task)

    # Factory output is sanitized
    assert "it_password" not in snapshot.entities
    assert "Field1489" not in snapshot.custom_fields

    # Source TaskDTO remains untouched
    assert task.entities.it_password == "OriginalSecretPassword!"
    assert task.custom_fields["Field1489"] == "SensitiveValue"
    assert task.custom_fields["SafeField"] == "SafeValue"


def test_task_lifetime_events_dto_support_and_last_event_id():
    """TicketSnapshotFactory supports TaskLifetimeEventDTO with comment, created, editor and last_event_id."""
    from core.intraservice.dto import TaskLifetimeEventDTO

    task = _create_sample_task()

    event1 = TaskLifetimeEventDTO(
        id=501,
        task_id=42,
        created="2026-09-26T10:00:00",
        editor="Сидоров С.С.",
        comment="Пользователь сообщил, что принтер не виден в сети",
        is_private=False,
    )
    event2 = TaskLifetimeEventDTO(
        id=502,
        task_id=42,
        created="2026-09-26T10:05:00",
        user_name="Автопилот",
        comment="Автопилот: запуск диагностики сетевого хоста",
        is_private=True,
    )
    event3 = TaskLifetimeEventDTO(
        id=503,
        task_id=42,
        created="2026-09-26T10:10:00",
        editor="Сидоров С.С.",
        status_id=3,
        comment=None,  # Event without comment (status change)
        is_private=False,
    )

    snapshot = TicketSnapshotFactory.create(task, comments=[event1, event2, event3])

    # 1. Private event is excluded, empty-comment event is excluded
    assert len(snapshot.public_comments) == 1
    comm = snapshot.public_comments[0]
    assert comm.id == 501
    assert comm.text == "Пользователь сообщил, что принтер не виден в сети"
    assert comm.author_name == "Сидоров С.С."
    assert comm.created_at == "2026-09-26T10:00:00"

    # 2. last_event_id is computed from max of all events (503)
    assert snapshot.last_event_id == 503


def test_task_comment_security_group_ids_payload_forms_normalize_to_private():
    """Regression test: TaskCommentSecurityGroupIDs in all real IntraService payload forms mark events private."""
    from core.intraservice.dto import TaskCommentDTO, TaskLifetimeEventDTO

    # 1. Raw dicts with various forms of TaskCommentSecurityGroupIDs
    raw_payloads = [
        {"Id": 101, "Comment": "SECRET_INTRA_INT", "TaskCommentSecurityGroupIDs": 5},
        {"Id": 102, "Comment": "SECRET_INTRA_STR", "TaskCommentSecurityGroupIDs": "12, 14"},
        {"Id": 103, "Comment": "SECRET_INTRA_LIST", "TaskCommentSecurityGroupIDs": [1, 2]},
        {"Id": 104, "Comment": "SECRET_INTRA_FLOAT", "TaskCommentSecurityGroupIDs": 4.0},
        {"Id": 105, "Comment": "SECRET_INTRA_BOOL", "IsPrivateComment": True},
        {"Id": 106, "Comment": "PUBLIC_INTRA_EMPTY_LIST", "TaskCommentSecurityGroupIDs": []},
        {"Id": 107, "Comment": "PUBLIC_INTRA_NULL", "TaskCommentSecurityGroupIDs": None},
        {"Id": 108, "Comment": "PUBLIC_INTRA_ZERO_STR", "TaskCommentSecurityGroupIDs": "   "},
    ]

    events = [TaskLifetimeEventDTO.model_validate(p) for p in raw_payloads]

    assert events[0].is_private is True
    assert events[1].is_private is True
    assert events[2].is_private is True
    assert events[3].is_private is True
    assert events[4].is_private is True
    assert events[5].is_private is False
    assert events[6].is_private is False
    assert events[7].is_private is False

    task = _create_sample_task()
    snapshot = TicketSnapshotFactory.create(task, comments=events)

    # All private comments text MUST NOT appear anywhere in public_comments
    all_public_text = " ".join(c.text for c in snapshot.public_comments)
    assert "SECRET_INTRA_INT" not in all_public_text
    assert "SECRET_INTRA_STR" not in all_public_text
    assert "SECRET_INTRA_LIST" not in all_public_text
    assert "SECRET_INTRA_FLOAT" not in all_public_text
    assert "SECRET_INTRA_BOOL" not in all_public_text

    # Public comments are present
    assert "PUBLIC_INTRA_EMPTY_LIST" in all_public_text
    assert "PUBLIC_INTRA_NULL" in all_public_text
    assert "PUBLIC_INTRA_ZERO_STR" in all_public_text

    # last_event_id must be max across ALL events including private (108)
    assert snapshot.last_event_id == 108

    # Also test TaskCommentDTO model validation
    c_int = TaskCommentDTO.model_validate({"Id": 201, "Text": "SECRET_COMM_INT", "TaskCommentSecurityGroupIDs": 3})
    assert c_int.is_private is True
    c_str = TaskCommentDTO.model_validate({"Id": 202, "Text": "SECRET_COMM_STR", "TaskCommentSecurityGroupIDs": "5"})
    assert c_str.is_private is True
    c_null = TaskCommentDTO.model_validate({"Id": 203, "Text": "PUBLIC_COMM", "TaskCommentSecurityGroupIDs": None})
    assert c_null.is_private is False

