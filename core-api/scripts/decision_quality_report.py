#!/usr/bin/env python3
"""
Скрипт генерации отчета о качестве решений Helpdesk и экспорта кандидатов для регрессионной выборки (Этап 5 Roadmap).

Использование:
  python core-api/scripts/decision_quality_report.py --days 30
  python core-api/scripts/decision_quality_report.py --days 30 --markdown report.md
  python core-api/scripts/decision_quality_report.py --days 30 --export-candidates core-api/tests/candidates/
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

# Добавляем путь к core-api для импортов
current_dir = Path(__file__).resolve().parent
core_api_dir = current_dir.parent
repo_root = core_api_dir.parent
sys.path.insert(0, str(core_api_dir))
sys.path.insert(0, str(repo_root))

from app.database.db import (
    AsyncSessionLocal,
    DecisionApplication,
    DecisionApplicationAttempt,
    DecisionFeedback,
    DecisionRecord,
)
from app.services.decision_analytics import DecisionFeedbackAnalyticsService
from app.services.decision_journal import sanitize_payload


async def generate_report_and_export(
    *,
    days: int,
    start_iso: str | None,
    end_iso: str | None,
    scenario_key: str | None,
    analysis_revision: str | None,
    output_markdown: str | None,
    output_json: str | None,
    export_candidates_dir: str | None,
) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    if start_iso and end_iso:
        start_date = dt.datetime.fromisoformat(start_iso)
        end_date = dt.datetime.fromisoformat(end_iso)
    else:
        end_date = now
        start_date = now - dt.timedelta(days=days)

    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=dt.timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=dt.timezone.utc)

    print(f"=== Отчет о качестве решений IntraLink (Этап 5) ===")
    print(f"Период: {start_date.isoformat()} — {end_date.isoformat()}")

    async with AsyncSessionLocal() as db:
        analytics = DecisionFeedbackAnalyticsService(db)
        metrics = await analytics.get_quality_metrics(
            start_date=start_date,
            end_date=end_date,
            scenario_key=scenario_key,
            analysis_revision=analysis_revision,
        )

        # Вывод в консоль
        totals = metrics["totals"]
        m = metrics["metrics"]
        print(f"\nОбщая статистика:")
        print(f"  Всего решений в БД: {totals['total_decisions']} (Triage: {totals['total_decisions_triage']}, Failed: {totals['total_decisions_failed']})")
        print(f"  Всего подтвержденных применений: {totals['total_applications']} (Triage: {totals['total_applications_triage']}, Manual: {totals['total_applications_manual']}, Command: {totals['total_applications_command']})")
        print(f"\nМетрики качества рекомендаций:")
        print(f"  N (Eligible Recommendation Attempts): {m['eligible_recommendation_applications']}")
        print(f"  Acceptance Rate (согласие со статусом и текстом): {m['acceptance_rate'] if m['acceptance_rate'] is not None else 'N/A'}")
        print(f"  Status Override Rate (изменения статуса): {m['status_override_rate'] if m['status_override_rate'] is not None else 'N/A'}")
        print(f"  Comment Edit Rate (правки текста ответа): {m['comment_edit_rate'] if m['comment_edit_rate'] is not None else 'N/A'}")
        print(f"  Feedback Coverage: {m['feedback_coverage'] if m['feedback_coverage'] is not None else 'N/A'}")

        print(f"\nЯвные отклонения и замечания:")
        exp = metrics["explicit_feedback"]
        print(f"  Уникальных отклоненных решений (rejected): {exp['unique_rejected_decisions']}")
        print(f"  Всего сообщений explicit feedback: {exp['total_explicit_feedback']}")

        if metrics["scenario_breakdown"]:
            print(f"\nРазбивка по сценариям:")
            for sc in metrics["scenario_breakdown"]:
                warn = " [insufficient data (<5)]" if sc["insufficient_data"] else ""
                print(f"  - {sc['scenario_key']} (v{sc['scenario_version']}): N={sc['total']}, Acceptance={sc['acceptance_rate']}{warn}")

        # Экспорт кандидатов
        if export_candidates_dir:
            out_dir = Path(export_candidates_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            print(f"\nЭкспорт кандидатов для регрессионного тестирования в: {out_dir}...")

            from sqlalchemy import select

            # Выбираем feedback с rejected или правками
            fb_stmt = (
                select(DecisionFeedback, DecisionRecord)
                .join(DecisionRecord, DecisionRecord.id == DecisionFeedback.decision_id)
                .where(
                    DecisionFeedback.created_at >= start_date,
                    DecisionFeedback.created_at < end_date,
                )
            )
            rows = list((await db.execute(fb_stmt)).all())

            exported_count = 0
            manifest = []
            for fb, dec in rows:
                fa = fb.final_action_json or {}
                cdiff = fa.get("comment_diff") or {}
                ratio = cdiff.get("ratio") or 0.0
                is_candidate = (
                    fb.verdict in {"rejected", "modified"}
                    or fa.get("status_changed")
                    or ratio >= 0.4
                    or fb.reason_code in {"wrong_scenario", "ungrounded_rag"}
                )
                if not is_candidate:
                    continue

                candidate_id = f"cand_{dec.task_id}_{dec.version}_{fb.id.hex[:8]}"
                candidate_data = {
                    "candidate_id": candidate_id,
                    "review_status": "needs_review",  # Инвариант: только экспертная разметка переводит в эталон!
                    "exported_at": now.isoformat(),
                    "task_id": dec.task_id,
                    "decision_id": str(dec.id),
                    "decision_version": dec.version,
                    "scenario_key": (dec.envelope_json or {}).get("scenario_key"),
                    "scenario_version": (dec.envelope_json or {}).get("scenario_version"),
                    "analysis_revision": (dec.context_json or {}).get("analysis_revision"),
                    "inputs": sanitize_payload((dec.context_json or {}).get("task") or {}),
                    "proposed_envelope": sanitize_payload(dec.envelope_json or {}),
                    "feedback": {
                        "verdict": fb.verdict,
                        "reason_code": fb.reason_code,
                        "reason_source": fb.reason_source,
                        "operator_reason_code": fb.operator_reason_code,
                        "operator_comment": fb.comment,
                        "actor": fb.actor,
                        "source": fb.source,
                        "comment_diff_ratio": ratio,
                    },
                    "final_action": sanitize_payload(fa),
                }

                cand_file = out_dir / f"{candidate_id}.json"
                with open(cand_file, "w", encoding="utf-8") as f:
                    json.dump(candidate_data, f, ensure_ascii=False, indent=2)

                manifest.append(
                    {
                        "candidate_id": candidate_id,
                        "task_id": dec.task_id,
                        "verdict": fb.verdict,
                        "reason_code": fb.reason_code,
                        "file": str(cand_file.name),
                    }
                )
                exported_count += 1

            manifest_file = out_dir / "manifest.json"
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "generated_at": now.isoformat(),
                        "period_start": start_date.isoformat(),
                        "period_end": end_date.isoformat(),
                        "total_candidates": exported_count,
                        "items": manifest,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            print(f"Экспортировано кандидатов: {exported_count}. Манифест: {manifest_file}")

        # Сохранение отчета в JSON
        if output_json:
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(metrics, f, ensure_ascii=False, indent=2)
            print(f"JSON-отчет сохранен: {output_json}")

        # Сохранение отчета в Markdown
        if output_markdown:
            md_content = f"""# Отчет о качестве решений IntraLink (Этап 5 Roadmap)

**Период**: {start_date.isoformat()} — {end_date.isoformat()}  
**Аналитическая ревизия**: {analysis_revision or 'Все'}  
**Сценарий**: {scenario_key or 'Все'}  

## Сводные метрики

| Метрика | Значение | Описание |
|---|---|---|
| **Всего решений в БД** | {totals['total_decisions']} | Уникальные карточки анализа за период |
| **Успешных применений** | {totals['total_applications']} | Зафиксированные выполнения действий |
| **Релевантных рекомендаций (N)** | {m['eligible_recommendation_applications']} | Знаменатель оценки согласия (сопоставимый feedback) |
| **Acceptance Rate** | {f"{m['acceptance_rate']*100:.1f}%" if m['acceptance_rate'] is not None else 'N/A'} | Доля решений, принятых без изменения статуса и текста |
| **Status Override Rate** | {f"{m['status_override_rate']*100:.1f}%" if m['status_override_rate'] is not None else 'N/A'} | Доля ручной смены целевого статуса |
| **Comment Edit Rate** | {f"{m['comment_edit_rate']*100:.1f}%" if m['comment_edit_rate'] is not None else 'N/A'} | Доля ручной правки текста ответа заявителю |
| **Feedback Coverage** | {f"{m['feedback_coverage']*100:.1f}%" if m['feedback_coverage'] is not None else 'N/A'} | Охват подтвержденных действий аудитом |
| **Явных отклонений (rejected)** | {exp['unique_rejected_decisions']} | Решения, явно забракованные инженерами |

## Распределение причин правок (Operator)
```json
{json.dumps(metrics['reason_distribution']['operator'], ensure_ascii=False, indent=2)}
```

## Исключения (Exclusions)
- Ручные применения без рекомендации (manual_apply): {metrics['exclusions']['manual_apply']}
- Исторические записи (legacy): {metrics['exclusions']['legacy']}
- Несопоставимые тексты: {metrics['exclusions']['uncomparable']}
"""
            with open(output_markdown, "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"Markdown-отчет сохранен: {output_markdown}")


def main():
    parser = argparse.ArgumentParser(description="Отчет о качестве решений Helpdesk")
    parser.add_argument("--days", type=int, default=30, help="Период в днях (по умолчанию: 30)")
    parser.add_argument("--start", type=str, default=None, help="Начало периода (ISO)")
    parser.add_argument("--end", type=str, default=None, help="Конец периода (ISO)")
    parser.add_argument("--scenario", type=str, default=None, help="Фильтр по ключу сценария")
    parser.add_argument("--revision", type=str, default=None, help="Фильтр по ревизии анализа")
    parser.add_argument("--output-json", type=str, default=None, help="Путь для сохранения JSON")
    parser.add_argument("--markdown", type=str, default=None, help="Путь для сохранения Markdown")
    parser.add_argument("--export-candidates", type=str, default=None, help="Каталог для экспорта кандидатов")
    args = parser.parse_args()

    asyncio.run(
        generate_report_and_export(
            days=args.days,
            start_iso=args.start,
            end_iso=args.end,
            scenario_key=args.scenario,
            analysis_revision=args.revision,
            output_markdown=args.markdown,
            output_json=args.output_json,
            export_candidates_dir=args.export_candidates,
        )
    )


if __name__ == "__main__":
    main()
