"""
Команды управления Active Directory и учетными записями: ad, wlan, create-user.
Исполняются через Unified Command Bus, а затем финализируют заявку в Core API.
"""

import asyncio
import json
import sys
from typing import Any

from core_api_client import CoreApiClient


def register_parser(subparsers: Any) -> None:
    # wlan
    p_w = subparsers.add_parser(
        "wlan",
        help="Автоматическая выдача Wi-Fi доступа в AD (WLAN-WORKNET) и закрытие заявки через Command Bus",
    )
    p_w.add_argument("task_id", type=str, help="ID заявки или список ID")
    p_w.add_argument(
        "--login",
        "--identity",
        type=str,
        default=None,
        help="Явный логин sAMAccountName или ФИО",
    )
    p_w.add_argument(
        "--dry-run",
        action="store_true",
        help="Симуляция без внесения изменений",
    )
    p_w.add_argument(
        "--executor",
        type=str,
        default=None,
        help="ID исполнителей в IntraService",
    )

    # create-user
    p_cu = subparsers.add_parser(
        "create-user",
        aliases=["new-user"],
        help="Автоматическое создание пользователя в AD и финализация заявки через Command Bus",
    )
    p_cu.add_argument("task_id", type=str, help="ID заявки или список ID")
    p_cu.add_argument("--surname", type=str, default=None, help="Фамилия")
    p_cu.add_argument("--name", type=str, default=None, help="Имя")
    p_cu.add_argument("--patronymic", type=str, default=None, help="Отчество")
    p_cu.add_argument("--company", type=str, default=None, help="Организация")
    p_cu.add_argument(
        "--department", "--dept", type=str, default=None, help="Подразделение"
    )
    p_cu.add_argument("--phone", type=str, default=None, help="Телефон")
    p_cu.add_argument("--pc-name", type=str, default=None, help="Имя ПК")
    p_cu.add_argument("--title", type=str, default=None, help="Должность")
    p_cu.add_argument(
        "--dry-run",
        action="store_true",
        help="Симуляция без внесения изменений",
    )
    p_cu.add_argument(
        "--executor",
        type=str,
        default=None,
        help="ID исполнителей в IntraService",
    )

    # ad
    p_ad = subparsers.add_parser("ad", help="Утилиты проверки Active Directory")
    p_ad.add_argument(
        "ad_action",
        choices=["search", "status", "enable", "disable", "unlock", "groups"],
        help="Действие",
    )
    p_ad.add_argument("identity", type=str, help="Логин или ФИО пользователя")
    p_ad.add_argument(
        "group_name",
        type=str,
        nargs="?",
        default=None,
        help="Имя целевой группы AD",
    )
    p_ad.add_argument("--json", action="store_true", help="Вывод в JSON")


async def handle_wlan(args: Any) -> None:
    client = CoreApiClient()
    try:
        raw_ids = str(args.task_id).split(",")
        task_ids = [int(x.strip()) for x in raw_ids if x.strip().isdigit()]
        if not task_ids:
            print(f"❌ Некорректный ID заявки: '{args.task_id}'", file=sys.stderr)
            sys.exit(1)

        dry_run = args.dry_run
        print("=== ⚡ Выдача доступа Wi-Fi через Unified Command Bus ===")

        for task_id in task_ids:
            identity = getattr(args, "identity", None) or getattr(args, "login", None)

            # Отправка команды в единую шину Command Bus
            cmd_res = await client.submit_command(
                command_type="grant_wlan",
                target={"task_id": task_id, "identity": identity},
                params={"identity": identity, "login": identity},
                mode="dry_run" if dry_run else "auto",
                source="cli",
            )

            if not cmd_res or cmd_res.get("status") == "error":
                print(
                    f"❌ Ошибка Command Bus для #{task_id}: {cmd_res.get('error', 'Неизвестная ошибка')}",
                    file=sys.stderr,
                )
                continue

            if dry_run or cmd_res.get("status") == "dry_run_success":
                print(f"🔍 [DRY-RUN] #{task_id}: {cmd_res.get('message')}")
                continue

            job_id = cmd_res.get("job_id")
            print(f"✓ Задача принята Command Bus: {job_id} (Тикет #{task_id})")

            # Ожидание исполнения в домене через воркер
            print("⏳ Исполнение в домене...", end="", flush=True)
            success = False
            for _ in range(30):
                await asyncio.sleep(1)
                st = await client.get_command_status(job_id)
                if st and st.get("status") in ("success", "failed", "cancelled"):
                    print()
                    if st.get("status") == "success":
                        print(f"🎯 УСПЕХ: Заявка #{task_id} выполнена и зафиксирована в job_log! {st.get('message', '')}")
                        success = True
                    else:
                        print(f"❌ СБОЙ: {st.get('message') or st.get('error_message')}", file=sys.stderr)
                    break
                print(".", end="", flush=True)

            if not success:
                print(f"\n⚠️  Задача {job_id} продолжает выполняться в фоне.")
    except Exception as e:
        print(f"❌ Fail-Fast: Ошибка связи с Core API ({e})", file=sys.stderr)
        print("💡 Убедитесь, что шлюз Core API запущен: docker compose ps", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def handle_create_user(args: Any) -> None:
    client = CoreApiClient()
    try:
        raw_ids = str(args.task_id).split(",")
        task_ids = [int(x.strip()) for x in raw_ids if x.strip().isdigit()]
        if not task_ids:
            print(f"❌ Некорректный ID заявки: '{args.task_id}'", file=sys.stderr)
            sys.exit(1)

        dry_run = args.dry_run
        print("=== ⚡ Создание пользователя AD через Unified Command Bus ===")

        for task_id in task_ids:
            params = {
                "surname": getattr(args, "surname", None),
                "name": getattr(args, "name", None),
                "patronymic": getattr(args, "patronymic", None),
                "company": getattr(args, "company", None),
                "department": getattr(args, "department", None),
                "phone": getattr(args, "phone", None),
                "pc_name": getattr(args, "pc_name", None),
                "title": getattr(args, "title", None),
            }

            cmd_res = await client.submit_command(
                command_type="create_user",
                target={"task_id": task_id},
                params={k: v for k, v in params.items() if v is not None},
                mode="dry_run" if dry_run else "auto",
                source="cli",
            )

            if not cmd_res or cmd_res.get("status") == "error":
                print(
                    f"❌ Ошибка Command Bus для #{task_id}: {cmd_res.get('error', 'Неизвестная ошибка')}",
                    file=sys.stderr,
                )
                continue

            if dry_run or cmd_res.get("status") == "dry_run_success":
                print(f"🔍 [DRY-RUN] #{task_id}: {cmd_res.get('message')}")
                continue

            job_id = cmd_res.get("job_id")
            print(f"✓ Задача принята Command Bus: {job_id} (Тикет #{task_id})")

            # Ожидание исполнения в домене через воркер
            print("⏳ Исполнение в домене...", end="", flush=True)
            success = False
            for _ in range(40):
                await asyncio.sleep(1)
                st = await client.get_command_status(job_id)
                if st and st.get("status") in ("success", "failed", "cancelled"):
                    print()
                    if st.get("status") == "success":
                        print(f"🎯 УСПЕХ: Пользователь создан и заявка #{task_id} закрыта! {st.get('message', '')}")
                        success = True
                    else:
                        print(f"❌ СБОЙ: {st.get('message') or st.get('error_message')}", file=sys.stderr)
                    break
                print(".", end="", flush=True)

            if not success:
                print(f"\n⚠️  Задача {job_id} продолжает выполняться в фоне.")
    except Exception as e:
        print(f"❌ Fail-Fast: Ошибка связи с Core API ({e})", file=sys.stderr)
        print("💡 Убедитесь, что шлюз Core API запущен: docker compose ps", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def handle_ad(args: Any) -> None:
    client = CoreApiClient()
    action = args.ad_action
    identity = args.identity
    try:
        cmd_res = await client.submit_command(
            command_type=f"ad_{action}",
            target={"identity": identity},
            params={"identity": identity, "group_name": args.group_name},
            mode="auto",
            source="cli",
        )
        if not cmd_res or cmd_res.get("status") == "error":
            print(f"❌ Ошибка: {cmd_res.get('error', 'Неизвестная ошибка')}", file=sys.stderr)
            return

        job_id = cmd_res.get("job_id")
        print(f"⏳ Запрос {action} для '{identity}' передан в Command Bus (ID: {job_id})...")
        for _ in range(20):
            await asyncio.sleep(1)
            st = await client.get_command_status(job_id)
            if st and st.get("status") in ("success", "failed", "cancelled"):
                if st.get("status") == "success":
                    res_data = st.get("result", {})
                    print(f"🎯 УСПЕХ: {st.get('message', 'Операция завершена успешно')}")
                    if getattr(args, "json", False):
                        print(json.dumps(res_data, ensure_ascii=False, indent=2))
                else:
                    print(f"❌ СБОЙ: {st.get('message') or st.get('error_message')}", file=sys.stderr)
                return
        print("⚠️ Задача выполняется в фоне. Проверьте статус позже.")
    finally:
        await client.close()


async def handle(args: Any) -> None:
    if args.command == "wlan":
        await handle_wlan(args)
    elif args.command in ("create-user", "new-user"):
        await handle_create_user(args)
    elif args.command == "ad":
        await handle_ad(args)
