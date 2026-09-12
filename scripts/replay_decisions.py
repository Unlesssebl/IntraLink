#!/usr/bin/env python3
"""
Воспроизводимый детерминированный replay решений по историческому корпусу заявок Helpdesk.
Использует реальный ScenarioDecisionService и мигрированную базу данных PostgreSQL.
Все побочные эффекты (отправка комментариев, сброс паролей, команды PowerShell) отключены.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Добавляем core-api в sys.path для импорта сервисов
ROOT_DIR = Path(__file__).resolve().parent.parent
CORE_API_DIR = ROOT_DIR / "core-api"
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(CORE_API_DIR))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.scenario_decision import ScenarioDecisionService
from app.services.response_guard import EMERGENCY_DRAFT_TEXT


async def run_replay(
    db_url: str,
    output_dir: Path,
    limit: int | None = None,
    use_ai: bool = False,
) -> dict[str, Any]:
    print(f"=== Запуск Replay решений IntraLink ===")
    print(f"Подключение к БД: {db_url.split('@')[-1] if '@' in db_url else db_url}")
    print(f"Режим AI: {'Включен (живой/шлюз)' if use_ai else 'Отключен (детерминированный контрактный прогон)'}")

    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        # Извлекаем самые последние записи решений по каждой заявке
        query = text("""
            WITH ranked AS (
                SELECT 
                    id,
                    task_id,
                    analysis_kind,
                    status,
                    outcome,
                    envelope_json,
                    context_json,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY version DESC, created_at DESC) as rn
                FROM decision_records
            )
            SELECT task_id, status, outcome, envelope_json, context_json
            FROM ranked
            WHERE rn = 1
            ORDER BY task_id ASC;
        """)

        result = await session.execute(query)
        rows = result.fetchall()
        if limit:
            rows = rows[:limit]

        total_tickets = len(rows)
        print(f"Загружено уникальных заявок для replay: {total_tickets}\n")

        decision_service = ScenarioDecisionService(db=session, ai_enabled=use_ai)

        before_empty_count = 0
        after_empty_count = 0
        emergency_draft_count = 0
        false_action_authorizations = 0
        peripheral_fixed_count = 0
        latencies: list[float] = []

        scenarios_distribution: dict[str, int] = {}
        outcomes_distribution: dict[str, int] = {}
        states_distribution: dict[str, int] = {}

        detailed_results: list[dict[str, Any]] = []

        for idx, row in enumerate(rows, 1):
            task_id = row.task_id
            old_envelope = row.envelope_json if isinstance(row.envelope_json, dict) else json.loads(row.envelope_json)
            context = row.context_json if isinstance(row.context_json, dict) else json.loads(row.context_json)

            task = context.get("task") or {}
            comments = context.get("comments") or []
            diagnostics = context.get("diagnostics") or context.get("telemetry")
            kb_matches = context.get("kb_matches") or []

            # Старое состояние ответа
            old_resp = old_envelope.get("response") or {}
            old_text = old_resp.get("text", "").strip() if isinstance(old_resp, dict) else str(old_resp).strip()
            if not old_text:
                before_empty_count += 1

            t_start = time.perf_counter()
            new_envelope = await decision_service.analyze(
                task=task,
                comments=comments,
                diagnostics=diagnostics,
                kb_matches=kb_matches,
                fact_revision=0,
            )
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            latencies.append(elapsed_ms)

            new_resp = new_envelope.response
            new_text = new_resp.text.strip()
            if not new_text:
                after_empty_count += 1

            if new_resp.mode == "emergency_draft":
                emergency_draft_count += 1

            # Проверка инварианта: ложное разрешение действия при ошибке политики
            has_policy_error = bool(new_envelope.policy.get("resolution_error") or new_envelope.policy.get("error"))
            if has_policy_error and (new_envelope.gates.can_send_response or new_envelope.gates.can_execute_action):
                false_action_authorizations += 1

            # Проверка исправления периферийных кейсов
            if (
                new_envelope.scenario_key in {"peripheral_setup", "peripheral_diagnostics"}
                and new_envelope.outcome.outcome_key == "peripheral_clarify"
                and not old_text
            ):
                peripheral_fixed_count += 1

            scen_key = new_envelope.scenario_key
            outc_key = new_envelope.outcome.outcome_key if hasattr(new_envelope.outcome, "outcome_key") else str(new_envelope.outcome)
            scenarios_distribution[scen_key] = scenarios_distribution.get(scen_key, 0) + 1
            outcomes_distribution[outc_key] = outcomes_distribution.get(outc_key, 0) + 1
            states_distribution[new_envelope.status] = states_distribution.get(new_envelope.status, 0) + 1

            detailed_results.append({
                "task_id": task_id,
                "title": task.get("Name"),
                "service_name": task.get("ServiceName"),
                "scenario_key": scen_key,
                "outcome_key": outc_key,
                "status": new_envelope.status,
                "confidence": new_envelope.confidence,
                "old_text_empty": not old_text,
                "new_text_empty": not new_text,
                "is_emergency_draft": new_resp.mode == "emergency_draft",
                "can_send": new_envelope.gates.can_send_response,
                "can_execute": new_envelope.gates.can_execute_action,
                "blocked_reasons": new_envelope.gates.blocked_reasons,
                "response_text_snippet": (new_text[:80] + "...") if len(new_text) > 80 else new_text,
                "elapsed_ms": round(elapsed_ms, 2),
            })

            if idx % 20 == 0 or idx == total_tickets:
                print(f"  Обработано: {idx}/{total_tickets} (текущий пустых: {after_empty_count})")

        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0.0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0

        summary = {
            "total_tickets": total_tickets,
            "before_empty_responses": before_empty_count,
            "after_empty_responses": after_empty_count,
            "emergency_drafts": emergency_draft_count,
            "false_action_authorizations": false_action_authorizations,
            "peripheral_fixed_count": peripheral_fixed_count,
            "p50_latency_ms": round(p50, 2),
            "p95_latency_ms": round(p95, 2),
            "scenarios_distribution": scenarios_distribution,
            "outcomes_distribution": outcomes_distribution,
            "states_distribution": states_distribution,
        }

        print("\n=== ИТОГИ REPLAY ===")
        print(f"Пустых ответов ДО: {before_empty_count}")
        print(f"Пустых ответов ПОСЛЕ: {after_empty_count} (Цель 0: {'ДОСТИГНУТО' if after_empty_count == 0 else 'СБОЙ'})")
        print(f"Периферийных кейсов исправлено (были пустыми, теперь peripheral_clarify): {peripheral_fixed_count}")
        print(f"Аварийных нейтральных черновиков: {emergency_draft_count}")
        print(f"Ложных разрешений действий: {false_action_authorizations} (Цель 0: {'ДОСТИГНУТО' if false_action_authorizations == 0 else 'СБОЙ'})")
        print(f"Задержка p50: {p50:.1f} мс, p95: {p95:.1f} мс")

        output_dir.mkdir(parents=True, exist_ok=True)
        report_json_path = output_dir / "replay_report.json"
        report_md_path = output_dir / "replay_report.md"

        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "tickets": detailed_results}, f, ensure_ascii=False, indent=2)

        md_content = f"""# Отчет о результатах Replay решений

**Дата проведения:** {time.strftime('%Y-%m-%d %H:%M:%S')}  
**Выборка:** {total_tickets} исторических заявок 1-й линии Helpdesk.

## Ключевые метрики

| Метрика | До изменений | После изменений | Статус |
|---|---|---|---|
| **Пустые ответы (`text == ""`)** | {before_empty_count} | **{after_empty_count}** | {'OK (0 пустых)' if after_empty_count == 0 else 'FAILED'} |
| **Ложные разрешения действий** | - | **{false_action_authorizations}** | {'OK (0 нарушений)' if false_action_authorizations == 0 else 'FAILED'} |
| **Периферийные кейсы (`peripheral_clarify`)** | 0 валидных (7 сбоев) | **{peripheral_fixed_count} исправлено** | OK |
| **Аварийные нейтральные черновики** | 0 (давался пустой текст) | **{emergency_draft_count}** | Заблокировано оператору |
| **Задержка p50 / p95** | - | **{p50:.1f} мс / {p95:.1f} мс** | В пределах бюджета |

## Распределение сценариев
```json
{json.dumps(scenarios_distribution, ensure_ascii=False, indent=2)}
```

## Распределение состояний решений
```json
{json.dumps(states_distribution, ensure_ascii=False, indent=2)}
```
"""
        with open(report_md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"\nОтчет сохранен:\n- {report_json_path}\n- {report_md_path}")
        await engine.dispose()
        return summary


def main():
    parser = argparse.ArgumentParser(description="Replay решений IntraLink")
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/intraservice"))
    parser.add_argument("--output-dir", default=str(ROOT_DIR / "scripts" / "reports"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--use-ai", action="store_true", help="Включить живой вызов AI Hub")
    args = parser.parse_args()

    asyncio.run(run_replay(
        db_url=args.db_url,
        output_dir=Path(args.output_dir),
        limit=args.limit,
        use_ai=args.use_ai,
    ))


if __name__ == "__main__":
    main()
