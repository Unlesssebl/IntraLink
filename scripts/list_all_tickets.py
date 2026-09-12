import json

with open("scripts/evaluation_dump.json", "r", encoding="utf-8") as f:
    by_scenario = json.load(f)

print(f"{'ID':<8} | {'Scenario':<22} | {'Outcome':<14} | {'Title'}")
print("-" * 100)

all_items = []
for scen, items in by_scenario.items():
    for it in items:
        all_items.append((scen, it))

# Sort by scenario then task_id
all_items.sort(key=lambda x: (x[0], x[1]["task_id"]))

for scen, it in all_items:
    t_id = it["task_id"]
    outcome = it["outcome"]
    title = (it["title"] or "")[:60]
    print(f"{t_id:<8} | {scen:<22} | {outcome:<14} | {title}")
