import json

with open("scripts/evaluation_dump.json", "r", encoding="utf-8") as f:
    by_scenario = json.load(f)

print("=== ДЕТАЛЬНЫЙ РАЗБОР PC_PERFORMANCE ===")
for it in by_scenario.get("pc_performance", []):
    print(f"\nTask #{it['task_id']} | Outcome: {it['outcome']} | Conf: {it['confidence']}")
    print(f"  Title: {it['title']}")
    print(f"  Description: {it['description']}")
    print(f"  Response: {it['response']}")
    print(f"  Violations: {it['violations']}")
