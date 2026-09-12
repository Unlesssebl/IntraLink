import json
from collections import Counter

with open("scripts/evaluation_dump.json", "r", encoding="utf-8") as f:
    by_scenario = json.load(f)

print("="*80)
print("1. АНАЛИЗ ПУСТЫХ И ОШИБОЧНЫХ ОТВЕТОВ (VIOLATIONS & EMPTY)")
print("="*80)

for scen, items in by_scenario.items():
    for item in items:
        if not item["response"] or item["violations"]:
            print(f"Task #{item['task_id']} | Scenario: {scen} | Outcome: {item['outcome']}")
            print(f"  Title: {item['title']}")
            print(f"  Service: {item['service']}")
            print(f"  Response: {repr(item['response'])}")
            print(f"  Violations: {item['violations']}")
            print(f"  Resp State: {item['resp_state']}")
            print(f"  Internal Summary: {item['internal_summary']}")
            print("-" * 60)

print("\n" + "="*80)
print("2. АНАЛИЗ ПО КАЖДОМУ СЦЕНАРИЮ: ТОЧНОСТЬ МАРШРУТИЗАЦИИ И КАЧЕСТВО ТЕКСТА")
print("="*80)

for scen, items in sorted(by_scenario.items()):
    print(f"\n### СЦЕНАРИЙ: {scen} (Всего заявок: {len(items)})")
    outcomes = Counter(x["outcome"] for x in items)
    print(f"  Распределение исходов: {dict(outcomes)}")
    
    # Show each ticket in this scenario
    for idx, item in enumerate(items):
        print(f"\n  [{idx+1}] Заявка #{item['task_id']} | Исход: {item['outcome']} | Conf: {item['confidence']}")
        print(f"      Тема: {item['title']}")
        desc_preview = (item['description'] or '')[:120].replace('\n', ' ')
        print(f"      Описание: {desc_preview}...")
        resp_preview = (item['response'] or '')[:150].replace('\n', ' ')
        print(f"      Ответ: {resp_preview}...")
        if item.get("clarifications"):
            print(f"      Уточнения (вопросы): {item['clarifications']}")
        if item.get("execution_plan"):
            print(f"      План действий: {item['execution_plan']}")
        if item.get("violations"):
            print(f"      Нарушения: {item['violations']}")
