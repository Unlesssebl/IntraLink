"""Create an anonymized, read-only evaluation dataset from historical JSONL.

Input is an export made by the approved IntraService reporting job. This tool
does not contact IntraService and never writes to its source. Its output must
live outside Git and is safe to pass to the offline evaluator after predictions
are attached by the evaluated RAG/LLM build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.ai.sanitizer import data_sanitizer


def _first(row: dict, *keys: str) -> object:
    return next((row[key] for key in keys if row.get(key) is not None), None)


def _iso_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("closed_at is required")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()


def _anon_id(value: object, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:24]


def convert(row: dict, salt: str) -> dict:
    task_id = _first(row, "task_id", "Id", "id")
    if task_id is None:
        raise ValueError("task id is required")
    query = "\n".join(
        str(value).strip()
        for value in (_first(row, "name", "Name", "title"), _first(row, "problem", "description", "Description"))
        if value
    )
    if not query:
        raise ValueError("ticket query is required")
    sanitized = data_sanitizer.sanitize(query).sanitized_text
    output = {
        "id": _anon_id(task_id, salt),
        "closed_at": _iso_timestamp(_first(row, "closed_at", "ClosedAt", "completed_at")),
        "query": sanitized,
        "expected_ids": [_anon_id(task_id, salt)],
        "expected_status_id": _first(row, "status_id", "StatusId"),
        "expected_action": _first(row, "expected_action", "action"),
    }
    # Do not leak absent fields as null-heavy noise.
    return {key: value for key, value in output.items() if value is not None}


def main() -> int:
    parser = argparse.ArgumentParser(description="Anonymize historical ticket JSONL for offline eval")
    parser.add_argument("--source", type=Path, required=True, help="read-only historical JSONL export")
    parser.add_argument("--output", type=Path, required=True, help="destination outside Git")
    parser.add_argument("--salt-env", default="EVAL_EXPORT_SALT")
    args = parser.parse_args()
    salt = os.getenv(args.salt_env)
    if not salt:
        print(f"Set {args.salt_env}; a stable secret salt is required", file=sys.stderr)
        return 2
    try:
        source_rows = [json.loads(line) for line in args.source.read_text(encoding="utf-8").splitlines() if line.strip()]
        converted = [convert(row, salt) for row in source_rows]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot export evaluation dataset: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in converted), encoding="utf-8")
    print(f"Exported {len(converted)} anonymized records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
