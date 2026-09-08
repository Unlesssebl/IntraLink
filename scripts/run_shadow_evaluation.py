"""Script for offline batch shadow evaluation on ticket samples.

Compares legacy heuristics against the new TicketRunOrchestrator decisions
without executing any side effects (strictly read-only).
"""

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Ensure repository root and core-api are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "core-api"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("shadow_evaluation")


async def run_evaluation(
    limit: int = 50,
    service_id: int | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    from app.database.db import AsyncSessionLocal, init_db, TicketRun, engine
    from app.services.scenario_orchestrator import TicketRunOrchestrator
    from app.services.scenarios.shadow_comparator import ShadowComparator
    from sqlalchemy import select

    if engine.dialect.name == "sqlite":
        await init_db()

    output_path = output_path or (REPO_ROOT / "docs" / "shadow_evaluation_report.md")

    async with AsyncSessionLocal() as db:
        query = select(TicketRun).order_by(TicketRun.created_at.desc()).limit(limit)
        if service_id is not None:
            # фильтруем по service_id если возможно через snapshot
            pass

        runs = list((await db.scalars(query)).all())
        logger.info("Found %d ticket runs to evaluate in shadow mode", len(runs))

        orchestrator = TicketRunOrchestrator(db)
        results = []
        total = 0
        matched = 0
        diverged = 0

        for run in runs:
            total += 1
            task = run.trigger_snapshot_json.get("task") or {
                "Id": run.task_id,
                "ServiceId": run.trigger_snapshot_json.get("service_id", 0),
                "Name": run.trigger_snapshot_json.get("task_name", f"Ticket #{run.task_id}"),
            }
            legacy_key = run.scenario_key or run.trigger_snapshot_json.get("scenario_key")

            try:
                envelope = await orchestrator.observe(
                    run_id=run.id,
                    task=task,
                    comments=[],
                )
                comp = ShadowComparator.compare(
                    legacy_scenario_key=legacy_key,
                    envelope=envelope,
                    task=task,
                )
                if comp.matched:
                    matched += 1
                else:
                    diverged += 1

                results.append({
                    "task_id": run.task_id,
                    "legacy_scenario": legacy_key,
                    "new_scenario": envelope.scenario_key,
                    "outcome_kind": envelope.outcome.kind,
                    "confidence": envelope.confidence,
                    "matched": comp.matched,
                    "reasons": comp.divergence_reasons,
                })
            except Exception as exc:
                diverged += 1
                results.append({
                    "task_id": run.task_id,
                    "legacy_scenario": legacy_key,
                    "new_scenario": "error",
                    "outcome_kind": "exception",
                    "confidence": 0.0,
                    "matched": False,
                    "reasons": [f"evaluation_exception: {exc}"],
                })

    div_rate = round((diverged / total) * 100, 2) if total else 0.0
    match_rate = round((matched / total) * 100, 2) if total else 0.0

    # Генерируем Markdown-отчет
    report_lines = [
        "# Отчет теневого прогона (Shadow Evaluation Report)",
        "",
        f"- **Дата запуска:** {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"- **Проанализировано заявок:** {total}",
        f"- **Совпало с ожиданиями (Match Rate):** {match_rate}% ({matched}/{total})",
        f"- **Процент расхождений (Divergence Rate):** {div_rate}% ({diverged}/{total})",
        "",
        "## Сводная таблица результатов",
        "",
        "| Ticket ID | Legacy Сценарий | Новый Сценарий | Исход Оркестратора | Confidence | Статус | Причины расхождения |",
        "| :--- | :--- | :--- | :--- | :--- | :---: | :--- |",
    ]

    for item in results:
        status_icon = "✅ Совпало" if item["matched"] else "⚠️ Расхождение"
        reasons_str = "; ".join(item["reasons"]) if item["reasons"] else "—"
        report_lines.append(
            f"| #{item['task_id']} | `{item['legacy_scenario']}` | `{item['new_scenario']}` | `{item['outcome_kind']}` | {item['confidence']:.2f} | {status_icon} | {reasons_str} |"
        )

    report_lines.append("")
    report_lines.append("## Рекомендация по Canary Rollout")
    if div_rate <= 5.0 and total > 0:
        report_lines.append("> [!TIP]\n> Уровень расхождений ниже 5%. Рекомендуется безопасный запуск Canary 5% -> 25%.")
    else:
        report_lines.append("> [!WARNING]\n> Требуется аудит расхождений перед переводом канарейки выше 10%.")

    report_content = "\n".join(report_lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_content, encoding="utf-8")
    logger.info("Report saved to %s", output_path)

    return {
        "total": total,
        "matched": matched,
        "diverged": diverged,
        "divergence_rate": div_rate,
        "output_path": str(output_path),
    }


def main():
    parser = argparse.ArgumentParser(description="Run batch shadow evaluation on tickets")
    parser.add_argument("--limit", type=int, default=50, help="Maximum tickets to evaluate")
    parser.add_argument("--service-id", type=int, default=None, help="Filter by ServiceId")
    parser.add_argument("--output", type=str, default=None, help="Output markdown report path")
    args = parser.parse_args()

    out = Path(args.output) if args.output else None
    summary = asyncio.run(run_evaluation(limit=args.limit, service_id=args.service_id, output_path=out))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
