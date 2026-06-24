"""
debug_tools/batch_runner.py
───────────────────────────
Батч-режим для LLM-as-judge оценки качества воркеров на выборке заявок.

Использование:
    python -m debug_tools.cli printer --batch 133609,141205,145001
    python -m debug_tools.cli printer --batch-file ids.txt
    python -m debug_tools.cli ai --batch 133609,141205
"""
import asyncio
import json
import sys
from typing import Any
from debug_tools.core import fetch_task


async def run_batch(worker: str, task_ids: list[int]) -> dict[str, Any]:
    """
    Прогоняет список заявок через указанный воркер и возвращает агрегированный отчёт.
    """
    report: dict[str, Any] = {
        "worker": worker,
        "total": len(task_ids),
        "success": 0,
        "failed": 0,
        "failed_details": [],
    }

    if worker == "ai":
        report.update({"classify_none": 0, "classify_redirect": 0, "responder_can_resolve": 0})
    elif worker == "printer":
        report.update({
            "router_success": 0,
            "router_failed": 0,
            "track_snmp": 0,
            "track_fast": 0,
            "track_smart": 0,
            "track_unknown": 0,
        })

    for i, task_id in enumerate(task_ids, 1):
        print(f"[{i}/{len(task_ids)}] Обработка заявки #{task_id}...")
        try:
            task_data = await fetch_task(task_id)
            if not task_data:
                raise RuntimeError("Пустой ответ от API")
        except Exception as e:
            print(f"  ❌ Не удалось загрузить: {e}")
            report["failed"] += 1
            report["failed_details"].append({"task_id": task_id, "reason": f"Ошибка загрузки: {e}"})
            continue

        if worker == "printer":
            result = await _run_printer_batch_item(task_id, task_data, report)
        elif worker == "ai":
            result = await _run_ai_batch_item(task_id, task_data, report)
        else:
            print(f"  ❓ Неизвестный воркер: {worker}")
            continue

    report["success_rate"] = (
        round(report["success"] / report["total"] * 100, 1) if report["total"] > 0 else 0.0
    )
    return report


async def _run_printer_batch_item(task_id: int, task_data: dict, report: dict[str, Any]) -> None:
    import os
    import re
    import json as json_mod
    from debug_tools import PROJECT_ROOT, add_to_path

    kb_path = os.path.join(PROJECT_ROOT, "printer-worker", "knowledge_base", "printers_knowledge_base.json")
    os.environ["PRINTERS_KB_PATH"] = kb_path
    add_to_path("printer-worker")

    from orchestrator.schemas import PrintJob, JobState, KnowledgeBase
    from orchestrator.router import JobRouter

    target_pc = task_data.get("Field1112") or None
    model_key = task_data.get("Field1103") or None

    if not target_pc and task_data.get("CreatorComments"):
        target_pc = task_data.get("CreatorComments")

    if (not target_pc or not model_key) and task_data.get("Data"):
        data_xml = task_data["Data"]
        if not target_pc:
            m = re.search(r'<field id="1112">([^<]+)</field>', data_xml)
            if m:
                target_pc = m.group(1).strip() or None
        if not model_key:
            m = re.search(r'<field id="1103">([^<]+)</field>', data_xml)
            if m:
                model_key = m.group(1).strip() or None

    raw_text = f"{task_data.get('Name', '')} {task_data.get('Description', '')}"
    if task_data.get("CreatorComments"):
        raw_text += f" {task_data['CreatorComments']}"

    cyrillic = 'ОСАЕРХМТКВ'
    latin    = 'OCAEPXMTKB'
    tr_map = str.maketrans(cyrillic + cyrillic.lower(), latin + latin.lower())
    if target_pc:
        target_pc = target_pc.translate(tr_map).replace(" ", "").upper()
    if model_key:
        model_key = model_key.translate(tr_map)
        model_key = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '', model_key).strip() or None

    if target_pc:
        raw_text += f" {target_pc}"
    if model_key:
        raw_text += f" {model_key}"

    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            data = json_mod.load(f)
        kb = KnowledgeBase.model_validate(data)
    except Exception as e:
        report["failed"] += 1
        report["failed_details"].append({"task_id": task_id, "reason": f"Ошибка КБ: {e}"})
        return

    job = PrintJob(
        task_id=task_id, tg_user_id=0, raw_text=raw_text,
        state=JobState.PENDING, target_pc=target_pc, model_key=model_key,
    )
    router = JobRouter(kb)

    try:
        job = await router.route(job)
    except Exception as e:
        report["router_failed"] += 1
        report["failed"] += 1
        report["failed_details"].append({"task_id": task_id, "reason": f"router.route() exception: {e}"})
        return

    if job.state == JobState.FAILED or job.driver_info is None:
        report["router_failed"] += 1
        report["failed"] += 1
        report["failed_details"].append({
            "task_id": task_id,
            "reason": job.error_message or "driver_info is None",
            "raw_model": task_data.get("Field1103"),
            "raw_pc": task_data.get("Field1112"),
        })
        print(f"  ❌ FAILED — {job.error_message or 'driver_info=None'}")
    else:
        report["router_success"] += 1
        report["success"] += 1
        bundle_info = f" (бандл: {job.driver_info.driver_bundle})" if job.driver_info.driver_bundle else ""
        print(f"  ✅ OK — {job.driver_info.model_key}{bundle_info}, PC={job.target_pc}")


async def _run_ai_batch_item(task_id: int, task_data: dict, report: dict[str, Any]) -> None:
    from debug_tools import add_to_path
    add_to_path("ai-worker")
    from services.classifier import AIClassifier

    try:
        classifier = AIClassifier()
        result = await classifier.classify_task(task_data)
        report["success"] += 1
        if result.action == "redirect":
            report["classify_redirect"] += 1
            print(f"  🔀 redirect → {result.correct_service_name} (conf={result.confidence:.2f})")
        else:
            report["classify_none"] += 1
            print(f"  ✅ none (conf={result.confidence:.2f})")
    except Exception as e:
        report["failed"] += 1
        report["failed_details"].append({"task_id": task_id, "reason": str(e)})
        print(f"  ❌ Ошибка: {e}")


def print_batch_report(report: dict[str, Any]) -> None:
    """Красиво печатает итоговый отчёт батч-прогона."""
    print("\n" + "=" * 55)
    print(f"📊  БАТЧ-ОТЧЁТ ({report['worker'].upper()}-WORKER)")
    print("=" * 55)
    print(f"  Всего заявок    : {report['total']}")
    print(f"  Успешно         : {report['success']} ({report.get('success_rate', 0)}%)")
    print(f"  С ошибкой       : {report['failed']}")

    if report["worker"] == "printer":
        print(f"\n  Маршрутизация:")
        print(f"    Успех    : {report.get('router_success', 0)}")
        print(f"    Провал   : {report.get('router_failed', 0)}")

    if report["worker"] == "ai":
        print(f"\n  Классификация:")
        print(f"    none     : {report.get('classify_none', 0)}")
        print(f"    redirect : {report.get('classify_redirect', 0)}")

    if report.get("failed_details"):
        print(f"\n  ❌ Детали провалов:")
        for item in report["failed_details"]:
            print(f"    Task #{item['task_id']}: {item['reason']}")
            if item.get("raw_model"):
                print(f"      raw_model={item['raw_model']}, raw_pc={item.get('raw_pc')}")

    print("=" * 55)
    print(json.dumps(report, ensure_ascii=False, indent=2))
