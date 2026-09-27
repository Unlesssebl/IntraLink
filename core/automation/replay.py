"""Closed offline acceptance replay for versioned ADR 0006 datasets."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.automation.capabilities import get_default_capability_registry
from core.automation.case_profiles import CaseProfileRegistry
from core.automation.case_router import CaseRouter
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import (
    AssertionKind,
    CaseAssertion,
    CaseCandidate,
    CaseDecision,
    CaseDecisionState,
    CaseFrame,
    ExtractionMethod,
    TicketSnapshot,
)
from core.automation.frame_extractor import CaseFrameExtractor
from core.automation.intake import CaseIntakeEngine
from core.automation.snapshot import compute_canonical_snapshot_hash
from core.automation.workflows import get_default_workflow_registry


@dataclass(frozen=True)
class ReplayScore:
    dataset: str
    total: int
    exact: int
    accuracy: float
    failures: list[dict[str, Any]]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
    return rows


def _snapshot(payload: dict[str, Any]) -> TicketSnapshot:
    data = {
        "task_id": payload["task_id"],
        "status_id": payload.get("status_id", 2),
        "service_id": payload.get("service_id"),
        "service_name": payload.get("service_name"),
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
        "public_comments": payload.get("public_comments", []),
        "custom_fields": payload.get("custom_fields", {}),
        "entities": payload.get("entities", {}),
        "attachments": payload.get("attachments", []),
        "last_event_id": payload.get("last_event_id"),
    }
    return TicketSnapshot(**data, snapshot_hash=compute_canonical_snapshot_hash(data))


async def replay_intake(path: Path) -> ReplayScore:
    rows = _read_jsonl(path)
    extractor = CaseFrameExtractor()
    router = CaseRouter(CaseProfileRegistry())
    intake = CaseIntakeEngine(extractor, router)
    failures: list[dict[str, Any]] = []
    for row in rows:
        snapshot = _snapshot(row["ticket"])
        _, decision = await intake.analyze(snapshot)
        actual = {
            "state": decision.state.value,
            "primary_case_type": decision.primary_case_type,
            "secondary_case_types": decision.secondary_case_types,
        }
        expected = row["expected"]
        normalized_expected = {
            "state": expected["state"],
            "primary_case_type": expected.get("primary_case_type"),
            "secondary_case_types": expected.get("secondary_case_types", []),
        }
        if actual != normalized_expected:
            failures.append({"id": row["id"], "expected": normalized_expected, "actual": actual})
    exact = len(rows) - len(failures)
    return ReplayScore("intake", len(rows), exact, exact / len(rows) if rows else 0.0, failures)


async def replay_action_selection(path: Path) -> ReplayScore:
    rows = _read_jsonl(path)
    compiler = WorkflowCompiler(get_default_workflow_registry(), get_default_capability_registry())
    failures: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        snapshot_hash = f"{index + 1:064x}"
        assertions = [
            CaseAssertion(
                id=f"dataset:{row['id']}:{item_index}",
                kind=AssertionKind(item["kind"]),
                key=item["key"],
                value=str(item.get("value", "true")),
                source_ref="dataset",
                text_span=None,
                extraction_method=ExtractionMethod.operator,
            )
            for item_index, item in enumerate(row.get("assertions", []))
        ]
        frame = CaseFrame(
            task_id=100000 + index,
            snapshot_hash=snapshot_hash,
            frame_version="offline-dataset-v1",
            assertions=assertions,
            entities={key: str(value) for key, value in row.get("facts", {}).items()},
        )
        case_type = row["case_type"]
        decision = CaseDecision(
            id=uuid4(),
            task_id=frame.task_id,
            snapshot_hash=snapshot_hash,
            frame_id=frame.id,
            router_version="offline-ground-truth-v1",
            state=CaseDecisionState.selected,
            primary_case_type=case_type,
            candidates=[CaseCandidate(case_type=case_type, case_type_version="1.0.0")],
            reason_codes=["offline_ground_truth"],
        )
        workflow, action_plan = compiler.compile(frame=frame, decision=decision)
        actual = {
            "workflow_key": workflow.workflow_key,
            "disposition": workflow.disposition.value,
            "state": workflow.state.value,
            "capabilities": [item.capability_key for item in action_plan.actions] if action_plan else [],
            "missing_facts": workflow.missing_facts,
        }
        expected = {
            "workflow_key": row["expected"]["workflow_key"],
            "disposition": row["expected"]["disposition"],
            "state": row["expected"]["state"],
            "capabilities": row["expected"].get("capabilities", []),
            "missing_facts": row["expected"].get("missing_facts", []),
        }
        if actual != expected:
            failures.append({"id": row["id"], "expected": expected, "actual": actual})
    exact = len(rows) - len(failures)
    return ReplayScore("action_selection", len(rows), exact, exact / len(rows) if rows else 0.0, failures)


async def run_replay(dataset_root: Path) -> list[ReplayScore]:
    return [
        await replay_intake(dataset_root / "intake.jsonl"),
        await replay_action_selection(dataset_root / "action_selection.jsonl"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=Path("datasets/automation/v1"))
    parser.add_argument("--min-accuracy", type=float, default=1.0)
    args = parser.parse_args()
    scores = asyncio.run(run_replay(args.dataset_root))
    print(json.dumps([asdict(score) for score in scores], ensure_ascii=False, indent=2))
    return 0 if all(score.accuracy >= args.min_accuracy for score in scores) else 1


if __name__ == "__main__":
    raise SystemExit(main())
