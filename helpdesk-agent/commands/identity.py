"""
Команды управления Active Directory и учетными записями: ad, wlan, create-user.
Исполняются непосредственно в Windows-домене, а затем финализируют заявку в Core API.
"""

import json
import sys
from typing import Any

from core_api_client import CoreApiClient
from executors.ad import (
    ActiveDirectoryExecutor,
    generate_sam_account_name,
    generate_secure_password,
)


def register_parser(subparsers: Any) -> None:
    # wlan
    p_w = subparsers.add_parser(
        "wlan",
        help="Автоматическая выдача Wi-Fi доступа в AD (WLAN-WORKNET) и закрытие заявки",
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
        "--executor", type=str, default="8664,10502", help="ID исполнителей"
    )
    p_w.add_argument(
        "--dry-run", action="store_true", help="Режим симуляции"
    )

    # create-user
    p_c = subparsers.add_parser(
        "create-user",
        aliases=["new-user"],
        help="Автоматическое создание пользователя в AD и закрытие заявки",
    )
    p_c.add_argument("task_id", type=str, help="ID заявки")
    p_c.add_argument("--surname", type=str, default=None, help="Фамилия")
    p_c.add_argument("--name", type=str, default=None, help="Имя")
    p_c.add_argument("--patronymic", type=str, default=None, help="Отчество")
    p_c.add_argument("--company", type=str, default=None, help="Организация")
    p_c.add_argument(
        "--department", type=str, default=None, help="Подразделение"
    )
    p_c.add_argument("--phone", type=str, default=None, help="Телефон")
    p_c.add_argument("--pc-name", type=str, default=None, help="Имя ПК")
    p_c.add_argument("--title", type=str, default=None, help="Должность")
    p_c.add_argument(
        "--executor", type=str, default="8664,10502", help="ID исполнителей"
    )
    p_c.add_argument(
        "--dry-run", action="store_true", help="Режим симуляции"
    )

    # ad
    p_ad = subparsers.add_parser(
        "ad", help="Управление и диагностика пользователей в Active Directory"
    )
    p_ad.add_argument(
        "ad_action",
        choices=["search", "find", "user", "unlock", "add-group", "group-add"],
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
    ad_exec = ActiveDirectoryExecutor()
    try:
        raw_ids = str(args.task_id).split(",")
        task_ids = [int(x.strip()) for x in raw_ids if x.strip().isdigit()]
        if not task_ids:
            print(f"❌ Некорректный ID заявки: '{args.task_id}'", file=sys.stderr)
            sys.exit(1)

        executor_ids = getattr(args, "executor", None) or "8664,10502"
        dry_run = args.dry_run

        print("=== ⚡ Автоматическая выдача доступа Wi-Fi (Active Directory) ===")
        for task_id in task_ids:
            task = await client.get_task_details(task_id)
            if not task:
                print(
                    f"❌ Заявка #{task_id} не найдена в IntraService.",
                    file=sys.stderr,
                )
                continue

            identity = getattr(args, "identity", None) or getattr(
                args, "login", None
            )
            if not identity:
                identity = ad_exec.extract_identity_from_task(task)

            if not identity:
                print(
                    f"❌ Заявка #{task_id}: Не удалось определить пользователя. Укажите: `--login <username>`",
                    file=sys.stderr,
                )
                continue

            print(
                f"--- Обработка #{task_id}: {task.get('Creator')} ➔ AD: '{identity}' ---"
            )

            if dry_run:
                st = await ad_exec.get_user_status_async(identity)
                print(f"[DRY-RUN] Статус пользователя в AD: {st}")
                continue

            ad_res = await ad_exec.grant_wlan_access_async(identity)
            if not ad_res.success:
                print(f"❌ СБОЙ AD: {ad_res.message}", file=sys.stderr)
                await client.apply_decision(
                    [task_id], status_id=27, executor_ids=executor_ids
                )
                continue

            print(f"✓ AD: {ad_res.message}")

            comment = (
                "Доступ к Wi-Fi предоставлен.\n"
                "Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил.\n"
                "Если возникнут проблемы с подключением, приходите в АБК-3, кабинет 112."
            )

            ok = await client.apply_decision(
                task_ids=[task_id],
                status_id=29,
                comment=comment,
                expenses=10,
                executor_ids=executor_ids,
            )
            if ok:
                print(
                    f"🎯 УСПЕХ: Заявка #{task_id} закрыта (29 Выполнена, 10 мин списано)!\n"
                )
    finally:
        await client.close()


async def handle_create_user(args: Any) -> None:
    client = CoreApiClient()
    ad_exec = ActiveDirectoryExecutor()
    try:
        raw_ids = str(args.task_id).split(",")
        task_ids = [int(x.strip()) for x in raw_ids if x.strip().isdigit()]
        if not task_ids:
            print(f"❌ Некорректный ID заявки: '{args.task_id}'", file=sys.stderr)
            sys.exit(1)

        executor_ids = getattr(args, "executor", None) or "8664,10502"
        dry_run = args.dry_run

        print("=== ⚡ Создание учетной записи в Active Directory ===")
        for task_id in task_ids:
            task = await client.get_task_details(task_id)
            if not task:
                print(
                    f"❌ Заявка #{task_id} не найдена в IntraService.",
                    file=sys.stderr,
                )
                continue

            details = ad_exec.extract_user_creation_details_from_task(task)
            surname = getattr(args, "surname", None) or details.get("surname")
            emp_name = getattr(args, "name", None) or details.get("name")
            patronymic = getattr(args, "patronymic", None) or details.get(
                "patronymic"
            )
            company = getattr(args, "company", None) or details.get("company")
            dept = getattr(args, "department", None) or details.get("department")
            phone = getattr(args, "phone", None) or details.get("phone")
            pc_name = getattr(args, "pc_name", None) or details.get("pc_name")
            title = getattr(args, "title", None) or details.get("title")

            if not surname or not emp_name:
                print(
                    f"❌ Заявка #{task_id}: Не указаны ФИО. Укажите `--surname <Фамилия> --name <Имя>`",
                    file=sys.stderr,
                )
                continue

            if dry_run:
                base_sam = generate_sam_account_name(
                    surname, emp_name, patronymic
                )
                print(
                    f"[DRY-RUN] Логин: {base_sam} | Пароль: {generate_secure_password(10)}"
                )
                continue

            res = await ad_exec.create_user_account_async(
                surname=surname,
                name=emp_name,
                patronymic=patronymic,
                company=company,
                department=dept,
                phone=phone,
                pc_name=pc_name,
                title=title,
                creator_company=details.get("creator_company"),
                creator_dept=details.get("creator_department"),
            )

            if not res.success:
                print(f"❌ СБОЙ AD: {res.error}", file=sys.stderr)
                await client.apply_decision(
                    [task_id], status_id=27, executor_ids=executor_ids
                )
                continue

            print(f"✓ AD: {res.message} (Логин: {res.sam_account_name})")

            comment = (
                "Учетная запись успешно создана.\n"
                f"Логин: {res.sam_account_name}\n"
                f"Временный пароль: {res.password}\n"
                "При первом входе в систему потребуется сменить пароль на постоянный.\n"
                "По всем вопросам подходите в АБК-3, кабинет 112."
            )

            ok = await client.apply_decision(
                task_ids=[task_id],
                status_id=29,
                comment=comment,
                expenses=10,
                executor_ids=executor_ids,
            )
            if ok:
                print(
                    f"🎯 УСПЕХ: Заявка #{task_id} закрыта (29 Выполнена, 10 мин списано)!\n"
                )
    finally:
        await client.close()


async def handle_ad(args: Any) -> None:
    ad = ActiveDirectoryExecutor()
    action = args.ad_action

    if action in ("search", "find", "user"):
        profiles = await ad.search_user_profiles_async(args.identity)
        if not profiles or not profiles[0].found:
            err = profiles[0].error if profiles else "UserNotFound"
            print(f"❌ {err}", file=sys.stderr)
            sys.exit(1)

        if getattr(args, "json", False):
            print(
                json.dumps(
                    [p.__dict__ for p in profiles],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        print(
            f"### 🏢 Найдено пользователей в Active Directory: {len(profiles)}\n"
        )
        for i, p in enumerate(profiles, start=1):
            lock_status = (
                "🔴 ЗАБЛОКИРОВАН" if p.locked_out else "🟢 Активен"
            )
            wlan_status = "🟢 Да" if p.is_wlan_member else "⚪ Нет"
            print(f"**{i}. {p.display_name}** (`{p.sam_account_name}`)")
            print(
                f"   • **Статус:** {lock_status} | **Wi-Fi:** {wlan_status} | **Кабинет:** {p.room or '—'}"
            )
            print()

    elif action in ("unlock",):
        success, msg, _ = await ad.unlock_user_account_async(args.identity)
        print(f"{'🎯 УСПЕХ' if success else '⚠️'}: {msg}")

    elif action in ("add-group", "group-add"):
        if not args.group_name:
            print("❌ Укажите имя группы AD.", file=sys.stderr)
            sys.exit(1)
        res = await ad.add_user_to_group_async(args.identity, args.group_name)
        print(f"{'🎯 УСПЕХ' if res.success else '❌'}: {res.message}")


async def handle(args: Any) -> None:
    if args.command == "wlan":
        await handle_wlan(args)
    elif args.command in ("create-user", "new-user"):
        await handle_create_user(args)
    elif args.command == "ad":
        await handle_ad(args)
