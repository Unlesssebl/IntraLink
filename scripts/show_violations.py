import json

with open("scripts/evaluation_dump.json", "r", encoding="utf-8") as f:
    by_scenario = json.load(f)

print("=== 1. ПУСТЫЕ ОТВЕТЫ И НАРУШЕНИЯ (VIOLATIONS) ===")
count = 0
for scen, items in by_scenario.items():
    for item in items:
        if not item["response"] or item.get("violations"):
            count += 1
            print(f"[{count}] Task #{item['task_id']} | Scenario: {scen} | Outcome: {item['outcome']}")
            print(f"  Title: {item['title']}")
            print(f"  Response: {repr(item['response'])}")
            print(f"  Violations: {item.get('violations')}")
            print(f"  Resp State: {item.get('resp_state')}")
            print("-" * 50)
