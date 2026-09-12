import json

with open("scripts/evaluation_dump.json", "r", encoding="utf-8") as f:
    by_scenario = json.load(f)

print("=== ДЕТАЛЬНЫЙ РАЗБОР INSTALL_PRINTER ===")
for it in by_scenario.get("install_printer", []):
    print(f"\nTask #{it['task_id']} | Outcome: {it['outcome']} | Conf: {it['confidence']}")
    print(f"  Title: {it['title']}")
    print(f"  Description: {it['description']}")
    print(f"  Response: {it['response']}")
    print(f"  Violations: {it['violations']}")
    print(f"  Clarifications: {it['clarifications']}")
    if it.get("execution_plan"):
        print(f"  Plan: {it['execution_plan'].get('title')}")
        steps = it['execution_plan'].get('steps', [])
        for s in steps:
            print(f"    - {s.get('id')}: {s.get('title')} ({s.get('status')}) metadata={s.get('metadata')}")
