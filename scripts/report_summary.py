import json
from collections import Counter, defaultdict

with open("scripts/evaluation_dump.json", "r", encoding="utf-8") as f:
    by_scenario = json.load(f)

print("=== 1. ПУСТЫЕ ОТВЕТЫ И НАРУШЕНИЯ (VIOLATIONS) ===")
for scen, items in by_scenario.items():
    for item in items:
        if not item["response"] or item.get("violations"):
            print(f"Task #{item['task_id']} | Scenario: {scen} | Outcome: {item['outcome']}")
            print(f"  Title: {item['title']}")
            print(f"  Response: {repr(item['response'])}")
            print(f"  Violations: {item['violations']}")
            print(f"  Resp State: {item['resp_state']}")
            print("-" * 50)

print("\n=== 2. ВСЕ УНИКАЛЬНЫЕ ТЕКСТЫ ОТВЕТОВ ПО СЦЕНАРИЯМ ===")
for scen, items in sorted(by_scenario.items()):
    print(f"\n--- {scen.upper()} ({len(items)} заявок) ---")
    resp_counts = Counter(item["response"] for item in items)
    for resp, count in resp_counts.most_common():
        print(f"  [x{count}] {repr(resp[:180])}")

print("\n=== 3. АНАЛИЗ УТОЧНЕНИЙ (CLARIFICATIONS) ===")
for scen, items in by_scenario.items():
    clarif_items = [it for it in items if it.get("clarifications")]
    if clarif_items:
        print(f"\nСценарий {scen}: {len(clarif_items)} уточняющих запросов:")
        for it in clarif_items:
            print(f"  Task #{it['task_id']}: {it['title']}")
            print(f"    Вопрос пользователю: {it['clarifications']}")
            print(f"    Ответ в тикете: {repr(it['response'][:150])}")

print("\n=== 4. АНАЛИЗ ДЕЙСТВИЙ (ACTIONS) ===")
for scen, items in by_scenario.items():
    action_items = [it for it in items if it["outcome"] == "action"]
    if action_items:
        print(f"\nСценарий {scen}: {len(action_items)} действий:")
        for it in action_items:
            print(f"  Task #{it['task_id']}: {it['title']}")
            print(f"    Ответ в тикете: {repr(it['response'][:150])}")
            print(f"    Execution plan: {it.get('execution_plan', {}).get('title')}")
            for step in it.get("execution_plan", {}).get("steps", []):
                print(f"      Step: {step.get('id')} | {step.get('title')} | status: {step.get('status')}")
