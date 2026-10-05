from __future__ import annotations

import pytest
from core.automation.contracts import TicketSnapshot
from core.automation.snapshot import TicketSnapshotFactory
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO


def _make_task(task_id: int = 142801) -> TaskDTO:
    return TaskDTO(
        id=task_id,
        name="Заявка на создание пользователя",
        description="Создать учетную запись для нового сотрудника",
        service_id=55,
        service_name="Заявка на пользователя",
        task_type_id=1018,
        status_id=31,
        applicant_id=123,
        custom_fields={"1057": "Иванов", "1058": "Иван"},
    )


def test_private_and_autopilot_comments_do_not_mutate_snapshot_hash() -> None:
    task = _make_task()

    # Initial events (e.g. status opened, assignment)
    events_initial = [
        TaskLifetimeEventDTO(
            id=1001,
            task_id=task.id,
            status_id=31,
            new_status_name="Открыта",
            is_private=False,
        ),
        TaskLifetimeEventDTO(
            id=1002,
            task_id=task.id,
            executors="Bot",
            is_private=False,
        ),
    ]

    snap1 = TicketSnapshotFactory.create(task, comments=events_initial)
    assert snap1.last_event_id == 1002

    # Autopilot posts a private audit note into IntraService
    events_with_audit = [
        *events_initial,
        TaskLifetimeEventDTO(
            id=1003,
            task_id=task.id,
            comment="[AUTO:run-1] Требуется действие оператора в Intralink.",
            is_private=True,
        ),
    ]

    snap2 = TicketSnapshotFactory.create(task, comments=events_with_audit)

    # The snapshot hash and last_event_id MUST remain identical to snap1
    assert snap2.last_event_id == 1002
    assert snap2.snapshot_hash == snap1.snapshot_hash
    assert len(snap2.public_comments) == 0


def test_public_applicant_comment_mutates_snapshot_hash() -> None:
    task = _make_task()

    events_initial = [
        TaskLifetimeEventDTO(
            id=1001,
            task_id=task.id,
            status_id=31,
            is_private=False,
        ),
    ]
    snap1 = TicketSnapshotFactory.create(task, comments=events_initial)

    # Applicant writes a public message
    events_with_applicant = [
        *events_initial,
        TaskLifetimeEventDTO(
            id=1002,
            task_id=task.id,
            comment="Пожалуйста, добавьте еще доступ в папку Обмен",
            is_private=False,
        ),
    ]
    snap2 = TicketSnapshotFactory.create(task, comments=events_with_applicant)

    assert snap2.last_event_id == 1002
    assert snap2.snapshot_hash != snap1.snapshot_hash
    assert len(snap2.public_comments) == 1
    assert snap2.public_comments[0].text == "Пожалуйста, добавьте еще доступ в папку Обмен"
