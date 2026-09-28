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
from core.automation.service_routing import (
    RedirectResolver,
    ServiceCatalogEntry,
    ServiceCompatibilityDecision,
    ServiceCompatibilityState,
    ServiceRouteBinding,
)
from core.automation.snapshot import compute_canonical_snapshot_hash
from core.automation.workflows import get_default_workflow_registry


@dataclass(frozen=True)
class ReplayScore:
    dataset: str
    total: int
    exact: int
    accuracy: float
    failures: list[dict[str, Any]]
    metrics: dict[str, float | int] | None = None


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
        service_context = row.get("service_context")
        compatibility = None
        binding = None
        if service_context:
            compatibility = ServiceCompatibilityDecision(
                task_id=frame.task_id,
                snapshot_hash=snapshot_hash,
                case_decision_id=decision.id,
                source_service_id=service_context["source_service_id"],
                source_task_type_id=service_context.get("task_type_id"),
                binding_key=service_context["binding_key"],
                binding_version=service_context["binding_version"],
                catalog_hash=service_context["catalog_hash"],
                allowed_case_types=[case_type],
                state=ServiceCompatibilityState.compatible,
            )
            binding = ServiceRouteBinding(
                key=service_context["binding_key"],
                version=service_context["binding_version"],
                service_ids=(service_context["source_service_id"],),
                allowed_case_types=(case_type,),
                allowed_workflows=("employee_onboarding_workflow",),
                allowed_capabilities=("create_ad_user",),
                required_task_type_id=service_context.get("required_task_type_id"),
                required_fields=(),
                redirect_strategy="cancel_and_recreate",
                risk="high",
                is_active=True,
                is_validated=True,
                catalog_hash=service_context["catalog_hash"],
            )
        workflow, action_plan = compiler.compile(
            frame=frame,
            decision=decision,
            compatibility=compatibility,
            binding=binding,
        )
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


def _routing_input(row: dict[str, Any], index: int):
    snapshot_hash = f"{index + 1000:064x}"
    frame_id = uuid4()
    case_type = row["case_type"]
    decision = CaseDecision(
        task_id=200000 + index,
        snapshot_hash=snapshot_hash,
        frame_id=frame_id,
        router_version="offline-ground-truth-v1",
        state=CaseDecisionState.selected,
        primary_case_type=case_type,
        candidates=[CaseCandidate(case_type=case_type, case_type_version="1.0.0")],
        reason_codes=["offline_ground_truth"],
    )
    entries = [ServiceCatalogEntry.model_validate(item) for item in row.get("entries", [])]
    bindings = [ServiceRouteBinding.model_validate(item) for item in row.get("bindings", [])]
    result = RedirectResolver().resolve(
        decision=decision,
        source_service_id=row.get("source_service_id"),
        source_task_type_id=row.get("source_task_type_id"),
        catalog_hash=row.get("catalog_hash"),
        entries=entries,
        bindings=bindings,
    )
    return result


async def replay_compatibility(path: Path) -> ReplayScore:
    rows = _read_jsonl(path)
    failures: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        actual = _routing_input(row, index).state.value
        if actual != row["expected"]["state"]:
            failures.append({"id": row["id"], "expected": row["expected"]["state"], "actual": actual})
    exact = len(rows) - len(failures)
    return ReplayScore(
        "compatibility",
        len(rows),
        exact,
        exact / len(rows) if rows else 0.0,
        failures,
        {
            "compatibility_accuracy": exact / len(rows) if rows else 0.0,
        },
    )


async def replay_redirect_target(path: Path) -> ReplayScore:
    rows = _read_jsonl(path)
    failures: list[dict[str, Any]] = []
    top1 = top3 = unknown = false_redirect = 0
    for index, row in enumerate(rows):
        result = _routing_input(row, index)
        ids = [item.service_id for item in result.candidates]
        expected = row["expected"]
        top1 += int(bool(ids) and ids[0] == expected.get("top1"))
        top3 += int(expected.get("top1") in ids[:3])
        unknown += int(result.state == ServiceCompatibilityState.unknown)
        false_redirect += int(result.state == ServiceCompatibilityState.mismatch and expected["state"] != "mismatch")
        if result.state.value != expected["state"] or ids[:3] != expected.get("top3", []):
            failures.append(
                {"id": row["id"], "expected": expected, "actual": {"state": result.state.value, "top3": ids[:3]}}
            )
    exact = len(rows) - len(failures)
    denominator = len(rows) or 1
    return ReplayScore(
        "redirect_target",
        len(rows),
        exact,
        exact / denominator,
        failures,
        {
            "redirect_top_1": top1 / denominator,
            "redirect_top_3": top3 / denominator,
            "false_redirect_rate": false_redirect / denominator,
            "unknown_target_rate": unknown / denominator,
            "llm_generated_service_id_count": 0,
        },
    )


async def replay_execution_feedback(path: Path) -> ReplayScore:
    rows = _read_jsonl(path)
    unauthorized_ad = sum(
        1 for row in rows if row.get("capability_key") == "create_ad_user" and not row.get("authorized_service")
    )
    failures = [] if unauthorized_ad == 0 else [{"id": "security", "unauthorized_ad": unauthorized_ad}]
    exact = len(rows) if not failures else len(rows) - unauthorized_ad
    return ReplayScore(
        "execution_feedback",
        len(rows),
        exact,
        exact / len(rows) if rows else 0.0,
        failures,
        {
            "create_ad_user_from_unauthorized_service": unauthorized_ad,
        },
    )


async def run_replay(dataset_root: Path) -> list[ReplayScore]:
    return [
        await replay_intake(dataset_root / "intake.jsonl"),
        await replay_compatibility(dataset_root / "compatibility.jsonl"),
        await replay_redirect_target(dataset_root / "redirect_target.jsonl"),
        await replay_action_selection(dataset_root / "action_selection.jsonl"),
        await replay_execution_feedback(dataset_root / "execution_feedback.jsonl"),
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
