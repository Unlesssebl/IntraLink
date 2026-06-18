import json

with open("/app/scripts/evaluation_results.json") as f:
    data = json.load(f)

results = data["results"]

print("=== FALSE POSITIVES (wrongly redirected) ===")
for r in results:
    if "False Positive" in r.get("evaluation", ""):
        print(f"Task #{r['task_id']} | Section: {r['service']}")
        print(f"  -> Redirect to: {r['predicted_service']}")
        print(f"  -> Reason: {r['reason']}")
        print()

print()
print("=== FALSE NEGATIVES (missed redirects) ===")
for r in results:
    if "False Negative" in r.get("evaluation", ""):
        print(f"Task #{r['task_id']} | Section: {r['service']}")
        print(f"  -> Reason: {r['reason']}")
        print()
