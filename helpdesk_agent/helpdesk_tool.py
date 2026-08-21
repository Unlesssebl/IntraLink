import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

from intraservice_api import IntraServiceClient
from diagnostics import extract_potential_hosts, run_host_diagnostics, format_diagnostics_summary
from kb import (
    search_knowledge_base,
    index_task_record,
    sync_history_kb,
    test_db_connection,
    ensure_db_schema,
)
from template_engine import auto_detect_template, render_template, load_templates
from deduplication import DuplicateDetector
from session_state import (
    add_skipped_tasks,
    add_applied_tasks,
    get_skipped_task_ids,
    reset_session_state,
)
from executors.ad import ActiveDirectoryExecutor
from llm.ollama_client import OllamaClient

DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")


async def cmd_services(args):
    """Список корневых разделов каталога услуг с человеческими номерами (для фильтрации /triage)."""
    client = IntraServiceClient()
    try:
        catalog = await client.get_service_catalog()
        roots = [s for s in catalog if s.get("ParentId") is None]
        roots.sort(key=lambda s: s.get("Name", ""))

        if args.json:
            print(json.dumps(roots, ensure_ascii=False, indent=2))
            return

        print("=== 📋 Корневые разделы каталога услуг (Номера для /triage <№>) ===\n")
        print("| № | ID | Раздел каталога | Назначение |")
        print("|:---:|:---:|---|---|")
        for r in roots:
            name = r.get("Name", "")
            r_id = r.get("Id")
            desc = r.get("Description") or "—"
            num_match = re.match(r"^(\d+)\.", name)
            num_str = num_match.group(1) if num_match else "—"
            print(f"| **{num_str}** | `{r_id}` | **{name}** | {desc} |")
        print("\n💡 Пример использования: `uv run python helpdesk_tool.py batch --service 2` или `/triage 2`")
    finally:
        await client.close()


async def cmd_queue(args):
    client = IntraServiceClient()
    try:
        service_ids = None
        service_title = ""
        if args.service:
            s_name, s_ids = await client.get_service_subtree(args.service)
            if not s_ids:
                print(f"❌ Сервис/раздел '{args.service}' не найден в каталоге IntraService.", file=sys.stderr)
                sys.exit(1)
            service_ids = s_ids
            service_title = f" | Раздел: {s_name}"

        fetch_size = args.limit if not service_ids else max(args.limit * 5, 50)
        tasks = await client.get_tasks_by_filter(args.filter, page=1, page_size=fetch_size, service_ids=service_ids)
        if service_ids:
            tasks = [t for t in tasks if t.get("ServiceId") in service_ids]
        tasks = tasks[:args.limit]

        if args.json:
            print(json.dumps(tasks, ensure_ascii=False, indent=2))
            return

        print(f"=== Очередь заявок (Фильтр #{args.filter}{service_title}, Найдено: {len(tasks)}) ===")
        for t in tasks:
            t_id = t.get("Id")
            name = t.get("Name")
            creator = t.get("Creator")
            service = t.get("ServiceName") or f"ID {t.get('ServiceId')}"
            status = t.get("StatusName") or f"ID {t.get('StatusId')}"
            created = t.get("Created", "")
            print(f"• #{t_id} | {status} | {service} | {creator} | {name} ({created})")
    finally:
        await client.close()


async def cmd_task(args):
    client = IntraServiceClient()
    try:
        task = await client.get_task_details(args.task_id)
        if not task:
            print(f"Заявка #{args.task_id} не найдена.", file=sys.stderr)
            sys.exit(1)

        # 1. Сетевая диагностика найденных хостов
        meta_info = task.get("_field_meta") or {}
        hosts = extract_potential_hosts(
            f"{task.get('Name', '')} {task.get('Description', '')}",
            meta_info.get("raw", {}),
            company=meta_info.get("company") or task.get("CreatorCompany", ""),
            dept=meta_info.get("dept") or task.get("CreatorDepartment", ""),
        )
        diag = None
        creator_ip = task.get("CreatorIP", "")
        if hosts:
            diag = await run_host_diagnostics(hosts[0], fallback_candidates=hosts, creator_ip=creator_ip)
        elif creator_ip:
            diag = await run_host_diagnostics(creator_ip, creator_ip=creator_ip)

        # 2. RAG поиск
        task_text = f"{task.get('Name', '')}. {task.get('Description', '')}".strip()
        kb_matches = []
        try:
            kb_matches = await search_knowledge_base(task_text, limit=2, distance_threshold=0.70)
        except Exception:
            pass

        # 3. Адаптивный шаблон
        action_plan = auto_detect_template(task, diag, kb_matches)

        task["_diagnostics"] = diag
        task["_kb_matches"] = kb_matches
        task["_suggested_action"] = action_plan

        if args.json:
            print(json.dumps(task, ensure_ascii=False, indent=2))
            return

        print(f"=== Заявка #{task.get('Id')} ===")
        print(f"Тема:       {task.get('Name')}")
        print(f"Заявитель:  {task.get('Creator')}")
        print(f"Раздел:     {task.get('ServiceName')}")
        print(f"Статус:     {task.get('StatusName')}")
        print(f"Создана:    {task.get('Created')}")
        
        friendly_fields = task.get("_parsed_fields", {})
        if friendly_fields:
            print("Кастомные поля:")
            for k, v in friendly_fields.items():
                print(f"  • {k}: {v}")

        if task.get("_has_attachments"):
            print(f"\n📸 Вложения ({len(task.get('_attachments_list', []))}):")
            for att in task.get("_attachments_list", []):
                print(f"  • {att.get('FileName', 'файл')} ({att.get('Size', 0)} байт)")

        print(f"\nОписание:\n{task.get('Description') or '—'}")

        if diag:
            print(f"\n🌐 Сетевой статус: {format_diagnostics_summary(diag)}")

        comments = task.get("Comments") or []
        if comments:
            print(f"\n--- Комментарии ({len(comments)}) ---")
            for c in comments:
                author = c.get("UserName") or c.get("Creator") or "Пользователь"
                date = c.get("Created", "")
                text = c.get("Text", "").strip()
                print(f"[{date}] {author}:\n{text}\n")

        if kb_matches:
            print(f"--- 🧠 Опыт базы знаний ({len(kb_matches)}) ---")
            for kb in kb_matches:
                print(f"• [Кейс #{kb['task_id']} | Сходство {kb['similarity_pct']}% | {kb['service_name']}]:")
                print(f"  {kb['solution']}\n")

        print("--- 🎯 Предлагаемое решение ---")
        print(f"Действие:     {action_plan['name']}")
        print(f"Новый статус: {action_plan['status_name']}")
        print(f"Трудозатраты: {action_plan['expenses']} мин.")
        print(f"Проект ответа:\n{action_plan['comment']}")
    finally:
        await client.close()


async def cmd_diagnose(args):
    target = args.target.strip()
    diag = await run_host_diagnostics(target)
    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=2))
    else:
        print(format_diagnostics_summary(diag))


async def process_single_ticket_for_batch(
    client: IntraServiceClient,
    basic_task: dict[str, Any],
    idx: int,
    redirect_mode: bool = False,
    sem: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Параллельная обработка одного тикета для формирования сводной матрицы."""
    async def _do_process():
        t_id = basic_task.get("Id")
        full_task = await client.get_task_details(t_id) or basic_task

        meta = full_task.get("_field_meta") or {}
        room = meta.get("room") or "—"
        phone = meta.get("phone") or "—"
        creator = full_task.get("Creator", "Заявитель")

        # Сетевая диагностика
        hosts = extract_potential_hosts(
            f"{full_task.get('Name', '')} {full_task.get('Description', '')}",
            meta.get("raw", {}),
            company=meta.get("company") or full_task.get("CreatorCompany", ""),
            dept=meta.get("dept") or full_task.get("CreatorDepartment", ""),
        )
        diag = None
        creator_ip = full_task.get("CreatorIP", "")
        if hosts:
            diag = await run_host_diagnostics(hosts[0], fallback_candidates=hosts, creator_ip=creator_ip)
        elif creator_ip:
            diag = await run_host_diagnostics(creator_ip, creator_ip=creator_ip)

        # RAG поиск
        task_text = f"{full_task.get('Name', '')}. {full_task.get('Description', '')}".strip()
        kb_matches = []
        try:
            kb_matches = await search_knowledge_base(task_text, limit=1, distance_threshold=0.70)
        except Exception:
            pass

        # Адаптивный шаблон (с проверкой на неверный сервис / редирект)
        action = auto_detect_template(full_task, diag, kb_matches, redirect_mode=redirect_mode)

        # Форматирование сетевого бейджа
        net_badge = "⚪ Нет хоста"
        if diag:
            if diag.get("is_online"):
                rtt = f" ({diag.get('avg_rtt')})" if diag.get("avg_rtt") else ""
                net_badge = f"🟢 В сети{rtt}"
            else:
                net_badge = f"🔴 Офлайн [{diag.get('target')}]"

        att_count = len(full_task.get("_attachments_list", []))
        att_str = f"📸 {att_count}" if att_count > 0 else "—"

        return {
            "index": idx,
            "task_id": t_id,
            "creator_info": f"{creator}<br>`{room}` / `{phone}`",
            "summary": (full_task.get("Name") or "")[:50],
            "net_badge": net_badge,
            "attachments": att_str,
            "action": action,
            "is_redirect": action.get("is_redirect", False),
            "target_service_name": action.get("target_service_name", ""),
            "full_task": full_task,
        }

    if sem:
        async with sem:
            return await _do_process()
    return await _do_process()


async def cmd_batch(args):
    """Пакетный анализ стопки заявок со сводной матрицей (с поддержкой фильтра редиректов)."""
    client = IntraServiceClient()
    try:
        service_ids = None
        service_name = None
        if args.service:
            s_name, s_ids = await client.get_service_subtree(args.service)
            if not s_ids:
                print(f"❌ Раздел/сервис '{args.service}' не найден в каталоге IntraService.", file=sys.stderr)
                print("💡 Выполните `uv run python helpdesk_tool.py services`, чтобы увидеть список доступных номеров.", file=sys.stderr)
                sys.exit(1)
            service_ids = s_ids
            service_name = s_name

        is_redirect_mode = getattr(args, "redirect", False)

        # Если включен режим редиректа или фильтрация по сервису, берем большую выборку для поиска кандидатов
        fetch_size = args.limit
        if service_ids or is_redirect_mode:
            fetch_size = max(args.limit * 6, 50)

        raw_tasks = await client.get_tasks_by_filter(
            args.filter,
            page=args.page,
            page_size=fetch_size,
            service_ids=service_ids,
        )
        if service_ids:
            raw_tasks = [t for t in raw_tasks if t.get("ServiceId") in service_ids]

        # Фильтрация пропущенных оператором заявок в текущей сессии
        if not getattr(args, "include_skipped", False):
            skipped_ids = get_skipped_task_ids()
            if skipped_ids:
                raw_tasks = [t for t in raw_tasks if t.get("Id") not in skipped_ids]

        if not raw_tasks:
            target_info = f" в разделе «{service_name}»" if service_name else ""
            print(f"Очередь заявок{target_info} пуста (или все кандидаты уже обработаны/пропущены в текущей смене)!")
            return

        hdr_info = f" (Раздел: {service_name})" if service_name else ""
        mode_info = " [Режим поиска РЕДИРЕКТОВ]" if is_redirect_mode else ""
        sem = asyncio.Semaphore(5)
        tasks_coros = [
            process_single_ticket_for_batch(client, t, idx + 1, redirect_mode=is_redirect_mode, sem=sem)
            for idx, t in enumerate(raw_tasks)
        ]
        results = await asyncio.gather(*tasks_coros)

        # Если запрошен режим редиректа, оставляем только заявки, требующие отмены/перенаправления
        if is_redirect_mode:
            results = [r for r in results if r.get("is_redirect")]
            results = results[:args.limit]
            for i, r in enumerate(results, start=1):
                r["index"] = i

            if not results:
                target_info = f" в разделе «{service_name}»" if service_name else " в очереди"
                print(f"\n✅ Заявок, требующих отмены и редиректа{target_info}, не обнаружено.")
                print("Все проверенные тикеты соответствуют своим разделам каталога услуг.")
                return
        else:
            results = results[:args.limit]

        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return

        title = f"Раздел «{service_name}»" if service_name else "Все разделы"
        if is_redirect_mode:
            print(f"\n### 🔄 Заявки на отмену и редирект: {title} (Найдено: {len(results)})\n")
            print("| № | Тикет | Заявитель (Каб / Тел) | Суть инцидента | Текущий раздел ➔ Рекомендованный сервис | Статус |")
            print("|---|---|---|---|---|:---:|")
            for r in results:
                t_id = r["task_id"]
                action = r["action"]
                curr_svc = action.get("current_service_name") or r["full_task"].get("ServiceName", "—")
                target_svc = action.get("target_service_name", "—")
                print(
                    f"| **{r['index']}** | **#{t_id}** | {r['creator_info']} | {r['summary']} | "
                    f"`{curr_svc}` ➔ **{target_svc}** | **🎯 {action['status_name']} (30)** |"
                )
        else:
            print(f"\n### 📋 Очередь 1-й линии: {title} (Пачка {args.page}: Заявки 1–{len(results)})\n")
            print("| № | Тикет | Заявитель (Каб / Тел) | Суть инцидента | Сеть хоста | 📎 | Рекомендация (Статус / Комментарий) |")
            print("|---|---|---|---|:---:|:---:|---|")
            
            for r in results:
                t_id = r["task_id"]
                action = r["action"]
                short_comment = action['comment'].split('\n')[0][:40] + "..."
                print(
                    f"| **{r['index']}** | **#{t_id}** | {r['creator_info']} | {r['summary']} | "
                    f"{r['net_badge']} | {r['attachments']} | "
                    f"**{action['status_name']}**<br>💬 *«{short_comment}»* |"
                )

        print("\n---")
        print("⚡ **Шорткаты для оператора:**")
        print("• `все` или `+` — применить сразу все рекомендации (1–{0})".format(len(results)))
        print("• `1, 2, 4` — применить выбранные номера")
        print("• `детали <N>` — раскрыть подробную карточку тикета")
        print("• `скриншот <N>` — скачать и посмотреть вложение")
        print("• `шаблон <имя> для <N>` — сменить шаблон (wrong_service, hardware_repair, 1c_issue, printer_issue, wifi_access, pc_offline)")
    finally:
        await client.close()


async def cmd_redirect(args):
    """Поиск и пакетная обработка заявок, требующих отмены из-за неверного сервиса."""
    args.redirect = True
    await cmd_batch(args)


async def cmd_duplicates(args):
    """Поиск и пакетная отмена заявок-дубликатов в очереди 1-й линии."""
    client = IntraServiceClient()
    try:
        fetch_size = max(args.limit * 5, 50)
        tasks = await client.get_tasks_by_filter(args.filter, page=1, page_size=fetch_size)
        
        # Исключаем пропущенные и уже финализированные
        skipped_ids = get_skipped_task_ids() if not getattr(args, "include_skipped", False) else set()
        active_tasks = [t for t in tasks if t.get("Id") not in skipped_ids and t.get("StatusId") not in (29, 30)]
        
        detector = DuplicateDetector()
        duplicates = detector.find_duplicates(active_tasks)
        duplicates = duplicates[:args.limit]

        if args.json:
            print(json.dumps(duplicates, ensure_ascii=False, indent=2))
            return

        if not duplicates:
            print(f"\n=== 🔍 Поиск дубликатов в очереди 1-й линии (Фильтр #{args.filter}) ===")
            print(f"✅ В проверенных {len(active_tasks)} открытых заявках дубликатов не обнаружено.\n")
            return

        print(f"\n### 🗑️ Обнаружены заявки-дубликаты в очереди 1-й линии (Найдено: {len(duplicates)})\n")
        for idx, d in enumerate(duplicates, 1):
            m_task = d["master_task"]
            dup_task = d["duplicate_task"]
            m_id = d["master_task_id"]
            dup_id = d["duplicate_task_id"]
            conf = d["confidence"]
            reason = d["reason"]
            action = d["action"]
            
            creator = dup_task.get("Creator", "—")
            meta = dup_task.get("_field_meta") or {}
            phone = meta.get("phone") or dup_task.get("CreatorPhone") or "—"
            room = meta.get("room") or "—"
            
            m_name = m_task.get("Name", "—")
            m_created = (m_task.get("Created") or "")[:16].replace("T", " ")
            dup_created = (dup_task.get("Created") or "")[:16].replace("T", " ")

            print(f"### [{idx}] [#{dup_id}](https://servicedesk.corporate.loc/Task/View/{dup_id}) ➔ 🎯 **Отменена (Дубликат)** ⭐ {conf}/10")
            print(f"- **Заявитель:** {creator} (📍 {room} | 📞 `{phone}`)")
            print(f"- **Основная заявка:** [#{m_id}](https://servicedesk.corporate.loc/Task/View/{m_id}) от {m_created} (*«{m_name}»*)")
            print(f"- **Текущая заявка:** #{dup_id} от {dup_created} (*«{dup_task.get('Name', '—')}»*)")
            print(f"- **Причина:** {reason}")
            print(f"> 💬 **Ответ заявителю:**")
            print(f"> *«{action['comment']}»*\n")

        print("---")
        print("⚡ **Шорткаты для оператора:**")
        print(f"• `все` или `+` — отменить все обнаруженные дубликаты (1–{len(duplicates)}) со статусом 30")
        print("• `1, 2` — отменить только выбранные дубликаты")
        print("• `детали <N>` — открыть полную карточку")
    finally:
        await client.close()



async def cmd_apply(args):
    client = IntraServiceClient()
    try:
        raw_ids = str(args.task_id).split(",")
        task_ids = [int(x.strip()) for x in raw_ids if x.strip().isdigit()]
        if not task_ids:
            print(f"❌ Некорректный ID заявки: '{args.task_id}'", file=sys.stderr)
            sys.exit(1)

        status_id = args.status
        comment = args.comment
        expenses = args.expenses
        executor_ids = getattr(args, "executor", None) or "8664,10502"
        dry_run = args.dry_run

        print(f"=== Применение решения к заявкам: {', '.join(f'#{tid}' for tid in task_ids)} ===")
        print(f"Целевой статус ID: {status_id}")
        print(f"Исполнители ID:   {executor_ids} (Беликов Ален + Беликов Ален_assitant)")
        print(f"Трудозатраты:     {expenses} мин." if expenses else "Трудозатраты: не списываются")
        print(f"Комментарий:\n{comment}\n")

        if dry_run:
            print("[DRY-RUN] Режим симуляции. Изменения не отправлены.")
            return

        all_ok = True
        for task_id in task_ids:
            # 0. Защита от перезаписи: проверяем текущий статус заявки в живой базе
            try:
                curr = await client.get_task_details(task_id)
                if curr and curr.get("StatusId") in (29, 30) and status_id not in (29, 30):
                    curr_name = curr.get("StatusName") or str(curr.get("StatusId"))
                    print(f"ℹ️ Заявка #{task_id} уже финализирована вручную ({curr_name}). Изменения пропущены.")
                    add_applied_tasks([task_id], curr.get("StatusId"))
                    continue
            except Exception:
                pass

            # 0.1. Защита для Wi-Fi: при попытке закрытия (29) обязательно проверяем/выдаем доступ в AD
            if status_id == 29 and any(w in (comment or "").lower() for w in ["wi-fi", "wifi", "вайфай", "вай-фай"]):
                ad_exec = ActiveDirectoryExecutor()
                identity = ad_exec.extract_identity_from_task(curr or await client.get_task_details(task_id) or {})
                if identity:
                    print(f"⚡ [AD Auto-Execution] Проверка и выдача доступа в AD для '{identity}'...")
                    ad_res = ad_exec.grant_wlan_access(identity)
                    if not ad_res.success:
                        print(f"❌ СБОЙ AD: {ad_res.message}. Перевод в статус 29 отменен, заявка переводится в '27 В работе'.", file=sys.stderr)
                        status_id = 27
                    else:
                        print(f"✓ AD: {ad_res.message}")

            # 1. Если статус финальный или меняется (29, 30, 35, 48), сначала берем в работу (27) с назначением исполнителя
            if status_id != 27:
                await client.update_task(task_id=task_id, status_id=27, executor_ids=executor_ids)

            # 2. Атомарное обновление заявки в целевой статус (статус, комментарий, исполнитель)
            update_ok = await client.update_task(
                task_id=task_id,
                status_id=status_id,
                comment=comment if comment else None,
                executor_ids=executor_ids,
            )

            # 3. Списание трудозатрат (без комментария)
            exp_ok = True
            if expenses and expenses > 0:
                # Для списания берем ID первого исполнителя (Беликов Ален: 8664)
                first_exec = executor_ids.split(",")[0].strip() if "," in str(executor_ids) else str(executor_ids)
                exp_user_id = int(first_exec) if first_exec.isdigit() else 8664
                exp_ok = await client.add_expenses(
                    task_id=task_id,
                    minutes=expenses,
                    user_id=exp_user_id,
                )

            # 3. Автообучение RAG
            if status_id in (29, 30) and comment and comment.strip():
                try:
                    task_data = await client.get_task_details(task_id)
                    if task_data:
                        t_name = task_data.get("Name") or f"Заявка #{task_id}"
                        t_desc = task_data.get("Description") or ""
                        s_id = task_data.get("ServiceId") or 0
                        s_name = task_data.get("ServiceName") or "Общие"
                        st_name = "Выполнена" if status_id == 29 else "Отменена"

                        await index_task_record(
                            task_id=task_id,
                            original_name=t_name,
                            problem=f"{t_name}. {t_desc}".strip(),
                            solution=comment.strip(),
                            service_id=s_id,
                            service_name=s_name,
                            status_name=st_name,
                            classification_data={"type": "auto_indexed_by_agent", "status_id": status_id},
                        )
                except Exception:
                    pass

            if update_ok and exp_ok:
                print(f"✓ УСПЕХ: Заявка #{task_id} успешно обновлена!")
                add_applied_tasks([task_id], status_id)
            else:
                print(f"⚠️ ВНИМАНИЕ: Ошибка обновления заявки #{task_id} (update={update_ok}, expenses={exp_ok})", file=sys.stderr)
                all_ok = False

        if not all_ok:
            sys.exit(1)
    finally:
        await client.close()


async def cmd_wlan(args):
    """Автоматическая выдача Wi-Fi доступа через Active Directory (WLAN-WORKNET) и закрытие заявки."""
    client = IntraServiceClient()
    ad_exec = ActiveDirectoryExecutor()
    try:
        raw_ids = str(args.task_id).split(",")
        task_ids = [int(x.strip()) for x in raw_ids if x.strip().isdigit()]
        if not task_ids:
            print(f"❌ Некорректный ID заявки: '{args.task_id}'", file=sys.stderr)
            sys.exit(1)

        executor_ids = getattr(args, "executor", None) or "8664,10502"
        dry_run = args.dry_run

        print(f"=== ⚡ Автоматическая выдача доступа Wi-Fi (Active Directory) ===")
        print(f"Целевые заявки: {', '.join(f'#{tid}' for tid in task_ids)}")
        print(f"Группа AD:      {ad_exec.target_wlan_group}")
        print(f"Исполнители:    {executor_ids} (Беликов Ален + Беликов Ален_assitant)\n")

        for task_id in task_ids:
            task = await client.get_task_details(task_id)
            if not task:
                print(f"❌ Заявка #{task_id} не найдена в IntraService.", file=sys.stderr)
                continue

            identity = getattr(args, "identity", None) or getattr(args, "login", None)
            if not identity:
                identity = ad_exec.extract_identity_from_task(task)

            if not identity:
                print(f"❌ Заявка #{task_id}: Не удалось определить пользователя (логин/ФИО). Укажите явно: `--login <username>`", file=sys.stderr)
                continue

            print(f"--- Обработка заявки #{task_id} ---")
            print(f"Заявитель:   {task.get('Creator')} (Логин в ИС: {task.get('CreatorLogin')})")
            print(f"Субъект AD:  '{identity}'")

            if dry_run:
                st = ad_exec.get_user_status(identity)
                print(f"[DRY-RUN] Статус пользователя в AD: {st}")
                continue

            # Выполнение добавления в AD
            ad_res = ad_exec.grant_wlan_access(identity)
            if not ad_res.success:
                print(f"❌ ОШИБКА AD: {ad_res.message}", file=sys.stderr)
                print(f"⚠️ Заявка #{task_id} переводится в статус '27 В работе' без закрытия.", file=sys.stderr)
                await client.update_task(task_id=task_id, status_id=27, executor_ids=executor_ids)
                continue

            print(f"✓ {ad_res.message}")

            # Формирование комментария
            comment = (
                "Доступ к Wi-Fi предоставлен.\n"
                "Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил.\n"
                "Если возникнут проблемы с подключением, приходите в АБК-3, кабинет 112."
            )

            # Двухэтапная финализация в IntraService: 27 ➔ 29
            await client.update_task(task_id=task_id, status_id=27, executor_ids=executor_ids)
            update_ok = await client.update_task(
                task_id=task_id,
                status_id=29,
                comment=comment,
                executor_ids=executor_ids,
            )
            exp_ok = await client.add_expenses(task_id=task_id, minutes=10, user_id=8664)

            # Автообучение RAG
            try:
                t_name = task.get("Name") or f"Заявка #{task_id}"
                t_desc = task.get("Description") or ""
                await index_task_record(
                    task_id=task_id,
                    original_name=t_name,
                    problem=f"{t_name}. {t_desc}".strip(),
                    solution=comment.strip(),
                    service_id=task.get("ServiceId") or 0,
                    service_name=task.get("ServiceName") or "Wi-Fi",
                    status_name="Выполнена",
                    classification_data={"type": "auto_wlan_execution", "status_id": 29},
                )
            except Exception:
                pass

            if update_ok and exp_ok:
                print(f"🎯 УСПЕХ: Заявка #{task_id} закрыта со статусом 29 (Выполнена) и списанием 10 мин трудозатрат.\n")
                add_applied_tasks([task_id], 29)
            else:
                print(f"⚠️ Ошибка обновления статуса заявки #{task_id} в IntraService.", file=sys.stderr)
    finally:
        await client.close()


async def cmd_summary(args):
    """Суммаризация истории и переписки инцидента через локальную микро-нейросеть Ollama."""
    client = IntraServiceClient()
    ollama = OllamaClient()
    try:
        task_id = args.task_id
        task = await client.get_task_details(task_id)
        if not task:
            print(f"❌ Заявка #{task_id} не найдена в IntraService.", file=sys.stderr)
            sys.exit(1)

        lifetime = await client.get_task_history(task_id) or []
        # Фильтруем записи с комментариями
        comments = []
        for item in lifetime:
            txt = (item.get("Comment") or item.get("Description") or item.get("Text") or "").strip()
            if txt:
                comments.append({
                    "UserName": item.get("UserName") or item.get("Creator") or "Пользователь",
                    "Created": item.get("Created") or "",
                    "Text": txt,
                })

        t_name = task.get("Name") or ""
        t_desc = task.get("Description") or ""

        if not await ollama.is_available():
            print(f"⚠️ Локальная нейросеть Ollama недоступна на {ollama.base_url}.", file=sys.stderr)
            print("💡 Запустите Ollama (`docker compose up -d ollama` или `ollama serve`).\n")
            print(f"=== 📋 Заявка #{task_id}: {t_name} ===")
            print(f"Описание: {t_desc}\n")
            print(f"Всего комментариев в истории: {len(comments)}")
            for c in comments[-3:]:
                print(f"• [{c['Created'][:16]}] {c['UserName']}: {c['Text'][:100]}...")
            return

        summary = await ollama.summarize_task_history(
            task_id=task_id,
            task_name=t_name,
            task_desc=t_desc,
            comments=comments,
        )

        if not summary:
            print(f"❌ Не удалось сформировать AI-сводку по заявке #{task_id}.", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(summary.model_dump(), ensure_ascii=False, indent=2))
            return

        print(f"### 📋 AI-Сводка по заявке [#{task_id}](https://servicedesk.corporate.loc/Task/View/{task_id}) ({ollama.model})\n")
        print(f"• **Суть инцидента:** {summary.core_problem}")
        print("• **Предпринятые действия:**")
        if summary.actions_taken:
            for act in summary.actions_taken:
                print(f"  - {act}")
        else:
            print("  - Действий пока не зафиксировано")
        print(f"• **Текущий статус:** {summary.current_status}")
        print(f"• **Рекомендованный следующий шаг:** {summary.recommended_next_step}\n")

    finally:
        await client.close()


async def cmd_skip(args):
    """Помечает заявку или список заявок как пропущенные в текущей смене/сессии."""
    raw_ids = [x.strip() for x in str(args.task_id).split(",") if x.strip()]
    task_ids = []
    for x in raw_ids:
        try:
            task_ids.append(int(x))
        except ValueError:
            pass
    if not task_ids:
        print("❌ Не указаны валидные ID заявок для пропуска.", file=sys.stderr)
        sys.exit(1)

    add_skipped_tasks(task_ids, reason=args.reason or "operator_skipped")
    print(f"✓ Заявки #{', #'.join(str(x) for x in task_ids)} помечены как пропущенные в текущей смене.")
    print("💡 Они не будут появляться в /triage и /redirect до сброса (`reset-session`).")


async def cmd_reset_session(args):
    """Сбрасывает сессионное состояние и возвращает все пропущенные заявки в очередь."""
    reset_session_state()
    print("✓ Сессионное состояние сброшено. Все пропущенные заявки снова активны для разбора.")


async def cmd_search_kb(args):
    """Поиск по RAG базе знаний pgvector."""
    query = args.query.strip()
    matches = await search_knowledge_base(query, limit=args.limit, distance_threshold=args.threshold)

    if args.json:
        print(json.dumps(matches, ensure_ascii=False, indent=2))
        return

    print(f"=== Результаты поиска в базе знаний (Найдено: {len(matches)}) ===")
    if not matches:
        print("Похожих решений в базе знаний не обнаружено.")
        return

    for m in matches:
        print(f"\n• [Кейс #{m['task_id']} | Сходство: {m['similarity_pct']}% | Раздел: {m['service_name']}]")
        print(f"  Тема:     {m['name']}")
        print(f"  Проблема: {m['problem']}")
        print(f"  Решение:\n    {m['solution']}")


async def cmd_sync_kb(args):
    """Пакетная синхронизация закрытых заявок 1-й линии в базу знаний."""
    client = IntraServiceClient()
    try:
        stats = await sync_history_kb(
            is_client=client,
            limit=args.limit,
            days=args.days,
            dry_run=args.dry_run,
        )
        print("\n=== Результаты синхронизации базы знаний ===")
        print(f"Всего проанализировано: {stats['fetched']}")
        print(f"Отобрано по качеству:  {stats['accepted']}")
        print(f"Отсеяно фильтром:      {stats['skipped']}")
        print(f"Успешно проиндексировано: {stats['indexed']}")
    finally:
        await client.close()


async def cmd_catalog(args):
    client = IntraServiceClient()
    try:
        catalog = await client.get_service_catalog()
        if args.search:
            q = args.search.lower()
            catalog = [s for s in catalog if q in (s.get("Name") or "").lower()]

        if args.json:
            print(json.dumps(catalog, ensure_ascii=False, indent=2))
            return

        print(f"=== Каталог услуг IntraService (Найдено: {len(catalog)}) ===")
        for s in catalog:
            print(f"• ID {s.get('Id')}: {s.get('Name')} (Parent: {s.get('ParentId')})")
    finally:
        await client.close()


async def cmd_history(args):
    client = IntraServiceClient()
    try:
        history = await client.get_task_history(args.task_id)
        if args.json:
            print(json.dumps(history, ensure_ascii=False, indent=2))
            return

        print(f"=== История заявки #{args.task_id} (Записей: {len(history)}) ===")
        for h in history:
            date = h.get("Created") or h.get("EventDate", "")
            user = h.get("UserName") or h.get("Creator", "Система")
            desc = h.get("Description") or h.get("EventName", "")
            print(f"[{date}] {user}: {desc}")
    finally:
        await client.close()


async def cmd_attachment(args):
    """Скачивание вложений заявки для просмотра оператором/агентом."""
    client = IntraServiceClient()
    try:
        task = await client.get_task_details(args.task_id)
        if not task:
            print(f"Заявка #{args.task_id} не найдена.")
            return

        attachments = task.get("_attachments_list", [])
        if not attachments:
            print(f"В заявке #{args.task_id} нет прикрепленных файлов.")
            return

        print(f"=== Вложения заявки #{args.task_id} ({len(attachments)}) ===")
        for idx, att in enumerate(attachments, 1):
            name = att.get("FileName", f"file_{idx}")
            f_id = att.get("Id") or idx
            save_path = os.path.join(DOWNLOADS_DIR, f"task_{args.task_id}_{name}")
            
            ok = await client.download_attachment_file(args.task_id, f_id, save_path)
            if ok:
                print(f"✓ Скачан: {name} -> {save_path}")
            else:
                print(f"• Файл вложения: {name} (ID: {f_id})")
    finally:
        await client.close()


async def cmd_check_db(args):
    """Проверка доступности Tier 1 (PostgreSQL pgvector) и LiteLLM."""
    print("=== Проверка статуса базы знаний (RAG) ===")
    ok, msg = await test_db_connection()
    print(msg)
    if not ok or "Fallback" in msg:
        print("\n💡 Для запуска PostgreSQL и LiteLLM выполните:")
        print("   uv run python helpdesk_tool.py start-db")
        print("   (или в корне проекта: docker compose up -d postgres litellm)")
        if getattr(args, "exit_code", False):
            sys.exit(1)
    else:
        print("\n✓ Tier 1 активен. Семантический поиск и индексация выполняются в PostgreSQL pgvector.")


async def cmd_start_db(args):
    """Автоматический запуск контейнеров PostgreSQL и LiteLLM через Docker Compose."""
    import subprocess
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print("🚀 Запуск контейнеров PostgreSQL и LiteLLM через Docker Compose...")

    cmd = ["docker", "compose", "up", "-d", "postgres", "litellm"]
    try:
        proc = subprocess.run(cmd, cwd=root_dir, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            print(f"🔴 Ошибка запуска Docker: {proc.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        if proc.stdout.strip():
            print(proc.stdout.strip())
        else:
            print("✓ Команда docker compose выполнена успешно.")
    except Exception as e:
        print(f"🔴 Не удалось запустить Docker команду: {e}", file=sys.stderr)
        sys.exit(1)

    print("⏳ Ожидание инициализации PostgreSQL (до 10 сек)...")
    for _ in range(10):
        await asyncio.sleep(1)
        from kb import get_pg_connection
        conn = await get_pg_connection()
        if conn:
            await conn.close()
            break

    # Инициализация схемы
    schema_ok = await ensure_db_schema()
    if schema_ok:
        print("✓ Схема PostgreSQL (таблица task_knowledge_base + pgvector) готова к работе!")

    ok, msg = await test_db_connection()
    print(f"\nСтатус: {msg}")


def main():
    parser = argparse.ArgumentParser(description="Helpdesk I/O, Batch Triage & RAG Tools for AGY Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # check-db
    p_cdb = subparsers.add_parser("check-db", help="Проверка статуса подключения к PostgreSQL pgvector")
    p_cdb.add_argument("--exit-code", action="store_true", help="Вернуть exit code 1 при недоступности Tier 1")

    # start-db
    p_sdb = subparsers.add_parser("start-db", help="Автоматический запуск PostgreSQL и LiteLLM в Docker")

    # batch
    p_b = subparsers.add_parser("batch", help="Сводный дашборд стопки заявок с авто-рекомендациями")
    p_b.add_argument("--filter", type=int, default=int(os.getenv("FILTER_ID", "984")), help="ID фильтра очереди")
    p_b.add_argument("--service", "-s", type=str, default=None, help="Номер раздела (01..16, 2, 3, 6) или название сервиса для фильтрации")
    p_b.add_argument("--limit", type=int, default=5, help="Размер пачки заявок")
    p_b.add_argument("--page", type=int, default=1, help="Номер пачки/страницы")
    p_b.add_argument("--redirect", "-r", action="store_true", help="Поиск только заявок, требующих отмены и редиректа в другие сервисы")
    p_b.add_argument("--include-skipped", action="store_true", help="Включить в выборку ранее пропущенные заявки")
    p_b.add_argument("--json", action="store_true", help="Вывод в JSON")

    # redirect
    p_red = subparsers.add_parser("redirect", help="Поиск и отмена заявок, требующих редиректа в другие сервисы")
    p_red.add_argument("--filter", type=int, default=int(os.getenv("FILTER_ID", "984")), help="ID фильтра очереди")
    p_red.add_argument("--service", "-s", type=str, default=None, help="Номер раздела (01..16, 2, 3, 6) или название сервиса для фильтрации")
    p_red.add_argument("--limit", type=int, default=5, help="Размер пачки заявок")
    p_red.add_argument("--page", type=int, default=1, help="Номер пачки/страницы")
    p_red.add_argument("--include-skipped", action="store_true", help="Включить в выборку ранее пропущенные заявки")
    p_red.add_argument("--json", action="store_true", help="Вывод в JSON")

    # task
    p_t = subparsers.add_parser("task", help="Детальная карточка конкретной заявки")
    p_t.add_argument("task_id", type=int, help="ID заявки")
    p_t.add_argument("--json", action="store_true", help="Вывод в JSON")

    # queue
    p_q = subparsers.add_parser("queue", help="Простой список очереди")
    p_q.add_argument("--filter", type=int, default=int(os.getenv("FILTER_ID", "984")), help="ID фильтра очереди")
    p_q.add_argument("--service", "-s", type=str, default=None, help="Номер раздела (01..16, 2, 3, 6) или название сервиса для фильтрации")
    p_q.add_argument("--limit", type=int, default=10, help="Количество заявок")
    p_q.add_argument("--json", action="store_true", help="Вывод в JSON")

    # services
    p_srv = subparsers.add_parser("services", help="Список корневых разделов каталога с номерами")
    p_srv.add_argument("--json", action="store_true", help="Вывод в JSON")

    # diagnose
    p_d = subparsers.add_parser("diagnose", help="Сетевая диагностика ПК / IP")
    p_d.add_argument("target", type=str, help="Имя хоста или IP")
    p_d.add_argument("--json", action="store_true", help="Вывод в JSON")

    # search-kb
    p_skb = subparsers.add_parser("search-kb", help="Поиск по RAG базе знаний pgvector")
    p_skb.add_argument("query", type=str, help="Текст поискового запроса")
    p_skb.add_argument("--limit", type=int, default=3, help="Лимит совпадений")
    p_skb.add_argument("--threshold", type=float, default=0.70, help="Порог расстояния")
    p_skb.add_argument("--json", action="store_true", help="Вывод в JSON")

    # sync-kb
    p_sykb = subparsers.add_parser("sync-kb", help="Умная синхронизация закрытых заявок")
    p_sykb.add_argument("--limit", type=int, default=50, help="Лимит выгрузки")
    p_sykb.add_argument("--days", type=int, default=30, help="Количество дней")
    p_sykb.add_argument("--dry-run", action="store_true", help="Режим симуляции")

    # apply
    p_a = subparsers.add_parser("apply", help="Применение решения к заявке (или списку ID через запятую)")
    p_a.add_argument("task_id", type=str, help="ID заявки или список ID через запятую (например 139088,138972)")
    p_a.add_argument("--status", type=int, required=True, help="Целевой ID статуса")
    p_a.add_argument("--comment", type=str, default="", help="Текст комментария")
    p_a.add_argument("--expenses", type=int, default=0, help="Списание трудозатрат в минутах")
    p_a.add_argument("--executor", type=str, default="8664,10502", help="ID исполнителей через запятую (по умолчанию 8664,10502 - Беликов Ален и Беликов Ален_assitant)")
    p_a.add_argument("--dry-run", action="store_true", help="Режим симуляции")

    # skip
    p_sk = subparsers.add_parser("skip", help="Пропуск заявки в текущей сессии")
    p_sk.add_argument("task_id", type=str, help="ID заявки или список ID через запятую")
    p_sk.add_argument("--reason", type=str, default="operator_skipped", help="Причина пропуска")

    # reset-session
    p_rs = subparsers.add_parser("reset-session", help="Сброс сессионного кэша пропущенных заявок")

    # catalog
    p_c = subparsers.add_parser("catalog", help="Поиск по каталогу услуг")
    p_c.add_argument("--search", type=str, default=None, help="Поисковый запрос")
    p_c.add_argument("--json", action="store_true", help="Вывод в JSON")

    # history
    p_h = subparsers.add_parser("history", help="История заявки")
    p_h.add_argument("task_id", type=int, help="ID заявки")
    p_h.add_argument("--json", action="store_true", help="Вывод в JSON")

    # attachment
    p_att = subparsers.add_parser("attachment", help="Скачивание вложений заявки")
    p_att.add_argument("task_id", type=int, help="ID заявки")

    # wlan (Active Directory WLAN Automation)
    p_wlan = subparsers.add_parser("wlan", help="Автоматическая выдача Wi-Fi доступа в AD (WLAN-WORKNET) и закрытие заявки")
    p_wlan.add_argument("task_id", type=str, help="ID заявки или список ID через запятую")
    p_wlan.add_argument("--login", "--identity", type=str, default=None, help="Явный логин sAMAccountName или ФИО пользователя")
    p_wlan.add_argument("--executor", type=str, default="8664,10502", help="ID исполнителей")
    p_wlan.add_argument("--dry-run", action="store_true", help="Режим симуляции без изменений")

    # summary (Ollama AI Thread Summarization)
    p_sum = subparsers.add_parser("summary", help="AI-суммаризация истории и переписки инцидента через Qwen2.5:1.5B")
    p_sum.add_argument("task_id", type=int, help="ID заявки")
    p_sum.add_argument("--json", action="store_true", help="Вывод в JSON")

    # duplicates / dedup
    p_dup = subparsers.add_parser("duplicates", aliases=["dedup"], help="Поиск и отмена заявок-дубликатов в очереди 1-й линии")
    p_dup.add_argument("--filter", type=int, default=int(os.getenv("FILTER_ID", "984")), help="ID фильтра очереди")
    p_dup.add_argument("--limit", type=int, default=10, help="Максимальное количество дубликатов для вывода")
    p_dup.add_argument("--include-skipped", action="store_true", help="Включить в выборку ранее пропущенные заявки")
    p_dup.add_argument("--json", action="store_true", help="Вывод в JSON")

    args = parser.parse_args()

    dispatch = {
        "check-db": cmd_check_db,
        "start-db": cmd_start_db,
        "batch": cmd_batch,
        "redirect": cmd_redirect,
        "duplicates": cmd_duplicates,
        "dedup": cmd_duplicates,
        "task": cmd_task,
        "queue": cmd_queue,
        "services": cmd_services,
        "diagnose": cmd_diagnose,
        "search-kb": cmd_search_kb,
        "sync-kb": cmd_sync_kb,
        "apply": cmd_apply,
        "wlan": cmd_wlan,
        "summary": cmd_summary,
        "skip": cmd_skip,
        "reset-session": cmd_reset_session,
        "catalog": cmd_catalog,
        "history": cmd_history,
        "attachment": cmd_attachment,
    }

    asyncio.run(dispatch[args.command](args))


if __name__ == "__main__":
    main()
