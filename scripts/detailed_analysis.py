import json
from collections import Counter, defaultdict

with open("scripts/evaluation_dump.json", "r", encoding="utf-8") as f:
    by_scenario = json.load(f)

print("=" * 80)
print("ДЕТАЛЬНЫЙ АНАЛИЗ ВСЕХ 101 ЗАЯВОК: СЦЕНАРИИ И ТЕКСТЫ ОТВЕТОВ")
print("=" * 80)

total_tickets = 0
misrouted_candidates = []
truncated_or_broken_texts = []
empty_responses = []
ungrounded_violations = []

for scen, items in by_scenario.items():
    total_tickets += len(items)
    for it in items:
        tid = it["task_id"]
        title = it["title"] or ""
        desc = it["description"] or ""
        resp = it["response"] or ""
        outcome = it["outcome"]
        violations = it.get("violations") or []
        
        # Check empty
        if not resp.strip():
            empty_responses.append((tid, scen, outcome, title))
            
        # Check ungrounded
        if any("ungrounded" in v for v in violations):
            ungrounded_violations.append((tid, scen, outcome, title, violations, resp))
            
        # Check grammar/broken template
        if resp.endswith("к ") or resp.endswith("в разделе") or "по не " in resp or "уточните, пожалуйста, пожалуйста" in resp:
            truncated_or_broken_texts.append((tid, scen, resp))

        # Check misrouting heuristic
        lower_text = (title + " " + desc).lower()
        if scen == "printer_print_failure" and ("word" in lower_text or "лиценз" in lower_text or "office" in lower_text):
            misrouted_candidates.append((tid, scen, "Лицензия Office/Word", title, "Должно быть ПО / Офис / RAG"))
        elif scen == "peripheral_setup" and ("астра раскро" in lower_text or "корреспонденц" in lower_text):
            misrouted_candidates.append((tid, scen, "Настройка ПО", title, "Программное обеспечение, а не периферия"))
        elif scen == "redirect" and ("тормоз" in lower_text or "грузит страниц" in lower_text):
            misrouted_candidates.append((tid, scen, "Производительность / Сеть", title, "Перенаправление вместо pc_performance"))
        elif scen == "rag_consultation":
            if any(k in lower_text for k in ["медленно", "тормозит", "долго грузит", "зависа"]):
                misrouted_candidates.append((tid, scen, "Производительность ПК", title, "Кандидат в pc_performance"))
            elif any(k in lower_text for k in ["не печатает", "принтер", "мфу", "сканир"]):
                misrouted_candidates.append((tid, scen, "Печать / Сканирование", title, "Кандидат в printer_*"))
            elif any(k in lower_text for k in ["установка windows", "обновить windows", "переустановк"]):
                misrouted_candidates.append((tid, scen, "Установка ОС", title, "Кандидат в os_reinstallation"))
            elif any(k in lower_text for k in ["клавиатур", "звук", "наушник"]):
                misrouted_candidates.append((tid, scen, "Периферия", title, "Кандидат в peripheral_*"))

print(f"Всего заявок: {total_tickets}")
print(f"Пустых ответов: {len(empty_responses)}")
print(f"Ответов с галлюцинациями/неподтвержденными фактами: {len(ungrounded_violations)}")
print(f"Текстов с явными шаблоными дефектами (обрывы, 'по не...', дубли): {len(truncated_or_broken_texts)}")
print(f"Кандидатов на ложную маршрутизацию: {len(misrouted_candidates)}")

print("\n--- 1. ПУСТЫЕ ОТВЕТЫ ---")
for tid, scen, outc, tit in empty_responses:
    print(f"  #{tid} | {scen} | {outc} | {tit}")

print("\n--- 2. ДЕФЕКТЫ ШАБЛОНОВ И ОБРЫВЫ ТЕКСТА ---")
for tid, scen, resp in truncated_or_broken_texts[:10]:
    print(f"  #{tid} ({scen}): {repr(resp)}")

print("\n--- 3. НЕСООТВЕТСТВИЯ МАРШРУТИЗАЦИИ (ЛОЖНЫЕ СЦЕНАРИИ) ---")
for tid, scen, kind, tit, comment in misrouted_candidates:
    print(f"  #{tid} [{scen} -> {kind}]: \"{tit}\" ({comment})")

