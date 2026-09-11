"""
Сервис аналитики качества решений и обратной связи операторов (Этап 5 Roadmap).
Содержит DecisionFeedbackAnalyticsService, вычисляющий детерминированные метрики качества:
- total_decisions, total_applications, execution_breakdown
- eligible_recommendation_applications (N)
- acceptance_rate, status_override_rate, comment_edit_rate, feedback_coverage
- explicit_rejections, reason_distribution, operator_reason_distribution
- scenario_breakdown с флагом insufficient_data
- exclusions: manual_apply, legacy, uncomparable
"""

from __future__ import annotations

import datetime as dt
from typing import Any
import uuid

from fastapi import HTTPException, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import (
    DecisionApplication,
    DecisionApplicationAttempt,
    DecisionFeedback,
    DecisionRecord,
)


class DecisionFeedbackAnalyticsService:
    def __init__(
        self,
        db: AsyncSession | None = None,
        session_factory: Any | None = None,
    ):
        self.db = db
        self.session_factory = session_factory

    async def get_quality_metrics(  # noqa: C901, PLR0912, PLR0915
        self,
        *,
        start_date: dt.datetime,
        end_date: dt.datetime,
        scenario_key: str | None = None,
        scenario_version: int | None = None,
        analysis_revision: str | None = None,
    ) -> dict[str, Any]:
        if self.db is not None:
            return await self._get_quality_metrics_impl(
                self.db,
                start_date=start_date,
                end_date=end_date,
                scenario_key=scenario_key,
                scenario_version=scenario_version,
                analysis_revision=analysis_revision,
            )
        if self.session_factory is not None:
            async with self.session_factory() as session:
                return await self._get_quality_metrics_impl(
                    session,
                    start_date=start_date,
                    end_date=end_date,
                    scenario_key=scenario_key,
                    scenario_version=scenario_version,
                    analysis_revision=analysis_revision,
                )
        raise RuntimeError("Neither db nor session_factory configured for DecisionFeedbackAnalyticsService")

    async def get_quality_summary(self, days: int = 30) -> dict[str, Any]:
        now = dt.datetime.now(dt.timezone.utc)
        start_date = now - dt.timedelta(days=days)
        metrics = await self.get_quality_metrics(start_date=start_date, end_date=now)
        sample_size = metrics["metrics"]["eligible_recommendation_applications"]
        acc_rate = metrics["metrics"]["acceptance_rate"] or 0.0
        rej_count = metrics["explicit_feedback"]["unique_rejected_decisions"]
        rej_rate = round(rej_count / sample_size, 4) if sample_size > 0 else 0.0

        top_rejected = []
        for scen in metrics.get("scenario_breakdown", []):
            if scen.get("total", 0) > 0:
                top_rejected.append({
                    "scenario_id": scen["scenario_key"],
                    "total_applications": scen["total"],
                    "rejected_count": scen["total"] - scen["accepted"],
                    "rejection_rate": round(1.0 - (scen["acceptance_rate"] or 1.0), 4),
                })

        return {
            "window_days": days,
            "since_date": start_date.isoformat(),
            "sample_size": sample_size,
            "acceptance_rate": acc_rate,
            "rejection_rate": rej_rate,
            "status_override_rate": metrics["metrics"]["status_override_rate"] or 0.0,
            "comment_edit_rate": metrics["metrics"]["comment_edit_rate"] or 0.0,
            "feedback_coverage": metrics["metrics"]["feedback_coverage"] or 0.0,
            "top_rejected_scenarios": top_rejected,
            "reconciliation_summary": metrics["reconciliation_summary"],
            "raw_metrics": metrics,
        }

    async def _get_quality_metrics_impl(  # noqa: C901, PLR0912, PLR0915
        self,
        session: AsyncSession,
        *,
        start_date: dt.datetime,
        end_date: dt.datetime,
        scenario_key: str | None = None,
        scenario_version: int | None = None,
        analysis_revision: str | None = None,
    ) -> dict[str, Any]:
        if start_date >= end_date:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "start_date must be strictly before end_date",
            )
        if (end_date - start_date).days > 366:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "date range must not exceed 366 days",
            )

        # -------------------------------------------------------------------
        # 1. Total Decisions
        # -------------------------------------------------------------------
        dec_stmt = select(DecisionRecord).where(
            DecisionRecord.created_at >= start_date,
            DecisionRecord.created_at < end_date,
        )
        dec_records = list((await session.scalars(dec_stmt)).all())

        total_decisions_triage = 0
        total_decisions_operational = 0
        total_decisions_failed = 0

        # Фильтруем решения по метаданным сценария, если заданы
        filtered_dec_ids: set[uuid.UUID] = set()
        for d in dec_records:
            env = d.envelope_json or {}
            ctx = d.context_json or {}
            d_scen_key = env.get("scenario_key")
            d_scen_ver = env.get("scenario_version")
            d_rev = ctx.get("analysis_revision")

            if scenario_key and d_scen_key != scenario_key:
                continue
            if scenario_version and d_scen_ver != scenario_version:
                continue
            if analysis_revision and d_rev != analysis_revision:
                continue

            filtered_dec_ids.add(d.id)
            if d.status == "failed":
                total_decisions_failed += 1
            elif d.analysis_kind == "triage":
                total_decisions_triage += 1
            else:
                total_decisions_operational += 1

        # -------------------------------------------------------------------
        # 2. Total Applications & Execution Breakdown
        # -------------------------------------------------------------------
        app_stmt = select(DecisionApplication).where(
            DecisionApplication.created_at >= start_date,
            DecisionApplication.created_at < end_date,
        )
        apps = list((await session.scalars(app_stmt)).all())

        app_command_count = 0
        app_triage_count = 0
        app_manual_count = 0

        exec_breakdown = {
            "succeeded": 0,
            "partial_failure": 0,
            "failed": 0,
            "needs_review": 0,
            "command_executions": 0,
            "manual_applications": 0,
            "triage_applications": 0,
        }

        for app in apps:
            if app.decision_id and filtered_dec_ids and app.decision_id not in filtered_dec_ids:
                continue
            if app.application_type == "command_execution":
                app_command_count += 1
                exec_breakdown["command_executions"] += 1
            elif app.status == "applied_manual":
                app_manual_count += 1
                exec_breakdown["manual_applications"] += 1
            else:
                app_triage_count += 1
                exec_breakdown["triage_applications"] += 1

            if app.status in {"applied_unmodified", "applied_modified", "applied_manual"}:
                exec_breakdown["succeeded"] += 1
            elif app.status == "partial_failure":
                exec_breakdown["partial_failure"] += 1
            elif app.status == "needs_review":
                exec_breakdown["needs_review"] += 1
            elif app.status == "failed":
                exec_breakdown["failed"] += 1

        # -------------------------------------------------------------------
        # 3. Eligible Recommendation Attempts, Acceptance & Edits
        # -------------------------------------------------------------------
        # Берем attempts, завершенные в интервале
        att_stmt = select(DecisionApplicationAttempt).where(
            DecisionApplicationAttempt.completed_at >= start_date,
            DecisionApplicationAttempt.completed_at < end_date,
        )
        attempts = list((await session.scalars(att_stmt)).all())

        # Читаем feedback, привязанный к этим attempts
        attempt_ids = [att.id for att in attempts]
        feedbacks_by_attempt: dict[uuid.UUID, DecisionFeedback] = {}
        if attempt_ids:
            fb_stmt = select(DecisionFeedback).where(
                DecisionFeedback.attempt_id.in_(attempt_ids)
            )
            fbs = list((await session.scalars(fb_stmt)).all())
            for fb in fbs:
                if fb.attempt_id:
                    feedbacks_by_attempt[fb.attempt_id] = fb

        # Кэш решений для сценариев
        decision_map: dict[uuid.UUID, DecisionRecord] = {
            d.id: d for d in dec_records
        }

        eligible_recommendations = 0
        accepted_count = 0
        status_override_count = 0
        comment_edit_count = 0
        total_confirmed_recommendation_updates = 0
        confirmed_with_usable_feedback = 0

        manual_exclusions = 0
        legacy_exclusions = 0
        uncomparable_exclusions = 0

        reason_counts_operator: dict[str, int] = {}
        reason_counts_inferred: dict[str, int] = {}
        operator_explicit_reasons: dict[str, int] = {}
        scenario_groups: dict[tuple[str, int], dict[str, int]] = {}

        for att in attempts:
            dec = decision_map.get(att.decision_id)
            if dec is None:
                continue
            env = dec.envelope_json or {}
            ctx = dec.context_json or {}
            s_key = env.get("scenario_key") or "unknown"
            s_ver = env.get("scenario_version") or 1
            rev = ctx.get("analysis_revision")

            if scenario_key and s_key != scenario_key:
                continue
            if scenario_version and s_ver != scenario_version:
                continue
            if analysis_revision and rev != analysis_revision:
                continue

            if att.source != "recommendation_apply":
                manual_exclusions += 1
                continue

            # Проверяем подтвержденное обновление статуса
            subops = att.suboperations_json or {}
            target_upd = subops.get("target_update") or {}
            is_confirmed_upd = (
                target_upd.get("outcome") == "confirmed"
                or att.state in {"succeeded", "partial_failure"}
            )
            if is_confirmed_upd:
                total_confirmed_recommendation_updates += 1

            fb = feedbacks_by_attempt.get(att.id)
            if fb is None:
                continue

            if fb.source == "legacy":
                legacy_exclusions += 1
                continue

            fa = fb.final_action_json or {}
            cdiff = fa.get("comment_diff") or {}
            is_comparable = cdiff.get("comparable", True)
            if not is_comparable:
                uncomparable_exclusions += 1
                continue

            if is_confirmed_upd:
                confirmed_with_usable_feedback += 1

            # Этот attempt входит в знаменатель N!
            eligible_recommendations += 1

            # Метрики согласия
            scen_tuple = (s_key, s_ver)
            if scen_tuple not in scenario_groups:
                scenario_groups[scen_tuple] = {"total": 0, "accepted": 0}
            scenario_groups[scen_tuple]["total"] += 1

            if fb.verdict == "accepted":
                accepted_count += 1
                scenario_groups[scen_tuple]["accepted"] += 1

            if fa.get("status_changed"):
                status_override_count += 1
            if fa.get("comment_changed"):
                comment_edit_count += 1

            # Распределение причин
            if fb.reason_code:
                if fb.reason_source == "operator":
                    reason_counts_operator[fb.reason_code] = (
                        reason_counts_operator.get(fb.reason_code, 0) + 1
                    )
                else:
                    reason_counts_inferred[fb.reason_code] = (
                        reason_counts_inferred.get(fb.reason_code, 0) + 1
                    )

            if fb.operator_reason_code:
                operator_explicit_reasons[fb.operator_reason_code] = (
                    operator_explicit_reasons.get(fb.operator_reason_code, 0) + 1
                )

        # -------------------------------------------------------------------
        # 4. Explicit Rejections & Explicit Feedback
        # -------------------------------------------------------------------
        exp_stmt = select(DecisionFeedback).where(
            DecisionFeedback.created_at >= start_date,
            DecisionFeedback.created_at < end_date,
            DecisionFeedback.source == "explicit_feedback",
        )
        explicit_fbs = list((await session.scalars(exp_stmt)).all())

        explicit_rejections_set: set[uuid.UUID] = set()
        explicit_reason_counts: dict[str, int] = {}

        for efb in explicit_fbs:
            if efb.verdict == "rejected":
                explicit_rejections_set.add(efb.decision_id)
            if efb.reason_code:
                explicit_reason_counts[efb.reason_code] = (
                    explicit_reason_counts.get(efb.reason_code, 0) + 1
                )

        # -------------------------------------------------------------------
        # 5. Вычисление долей (Rates)
        # -------------------------------------------------------------------
        N = eligible_recommendations
        acceptance_rate = (
            round(accepted_count / N, 4) if N > 0 else None
        )
        status_override_rate = (
            round(status_override_count / N, 4) if N > 0 else None
        )
        comment_edit_rate = (
            round(comment_edit_count / N, 4) if N > 0 else None
        )
        feedback_coverage = (
            round(confirmed_with_usable_feedback / total_confirmed_recommendation_updates, 4)
            if total_confirmed_recommendation_updates > 0
            else None
        )

        scenario_breakdown = []
        for (s_key, s_ver), counts in sorted(scenario_groups.items()):
            g_total = counts["total"]
            g_acc = counts["accepted"]
            scenario_breakdown.append(
                {
                    "scenario_key": s_key,
                    "scenario_version": s_ver,
                    "total": g_total,
                    "accepted": g_acc,
                    "acceptance_rate": round(g_acc / g_total, 4) if g_total > 0 else None,
                    "insufficient_data": g_total < 5,
                }
            )

        # -------------------------------------------------------------------
        # 6. Сводка сверки неизвестных попыток (Reconciliation Summary)
        # -------------------------------------------------------------------
        all_att_stmt = select(DecisionApplicationAttempt).where(
            DecisionApplicationAttempt.created_at >= start_date,
            DecisionApplicationAttempt.created_at < end_date,
        )
        all_period_attempts = list((await session.scalars(all_att_stmt)).all())
        total_unknown_attempts = sum(
            1 for a in all_period_attempts if a.overall_outcome == "unknown"
        )
        reconciled_confirmed = sum(
            1
            for a in all_period_attempts
            if a.overall_outcome == "confirmed" and a.reconciled_at is not None
        )
        reconciled_failed = sum(
            1
            for a in all_period_attempts
            if a.overall_outcome == "failed" and a.reconciled_at is not None
        )
        still_unknown = sum(
            1
            for a in all_period_attempts
            if a.overall_outcome == "unknown" and a.reconciled_at is None
        )

        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            "filters": {
                "scenario_key": scenario_key,
                "scenario_version": scenario_version,
                "analysis_revision": analysis_revision,
            },
            "totals": {
                "total_decisions": len(filtered_dec_ids) if filtered_dec_ids else len(dec_records),
                "total_decisions_triage": total_decisions_triage,
                "total_decisions_operational": total_decisions_operational,
                "total_decisions_failed": total_decisions_failed,
                "total_applications": len(apps),
                "total_applications_command": app_command_count,
                "total_applications_triage": app_triage_count,
                "total_applications_manual": app_manual_count,
            },
            "metrics": {
                "eligible_recommendation_applications": N,
                "accepted_count": accepted_count,
                "acceptance_rate": acceptance_rate,
                "status_override_count": status_override_count,
                "status_override_rate": status_override_rate,
                "comment_edit_count": comment_edit_count,
                "comment_edit_rate": comment_edit_rate,
                "feedback_coverage": feedback_coverage,
            },
            "execution_breakdown": exec_breakdown,
            "explicit_feedback": {
                "unique_rejected_decisions": len(explicit_rejections_set),
                "total_explicit_feedback": len(explicit_fbs),
                "reason_distribution": explicit_reason_counts,
            },
            "reason_distribution": {
                "operator": reason_counts_operator,
                "inferred": reason_counts_inferred,
            },
            "operator_reason_distribution": operator_explicit_reasons,
            "scenario_breakdown": scenario_breakdown,
            "reconciliation_summary": {
                "total_unknown_attempts": total_unknown_attempts,
                "reconciled_confirmed": reconciled_confirmed,
                "reconciled_failed": reconciled_failed,
                "still_unknown": still_unknown,
            },
            "exclusions": {
                "manual_apply": manual_exclusions,
                "legacy": legacy_exclusions,
                "uncomparable": uncomparable_exclusions,
            },
            "metadata": {
                "temporal_base_apply": "application_attempt_completed_at",
                "temporal_base_decisions": "decision_record_created_at",
                "temporal_base_explicit_feedback": "feedback_created_at",
                "version": 1,
            },
        }
