import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    # Connect to PostgreSQL via exposed port 5432
    db_url = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/intraservice"
    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        q = text("""
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY version DESC) as rn
            FROM decision_records
        )
        SELECT 
            task_id,
            analysis_kind,
            status,
            outcome,
            envelope_json,
            context_json
        FROM ranked
        WHERE rn = 1
        ORDER BY task_id;
        """)
        result = await conn.execute(q)
        rows = result.fetchall()
        print(f"Total latest decision records: {len(rows)}")
        
        scenarios = {}
        outcomes = {}
        response_sources = {}
        empty_responses = 0
        violations_count = 0
        
        by_scenario = {}

        for row in rows:
            env = row.envelope_json if isinstance(row.envelope_json, dict) else json.loads(row.envelope_json)
            ctx = row.context_json if isinstance(row.context_json, dict) else json.loads(row.context_json)
            task = ctx.get("task", {})
            
            scen = env.get("scenario_key") or "NO_SCENARIO"
            outc = row.outcome
            scenarios[scen] = scenarios.get(scen, 0) + 1
            outcomes[outc] = outcomes.get(outc, 0) + 1
            
            resp = env.get("response") or {}
            if isinstance(resp, str):
                try:
                    resp = json.loads(resp)
                except Exception:
                    resp = {"text": resp}
            
            resp_text = resp.get("text", "")
            if not resp_text:
                empty_responses += 1
                
            prov = resp.get("provenance") or {}
            src = prov.get("source", "unknown") if isinstance(prov, dict) else "unknown"
            response_sources[src] = response_sources.get(src, 0) + 1
            
            if resp.get("violations"):
                violations_count += 1
                
            if scen not in by_scenario:
                by_scenario[scen] = []
            by_scenario[scen].append({
                "task_id": row.task_id,
                "title": task.get("Name"),
                "description": task.get("Description"),
                "service": task.get("ServiceName"),
                "outcome": outc,
                "confidence": env.get("confidence"),
                "response": resp_text,
                "resp_state": resp.get("state"),
                "violations": resp.get("violations"),
                "gates": env.get("gates"),
                "internal_summary": env.get("internal_summary"),
                "clarifications": env.get("clarifications"),
                "execution_plan": env.get("execution_plan"),
            })

        print("\n--- SCENARIO COUNTS ---")
        for k, v in sorted(scenarios.items(), key=lambda x: x[1], reverse=True):
            print(f"  {k}: {v}")

        print("\n--- OUTCOME COUNTS ---")
        for k, v in sorted(outcomes.items(), key=lambda x: x[1], reverse=True):
            print(f"  {k}: {v}")

        print("\n--- RESPONSE SOURCES ---")
        for k, v in sorted(response_sources.items(), key=lambda x: x[1], reverse=True):
            print(f"  {k}: {v}")

        print(f"\nEmpty responses: {empty_responses}")
        print(f"Responses with violations: {violations_count}")

        # Save dump to json for inspection
        with open("scripts/evaluation_dump.json", "w", encoding="utf-8") as f:
            json.dump(by_scenario, f, ensure_ascii=False, indent=2)
        print("\nSaved detailed dump to scripts/evaluation_dump.json")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
