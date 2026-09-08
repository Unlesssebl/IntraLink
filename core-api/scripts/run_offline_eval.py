"""Run an offline RAG/LLM release gate over an anonymized JSONL dataset.

Each row must contain the evaluated build's retrieved_ids, predicted_status_id,
predicted_action, effective_mode and dlp_safe fields. The script never calls an
LLM, IntraService or an executor; it only evaluates captured predictions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.evals import DatasetError, apply_release_gate, evaluate, temporal_split, validate_records


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline quality gate for IntraLink AI automation")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--min-cases", type=int, default=100)
    args = parser.parse_args()

    try:
        rows = validate_records(read_jsonl(args.dataset), args.min_cases)
        split = temporal_split(rows)
        report = evaluate(split["test"])
        report["split"] = {name: len(items) for name, items in split.items()}
        baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
        report["gate"] = apply_release_gate(report, baseline)
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"Invalid evaluation dataset: {exc}", file=sys.stderr)
        return 2

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.write_baseline and report["gate"]["passed"]:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
