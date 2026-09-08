"""
Команды автоматической установки и настройки принтеров на рабочих станциях через Unified Command Bus.
"""

import asyncio
import re
import sys
from typing import Any

from core_api_client import CoreApiClient


def register_parser(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "printer",
        aliases=["install-printer"],
        help="Удаленная установка принтера/МФУ на ПК через Windows Execution Worker и финализация заявки",
    )
    p.add_argument("task_id", type=str, nargs="?", default=None, help="ID заявки IntraService (опционально)")
    p.add_argument("--pc", "--host", "--pc-name", dest="pc_name", type=str, default=None, help="Имя ПК (хоста)")
    p.add_argument("--printer", "--printer-name", "--model", dest="printer_name", type=str, default=None, help="Модель или имя принтера")
    p.add_argument("--ip", "--printer-ip", "--address", dest="printer_ip", type=str, default=None, help="IP адрес принтера")
    p.add_argument("--connection-type", choices=["tcpip", "usb"], default="tcpip", help="Тип подключения (по умолчанию tcpip)")
    p.add_argument("--dry-run", action="store_true", help="Симуляция без реального выполнения на хосте")
    p.add_argument("--approve", action="store_true", help="Автоматически подтвердить операцию (HITL approval)")
    p.add_argument("--no-close", action="store_true", help="Не переводить заявку в статус 29 после установки")
    p.add_argument("--comment", type=str, default=None, help="Кастомный комментарий к заявке при закрытии")
    p.add_argument("--executor", type=str, default="8664,10502", help="ID исполнителей в IntraService")


async def handle(args: Any) -> None:
    client = CoreApiClient()
    try:
        task_id_raw = args.task_id
        task_id = int(task_id_raw) if task_id_raw and str(task_id_raw).strip().isdigit() else None

        pc_name = args.pc_name
        printer_name = args.printer_name
        printer_ip = args.printer_ip
        conn_type = args.connection_type
        dry_run = args.dry_run

        print("=== 🖨️  Установка принтера через Unified Command Bus ===")

        # Если передан task_id, но реквизиты не указаны в CLI, подтягиваем их из карточки задачи
        if task_id and (not pc_name or not printer_name or not printer_ip):
            print(f"🔍 Загрузка реквизитов из заявки #{task_id}...")
            card = await client.get_task_card(task_id)
            if card:
                task_data = card.get("task") or {}
                meta = task_data.get("_field_meta") or {}

                if not pc_name:
                    pc_name = (
                        meta.get("pc_name")
                        or task_data.get("pc_name")
                        or task_data.get("PCName")
                        or card.get("pc_name")
                    )
                    if not pc_name and task_data.get("Data"):
                        from shared.normalizer import extract_pc_names_from_text
                        pcs = extract_pc_names_from_text(task_data["Data"])
                        if pcs:
                            pc_name = pcs[0]

                if not printer_ip:
                    for cf in task_data.get("CustomFields") or []:
                        val = str(cf.get("Value") or "").strip()
                        if re.match(r"^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$", val):
                            printer_ip = val
                            break
                    if not printer_ip and task_data.get("Description"):
                        m = re.search(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", task_data["Description"])
                        if m:
                            printer_ip = m.group(0)

                if not printer_name:
                    printer_name = (
                        task_data.get("PrinterName")
                        or task_data.get("PrinterModel")
                        or meta.get("printer_name")
                    )
                    if not printer_name and task_data.get("Name"):
                        name_text = task_data["Name"].strip()
                        if any(k in name_text.lower() for k in ["kyocera", "ecosys", "laserjet", "canon", "xerox", "pantum", "hp "]):
                            printer_name = name_text

        # Валидация перед запуском
        if not pc_name:
            print("❌ Ошибка: не указано имя ПК (--pc).", file=sys.stderr)
            sys.exit(1)
        if not printer_name:
            print("❌ Ошибка: не указана модель/имя принтера (--printer).", file=sys.stderr)
            sys.exit(1)

        print(f"  • Рабочая станция: {pc_name.upper()}")
        print(f"  • Модель принтера: {printer_name}")
        if printer_ip:
            print(f"  • Сетевой IP адрес: {printer_ip}")
        print(f"  • Тип подключения: {conn_type}")
        if task_id:
            print(f"  • Привязка к заявке: #{task_id}")

        params = {
            "pc_name": pc_name.upper(),
            "printer_name": printer_name,
            "connection_type": conn_type,
            "printer_ip": printer_ip,
        }
        target = {
            "host": pc_name.upper(),
            "task_id": task_id,
        }

        # Отправка команды в единую шину Command Bus v2
        cmd_res = await client.submit_command(
            command_type="install_printer",
            target=target,
            params=params,
            mode="dry_run" if dry_run else "auto",
            source="cli",
        )

        if not cmd_res or cmd_res.get("status") == "error":
            print(f"❌ Ошибка Command Bus: {cmd_res.get('error', 'Неизвестная ошибка')}", file=sys.stderr)
            sys.exit(1)

        if dry_run or cmd_res.get("status") == "dry_run_success":
            print(f"🔍 [DRY-RUN]: {cmd_res.get('message', 'Параметры и правила успешно проверены.')}")
            return

        job_id = cmd_res.get("job_id")
        current_status = cmd_res.get("current_status") or cmd_res.get("status")
        print(f"✓ Команда принята к исполнению (Job ID: {job_id})")

        # Если команда требует утверждения (HITL)
        if current_status == "awaiting_approval":
            if args.approve:
                print("⚡ Автоматическое утверждение команды (HITL approval)...")
                await client.confirm_command(job_id, decision="approve", reason="Approved via CLI --approve")
            else:
                print(f"⚠️  Команда {job_id} ожидает утверждения администратора (HITL).")
                print("💡 Запустите с флагом --approve для автоматического подтверждения.")
                return

        # Ожидание исполнения в воркере
        print("⏳ Установка драйвера и настройка порта на рабочей станции...", end="", flush=True)
        success = False
        final_info = {}
        for _ in range(45):
            await asyncio.sleep(2)
            st = await client.get_command_status(job_id)
            if st and st.get("status") in ("success", "failed", "cancelled"):
                print()
                final_info = st
                if st.get("status") == "success":
                    success = True
                    print(f"🎯 УСПЕХ: Принтер '{printer_name}' успешно установлен на хосте {pc_name.upper()}!")
                else:
                    err_msg = st.get("error_message") or st.get("message") or "Сбой выполнения"
                    print(f"❌ ОШИБКА ИСПОЛНЕНИЯ: {err_msg}", file=sys.stderr)
                break
            print(".", end="", flush=True)

        if not success:
            if not final_info:
                print(f"\n⚠️  Команда {job_id} еще выполняется на воркере. Проверьте статус позже.")
            return

        # Финализация заявки в IntraService
        if task_id and not args.no_close:
            default_comment = (
                f"Добрый день! Принтер {printer_name}"
                + (f" ({printer_ip})" if printer_ip else "")
                + f" успешно установлен и настроен на рабочей станции {pc_name.upper()}.\n"
                "Пробная страница отправлена на печать. По вопросам обращайтесь по телефону 49-87."
            )
            comment_to_post = args.comment or default_comment
            print(f"📝 Перевод заявки #{task_id} в статус «Выполнена» (29) со списанием 10 мин...")
            applied = await client.apply_decision(
                task_ids=[task_id],
                status_id=29,
                comment=comment_to_post,
                expenses=10,
                executor_ids=args.executor,
            )
            if applied:
                print(f"✅ Заявка #{task_id} успешно закрыта со статусом «Выполнена».")
            else:
                print(f"⚠️  Не удалось обновить статус заявки #{task_id} через Core API.", file=sys.stderr)

    except Exception as e:
        print(f"❌ Сбой выполнения: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()
